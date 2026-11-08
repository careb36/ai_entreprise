"""The Developer's workspace tools (``services/developer_tools.py``) and the
daemon's read handlers (``vscode-daemon/workspace_read.py``).

The daemon's functions run for real against ``tmp_path``; the model is a fake
proxy that answers with tool calls and then with a deliverable; NATS is a
synchronous fake that hands each request to the daemon's function.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import workspace_read  # the daemon's module, on sys.path via conftest
from config import settings
from models.agent_run import AgentRun
from models.company import Company
from services import context_read, developer_tools, mcp_config, mcp_tools, risk
from services import task_worker as tw
from services.verification import check_risk_tier, verify_agent_run

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_tools", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "greet.py").write_text(
        "def greet(name):\n    return f'Hello, {name}!'\n"
    )
    (root / "src" / "count.py").write_text("COUNT = 0\n")
    (root / "tests" / "test_greet.py").write_text(
        "from src.greet import greet\n\n\ndef test_greet():\n    assert greet('a') == 'Hello, a!'\n"
    )
    (root / "README.md").write_text("# Proj\n")
    (root / "node_modules" / "x" / "index.js").write_text("module.exports = 1\n")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    return root


# --- the daemon's read handlers --------------------------------------------


def test_the_daemon_lists_the_tree_without_dependencies_or_vcs_data(project):
    daemon = _load_daemon()
    listing = workspace_read.list_tree(
        str(project), None, 2, daemon._resolve_target_path
    )
    assert listing["path"] == "." and listing["truncated"] is False
    assert listing["entries"] == [
        "src/",
        "tests/",
        "README.md",
        "logo.png",
        "src/count.py",
        "src/greet.py",
        "tests/test_greet.py",
    ]
    one_level = workspace_read.list_tree(
        str(project), "src", 1, daemon._resolve_target_path
    )
    assert one_level == {
        "path": "src",
        "entries": ["src/count.py", "src/greet.py"],
        "truncated": False,
    }
    with pytest.raises(workspace_read.ReadError) as outside:
        workspace_read.list_tree(str(project), "../", 1, daemon._resolve_target_path)
    assert outside.value.status == 400
    with pytest.raises(workspace_read.ReadError) as missing:
        workspace_read.list_tree(
            str(project / "nope"), None, 1, daemon._resolve_target_path
        )
    assert missing.value.status == 404


def test_the_daemon_reads_a_file_numbered_with_its_hash_and_tells_a_binary_apart(
    project,
):
    daemon = _load_daemon()
    read = workspace_read.read_file(
        str(project), "src/greet.py", None, daemon._resolve_target_path
    )
    raw = (project / "src" / "greet.py").read_bytes()
    assert read["sha256"] == hashlib.sha256(raw).hexdigest() and read["bytes"] == len(
        raw
    )
    assert read["text"].splitlines() == [
        "    1| def greet(name):",
        "    2|     return f'Hello, {name}!'",
    ]
    assert read["lines"] == 2 and read["truncated"] is False and read["binary"] is False
    second = workspace_read.read_file(
        str(project), "src/greet.py", 2, daemon._resolve_target_path
    )
    assert (
        second["text"] == "    2|     return f'Hello, {name}!'"
        and second["first_line"] == 2
    )
    binary = workspace_read.read_file(
        str(project), "logo.png", None, daemon._resolve_target_path
    )
    assert binary["binary"] is True and binary["text"] == "" and binary["sha256"]
    with pytest.raises(workspace_read.ReadError) as missing:
        workspace_read.read_file(
            str(project), "src/none.py", None, daemon._resolve_target_path
        )
    assert missing.value.status == 404
    with pytest.raises(workspace_read.ReadError):
        workspace_read.read_file(
            str(project), "../etc/passwd", None, daemon._resolve_target_path
        )


def test_a_long_file_is_cut_and_the_reply_says_how_long_it_is(project):
    daemon = _load_daemon()
    (project / "src" / "big.py").write_text("".join(f"x = {i}\n" for i in range(1000)))
    read = workspace_read.read_file(
        str(project), "src/big.py", None, daemon._resolve_target_path
    )
    assert read["truncated"] is True and read["lines"] == 1000
    assert len(read["text"].splitlines()) == workspace_read.READ_MAX_LINES


def test_the_daemon_searches_the_project_and_refuses_a_bad_pattern(project):
    daemon = _load_daemon()
    found = workspace_read.search(
        str(project), r"def greet|COUNT", None, daemon._resolve_target_path
    )
    assert found["matches"] == [
        "src/count.py:1: COUNT = 0",
        "src/greet.py:1: def greet(name):",
    ]
    # Dependencies, VCS data and the binary are never scanned: four text files.
    assert found["files_scanned"] == 4 and found["truncated"] is False
    narrowed = workspace_read.search(
        str(project), "greet", "tests", daemon._resolve_target_path
    )
    assert [m.split(":")[0] for m in narrowed["matches"]] == ["tests/test_greet.py"] * 3
    with pytest.raises(workspace_read.ReadError) as bad:
        workspace_read.search(str(project), "(", None, daemon._resolve_target_path)
    assert bad.value.status == 400 and "pattern" in bad.value.message
    with pytest.raises(workspace_read.ReadError):
        workspace_read.search(str(project), "x", "../", daemon._resolve_target_path)


def test_the_daemon_handlers_answer_over_nats_in_the_reply_shape(project):
    import asyncio

    daemon = _load_daemon()
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for handler, body in (
            (workspace_read.handle_list, {"workspace_root": str(project), "depth": 1}),
            (
                workspace_read.handle_read,
                {"workspace_root": str(project), "path": "README.md"},
            ),
            (
                workspace_read.handle_search,
                {"workspace_root": str(project), "pattern": "Proj"},
            ),
            (
                workspace_read.handle_read,
                {"workspace_root": str(project), "path": "nope"},
            ),
            (workspace_read.handle_read, {"path": "README.md"}),
        ):
            await handler(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["entries"] == ["src/", "tests/", "README.md", "logo.png"]
    assert replies[1]["text"] == "    1| # Proj"
    assert replies[2]["matches"] == ["README.md:1: # Proj"]
    assert replies[3]["error"]["status"] == 404
    assert replies[4]["error"]["status"] == 400
    assert [s for s, _h in workspace_read.HANDLERS] == [
        "workspace.list",
        "workspace.read",
        "workspace.search",
    ]


# --- the toolbox ------------------------------------------------------------


class _Requester:
    """NATS, synchronously: each subject handed to the daemon's function."""

    def __init__(self, daemon, commands=None):
        self.daemon = daemon
        self.calls = []
        self.commands = commands or {}

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        try:
            if subject == "workspace.list":
                return workspace_read.list_tree(
                    root, payload.get("path"), payload.get("depth"), resolve
                )
            if subject == "workspace.read":
                return workspace_read.read_file(
                    root, payload.get("path"), payload.get("start_line"), resolve
                )
            if subject == "workspace.search":
                return workspace_read.search(
                    root, payload.get("pattern"), payload.get("path"), resolve
                )
        except workspace_read.ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        if subject == "workspace.execute":
            return self.commands.get(
                payload["command"],
                {"exit_code": 1, "stdout": "no such command\n", "stderr": ""},
            )
        raise AssertionError(subject)


def _call(name, **arguments):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_the_toolbox_offers_the_read_tools_and_the_test_run_only_with_a_command(
    project,
):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    assert [s["function"]["name"] for s in box.schemas()] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
    ]
    with_tests = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), test_command="python -m pytest -q"
    )
    assert [s["function"]["name"] for s in with_tests.schemas()][
        -1
    ] == "workspace__run_tests"
    assert all(t["read_only"] for t in with_tests.tools.values())
    assert with_tests.max_rounds == settings.DEVELOPER_TOOL_ROUNDS


def test_a_read_is_recorded_with_the_file_s_hash_and_the_model_sees_the_numbered_text(
    project,
):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    observation, text = box.call(_call("workspace__read", path="src/greet.py"), 1)
    raw = (project / "src" / "greet.py").read_bytes()
    assert observation["server"] == "workspace" and observation["tool"] == "read"
    assert (
        observation["path"] == "src/greet.py"
        and observation["sha256"] == hashlib.sha256(raw).hexdigest()
    )
    assert (
        observation["bytes"] == len(raw)
        and observation["read_only"] is True
        and observation["is_error"] is False
    )
    assert observation["result_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert text.startswith("    1| def greet(name):")
    listing, text = box.call(_call("workspace__list"), 1)
    assert listing["tool"] == "list" and "src/greet.py" in text.splitlines()
    search, text = box.call(_call("workspace__search", pattern="greet", path="src"), 2)
    assert search["files_scanned"] == 2 and text == "src/greet.py:1: def greet(name):"


def test_errors_are_results_the_model_sees_and_the_record_keeps(project):
    daemon = _load_daemon()
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), result_max_chars=80
    )
    missing, text = box.call(_call("workspace__read", path="src/none.py"), 1)
    assert missing["is_error"] is True and text.startswith("Error: No such file")
    unknown, text = box.call(_call("workspace__delete", path="x"), 1)
    assert unknown["reason"] == "unknown tool" and text == mcp_tools.NO_TOOL_NOTE
    malformed, text = box.call(
        {"id": "c", "function": {"name": "workspace__read", "arguments": "{nope"}}, 1
    )
    assert malformed["reason"].startswith("malformed arguments") and text.startswith(
        "Error: malformed"
    )
    (project / "src" / "big.py").write_text("".join(f"x = {i}\n" for i in range(50)))
    cut, text = box.call(_call("workspace__read", path="src/big.py"), 1)
    assert (
        cut["truncated"] is True
        and text.endswith("[result cut here]")
        and cut["result_chars"] > 80
    )


def test_run_tests_runs_the_project_s_command_or_a_narrower_one_on_the_same_runner(
    project,
):
    daemon = _load_daemon()
    requester = _Requester(
        daemon,
        {
            "python -m pytest -q": {
                "exit_code": 1,
                "stdout": "1 failed\n",
                "stderr": "",
            },
            "python -m pytest tests/test_greet.py": {
                "exit_code": 0,
                "stdout": "1 passed\n",
                "stderr": "",
            },
        },
    )
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), test_command="python -m pytest -q"
    )
    whole, text = box.call(_call("workspace__run_tests"), 1)
    assert (
        whole["command"] == "python -m pytest -q"
        and whole["exit_code"] == 1
        and text.startswith("exit status 1")
    )
    narrowed, text = box.call(
        _call("workspace__run_tests", command="python -m pytest tests/test_greet.py"), 2
    )
    assert (
        narrowed["command"] == "python -m pytest tests/test_greet.py"
        and "1 passed" in text
    )
    # Another runner, or shell syntax, falls back to the project's command.
    for wanted in ("rm -rf /", "python -m pytest; cat /etc/passwd", "npm test"):
        again, _text = box.call(_call("workspace__run_tests", command=wanted), 3)
        assert again["command"] == "python -m pytest -q", wanted
    assert [p["command"] for s, p in requester.calls if s == "workspace.execute"][
        :2
    ] == [
        "python -m pytest -q",
        "python -m pytest tests/test_greet.py",
    ]
    timed = developer_tools.WorkspaceToolbox(
        _Requester(
            daemon,
            {"python -m pytest -q": {"timed_out": True, "stdout": "...", "stderr": ""}},
        ),
        str(project),
        test_command="python -m pytest -q",
    )
    out, text = timed.call(_call("workspace__run_tests"), 1)
    assert out["timed_out"] is True and text.startswith(
        "The tests ran past the time limit"
    )


class _Model:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def chat(self, messages, model, role, bu_id=None, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _answer_with_calls(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                }
            }
        ]
    }


def _answer(text):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "served_model": "m",
    }


class _FakeMcp:
    """An MCP toolbox in shape only."""

    def __init__(self):
        self.unavailable = {"gone": "unreachable"}
        self.calls = []
        self.closed = False

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "tracker__search",
                    "description": "x",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def call(self, tool_call, round_no):
        self.calls.append(tool_call)
        return {
            "server": "tracker",
            "tool": "search",
            "is_error": False,
            "read_only": True,
        }, "found"

    def close(self):
        self.closed = True


def test_the_combined_toolbox_dispatches_by_name_and_caps_the_rounds(
    project, monkeypatch
):
    monkeypatch.setattr(settings, "DEVELOPER_TOOL_ROUNDS", 2)
    daemon = _load_daemon()
    mcp = _FakeMcp()
    box = developer_tools.CombinedToolbox(
        developer_tools.WorkspaceToolbox(_Requester(daemon), str(project)), mcp
    )
    assert [s["function"]["name"] for s in box.schemas()] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
        "tracker__search",
    ]
    assert box.unavailable == {"gone": "unreachable"} and box.max_rounds == 2
    model = _Model(
        [
            _answer_with_calls(
                _call("workspace__read", path="README.md"),
                _call("tracker__search", q="x"),
            )
        ]
    )
    _result, observations = mcp_tools.chat_with_tools(
        model, [{"role": "user", "content": "go"}], "m", "Developer", box
    )
    # Two rounds of calls, then the request without tools.
    assert [o["server"] for o in observations] == ["workspace", "tracker"] * 2
    assert [r["tools"] is not None for r in model.requests] == [True, True, False]
    assert len(mcp.calls) == 2
    # A name that is not a built-in tool goes to the MCP side, whatever its prefix.
    box.call(_call("workspace__other", x=1), 3)
    assert len(mcp.calls) == 3
    box.close()
    assert mcp.closed
    # The built-in namespace is reserved: no MCP server may take the name.
    with pytest.raises(mcp_config.McpConfigError, match="reserved"):
        mcp_config.normalize_server("workspace", {"url": "https://x.example/mcp"})


def test_the_tools_go_to_the_roles_that_deliver_files_when_the_project_is_known(
    project, monkeypatch
):
    daemon = _load_daemon()
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    developer_tools.unbind()
    assert developer_tools.toolbox_for(1, "Developer") is None  # not bound: no daemon
    developer_tools.bind(
        object(), object(), lambda company_id: str(project) if company_id == 1 else None
    )
    try:
        monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
        assert developer_tools.toolbox_for(1, "PO") is None
        assert developer_tools.toolbox_for(2, "Developer") is None  # no project path
        box = developer_tools.toolbox_for(1, "Developer")
        assert isinstance(box, developer_tools.CombinedToolbox) and box.mcp is None
        assert [s["function"]["name"] for s in box.schemas()] == [
            "workspace__list",
            "workspace__read",
            "workspace__search",
        ]
        for role in ("QA", "dev", "Developer"):
            assert developer_tools.offered_to(role)
        monkeypatch.setattr(settings, "DEVELOPER_TOOLS_ENABLED", False)
        assert (
            developer_tools.toolbox_for(1, "Developer") is None
            and developer_tools.repository_section(1, "Developer") == ""
        )
    finally:
        developer_tools.unbind()


def test_the_prompt_carries_the_top_of_the_tree_as_the_repository_map(
    project, monkeypatch
):
    daemon = _load_daemon()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
        section = developer_tools.repository_section(1, "Developer")
        assert (
            section.startswith("\n\n## Repository\n")
            and "src/greet.py" in section
            and "node_modules" not in section
        )
        assert developer_tools.repository_section(1, "ScrumMaster") == ""
        assert developer_tools.repository_section(None, "Developer") == ""
        monkeypatch.setattr(developer_tools, "MAP_MAX_CHARS", 20)
        assert developer_tools.repository_section(1, "Developer").endswith(
            "[listing cut here]"
        )

        def boom(*a, **k):
            raise RuntimeError("daemon down")

        monkeypatch.setattr(developer_tools, "_request_from_thread", boom)
        assert developer_tools.repository_section(1, "Developer") == ""
    finally:
        developer_tools.unbind()


# --- the evidence -------------------------------------------------------------


def _run():
    return AgentRun(
        company_id=1,
        external_ref="task-1",
        producer="p",
        artifact_digest="d",
        artifact_size=1,
        claims={"files": ["src/a.py"]},
    )


def test_the_verifier_says_which_modified_files_were_read_first_and_the_tier_follows():
    calls = [
        {
            "server": "workspace",
            "tool": "read",
            "is_error": False,
            "read_only": True,
            "path": "src/a.py",
            "sha256": "aa",
        },
        {
            "server": "workspace",
            "tool": "read",
            "is_error": True,
            "read_only": True,
            "path": "src/none.py",
            "sha256": None,
        },
        {"server": "workspace", "tool": "list", "is_error": False, "read_only": True},
    ]
    writes = [
        {"path": "src/a.py", "status": "written", "change": "modified"},
        {"path": "src/b.py", "status": "written", "change": "modified"},
        {"path": "src/new.py", "status": "written", "change": "added"},
    ]
    assert context_read.files_read(calls) == {"src/a.py": "aa"}
    check = context_read.check_context_read(
        {"tool_calls": calls, "workspace_writes": writes}
    )
    assert check["name"] == "context_read" and check["outcome"] == "pass"
    assert (check["read"], check["modified_read"], check["modified_unread"]) == (
        ["src/a.py"],
        ["src/a.py"],
        ["src/b.py"],
    )
    assert check["observation"] == "modified without reading first: src/b.py"
    tier = check_risk_tier(_run(), {"tool_calls": calls, "workspace_writes": writes})
    assert tier["tier"] == "medium" and tier["reasons"] == [
        "modified without reading first: src/b.py"
    ]
    # Every modified file read: low. New files only: a record, no reason.
    all_read = calls + [
        {
            "server": "workspace",
            "tool": "read",
            "is_error": False,
            "read_only": True,
            "path": "src/b.py",
            "sha256": "bb",
        }
    ]
    assert (
        check_risk_tier(_run(), {"tool_calls": all_read, "workspace_writes": writes})[
            "tier"
        ]
        == "low"
    )
    assert (
        context_read.check_context_read(
            {"tool_calls": all_read, "workspace_writes": writes}
        )["observation"]
        == "every modified file was read first (2)"
    )
    only_new = context_read.check_context_read(
        {"tool_calls": calls, "workspace_writes": writes[2:]}
    )
    assert only_new["observation"] == "read 1 file(s); no existing file was changed"
    # No workspace tools in use: a producer without them is classified by its paths alone.
    assert (
        context_read.check_context_read({"tool_calls": [], "workspace_writes": []})
        is None
    )
    no_tools = check_risk_tier(_run(), {"tool_calls": [], "workspace_writes": writes})
    assert no_tools["tier"] == "low"
    assert (
        risk.raise_for_unread_changes({"tier": "high", "reasons": ["x"]}, check)["tier"]
        == "high"
    )


def test_the_worker_offers_the_tools_reads_through_the_daemon_and_keeps_the_reads_as_evidence(
    project, monkeypatch, sqlite_session
):
    """``_execute_task`` carries the repository map, the model reads a file
    before delivering, and the run's verification shows the read beside the
    modified file."""
    daemon = _load_daemon()
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
    model = _Model(
        [
            _answer_with_calls(_call("workspace__read", path="src/greet.py")),
            _answer(
                "```python path=src/greet.py\ndef greet(name):\n    return 'Hi ' + name\n```\nDone."
            ),
        ]
    )
    monkeypatch.setattr(tw, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    try:
        content = tw._execute_task(
            {"id": 7, "title": "Greet", "description": "x"}, agent, "S1", company_id=1
        )
    finally:
        developer_tools.unbind()
    assert "Done." in content
    prompt = model.requests[0]["messages"][1]["content"]
    assert "## Repository" in prompt and "src/greet.py" in prompt
    assert [s["function"]["name"] for s in model.requests[0]["tools"]] == [
        "workspace__list",
        "workspace__read",
        "workspace__search",
    ]
    read = agent["tool_calls"][0]
    assert (
        read["tool"] == "read"
        and read["path"] == "src/greet.py"
        and len(read["sha256"]) == 64
    )

    company = Company(company_name="Tools Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref="task-7",
        producer="p",
        artifact_digest="d",
        artifact_size=1,
        claims={"files": ["src/greet.py"]},
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    verification = verify_agent_run(
        sqlite_session,
        run,
        {
            "tool_calls": agent["tool_calls"],
            "workspace_writes": [
                {
                    "path": "src/greet.py",
                    "status": "written",
                    "change": "modified",
                    "sha256": "x",
                }
            ],
        },
    )
    names = [c["name"] for c in verification.checks]
    assert "tools_used" in names and "context_read" in names
    context = next(c for c in verification.checks if c["name"] == "context_read")
    assert (
        context["modified_read"] == ["src/greet.py"]
        and context["modified_unread"] == []
    )
    assert next(c for c in verification.checks if c["name"] == "tools_used")[
        "observation"
    ].startswith("1 MCP tool call(s): workspace.read")


def test_the_history_check_refuses_a_read_recorded_without_a_hash(sqlite_session):
    from history_check import _run_facts, check_task
    from models.agent_run import Verification

    task = {"id": 1, "status": "in_progress", "comments": [], "depends_on": []}
    fine = {"version": 1, "status": "verified", "reads_without_hash": []}
    assert check_task(task, [fine], []) == []
    bad = {"version": 2, "status": "verified", "reads_without_hash": ["src/a.py"]}
    assert check_task(task, [bad], []) == [
        "read_without_hash: run v2 records a read of src/a.py with no sha256"
    ]
    verification = Verification(
        company_id=1,
        agent_run_id=1,
        verdict="verified",
        checks=[],
        evidence={
            "tool_calls": [
                {
                    "server": "workspace",
                    "tool": "read",
                    "path": "src/a.py",
                    "sha256": "aa",
                },
                {"server": "workspace", "tool": "read", "path": "src/b.py"},
                {
                    "server": "workspace",
                    "tool": "read",
                    "path": "src/c.py",
                    "is_error": True,
                },
                {"server": "tracker", "tool": "read", "path": "x"},
            ]
        },
    )
    assert _run_facts(verification)["reads_without_hash"] == ["src/b.py"]
    assert _run_facts(None)["reads_without_hash"] == []


# --- the review's findings ----------------------------------------------------


def test_a_narrower_test_command_must_be_the_same_runner_exactly(project):
    daemon = _load_daemon()
    requester = _Requester(
        daemon,
        {"python -m pytest -q": {"exit_code": 0, "stdout": "ok\n", "stderr": ""}},
    )
    box = developer_tools.WorkspaceToolbox(
        requester, str(project), test_command="python -m pytest -q"
    )
    for wanted in (
        "python -m pytest_custom",
        "python -m pytestx tests",
        "python -m pytest\ttests",
    ):
        observation, _text = box.call(_call("workspace__run_tests", command=wanted), 1)
        assert observation["command"] == "python -m pytest -q", wanted
    exact, _text = box.call(
        _call("workspace__run_tests", command="python -m pytest"), 1
    )
    assert exact["command"] == "python -m pytest"
    assert developer_tools._runs_with("npx vitest run x", "npx vitest")
    assert not developer_tools._runs_with("npx vitest-custom run", "npx vitest")


def test_files_that_may_hold_secrets_are_never_read_nor_searched(project):
    daemon = _load_daemon()
    (project / ".env").write_text(
        "OPENAI_API_KEY=sk-live-abcdefghijklmnopqrstuvwxyz123456\n"
    )
    (project / "deploy.pem").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
    )
    (project / ".ssh").mkdir()
    (project / ".ssh" / "config").write_text("Host x\n")
    (project / "config").mkdir()
    (project / "config" / "secrets.yaml").write_text("token: abc\n")
    for name in (".env", "deploy.pem", ".ssh/config", "config/secrets.yaml"):
        with pytest.raises(workspace_read.ReadError) as refused:
            workspace_read.read_file(
                str(project), name, None, daemon._resolve_target_path
            )
        assert refused.value.status == 403, name
    found = workspace_read.search(
        str(project), "PRIVATE KEY|OPENAI|token", None, daemon._resolve_target_path
    )
    assert found["matches"] == []
    # An explicit search on the file itself is refused the same way: nothing is opened.
    direct = workspace_read.search(
        str(project), "KEY", "deploy.pem", daemon._resolve_target_path
    )
    assert direct["matches"] == [] and direct["files_scanned"] == 0


def test_a_secret_looking_line_in_a_readable_file_is_redacted_for_the_model(project):
    daemon = _load_daemon()
    (project / "src" / "settings.py").write_text(
        "DEBUG = True\nAWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\nNAME = 'x'\n"
    )
    box = developer_tools.WorkspaceToolbox(_Requester(daemon), str(project))
    observation, text = box.call(_call("workspace__read", path="src/settings.py"), 1)
    assert (
        "AKIAABCDEFGHIJKLMNOP" not in text
        and "[line redacted: it looks like a secret]" in text
    )
    assert "DEBUG = True" in text and observation["sha256"]
    _search, text = box.call(_call("workspace__search", pattern="AKIA"), 1)
    assert "AKIAABCDEFGHIJKLMNOP" not in text and "redacted" in text
    assert context_read.redact_secrets("a.py", "x = 1\ny = 2") == "x = 1\ny = 2"


def test_a_symlink_that_leads_out_of_the_project_is_never_opened(project, tmp_path):
    daemon = _load_daemon()
    outside = tmp_path / "outside.txt"
    outside.write_text("the secret word is pelican\n")
    (project / "src" / "link.txt").symlink_to(outside)
    found = workspace_read.search(
        str(project), "pelican", None, daemon._resolve_target_path
    )
    assert found["matches"] == []
    with pytest.raises(workspace_read.ReadError) as refused:
        workspace_read.read_file(
            str(project), "src/link.txt", None, daemon._resolve_target_path
        )
    assert refused.value.status == 400
    # A symlink inside the project is fine.
    (project / "src" / "alias.py").symlink_to(project / "src" / "greet.py")
    read = workspace_read.read_file(
        str(project), "src/alias.py", None, daemon._resolve_target_path
    )
    assert read["text"].startswith("    1| def greet")


def test_a_large_file_is_hashed_whole_but_only_its_head_is_read(project, monkeypatch):
    daemon = _load_daemon()
    monkeypatch.setattr(workspace_read, "READ_SCAN_BYTES", 64)
    body = "".join(f"line {i}\n" for i in range(100))
    (project / "src" / "big.txt").write_text(body)
    read = workspace_read.read_file(
        str(project), "src/big.txt", None, daemon._resolve_target_path
    )
    assert read["sha256"] == hashlib.sha256(body.encode()).hexdigest() and read[
        "bytes"
    ] == len(body)
    assert read["truncated"] is True and read["lines_indexed_to_byte"] == 64
    assert len(read["text"].splitlines()) <= 10
    beyond = workspace_read.read_file(
        str(project), "src/big.txt", 90, daemon._resolve_target_path
    )
    assert beyond["text"] == "" and beyond["truncated"] is True


def test_a_search_that_backtracks_forever_is_stopped_at_the_deadline(project):
    daemon = _load_daemon()
    (project / "src" / "evil.txt").write_text("a" * 40 + "b\n")
    with pytest.raises(workspace_read.ReadError) as stopped:
        workspace_read.search_bounded(
            str(project), r"(a+)+$", None, daemon._resolve_target_path, timeout=1.5
        )
    assert stopped.value.status == 408 and "stopped" in stopped.value.message
    # A sane search through the same path answers.
    found = workspace_read.search_bounded(
        str(project), "def greet", None, daemon._resolve_target_path
    )
    assert found["matches"] == ["src/greet.py:1: def greet(name):"]
    with pytest.raises(workspace_read.ReadError) as bad:
        workspace_read.search_bounded(
            str(project), "(", None, daemon._resolve_target_path
        )
    assert bad.value.status == 400


def test_a_search_over_more_files_than_the_bound_still_scans_the_bound(
    project, monkeypatch
):
    daemon = _load_daemon()
    # Three files within the bound: README.md, the binary logo (skipped) and
    # src/count.py; the walk found more, so the answer says so.
    monkeypatch.setattr(workspace_read, "SEARCH_MAX_FILES", 3)
    found = workspace_read.search(
        str(project), "greet|COUNT|Proj", None, daemon._resolve_target_path
    )
    assert found["truncated"] is True and found["files_scanned"] == 2
    assert found["matches"] == ["README.md:1: # Proj", "src/count.py:1: COUNT = 0"]


def test_the_daemon_reports_a_test_run_with_no_sprint_to_nobody(project):
    import asyncio

    daemon = _load_daemon()
    published = []

    class _NC:
        async def publish(self, subject, payload):
            published.append(subject)

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.subject = "workspace.execute"
            self.reply = "inbox"

    asyncio.run(
        daemon.handle_msg(
            _NC(), _Msg({"command": "echo hi", "workspace_root": str(project)})
        )
    )
    assert published == ["inbox"]
    asyncio.run(
        daemon.handle_msg(
            _NC(),
            _Msg(
                {"command": "echo hi", "workspace_root": str(project), "sprint_id": 4}
            ),
        )
    )
    assert published == ["inbox", "glassroom.4", "inbox"]


def test_the_tools_look_at_the_task_s_worktree_once_it_delivered_there(
    project, sqlite_session, monkeypatch
):
    from models.agent_run import Verification

    company = Company(company_name="Worktree Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref="task-9",
        producer="le-fabrique-task-worker",
        artifact_digest="d",
        artifact_size=1,
        claims={},
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        engine = sqlite_session.get_bind()
        assert developer_tools.tool_root(company.id, 9, engine=engine) == str(project)
        sqlite_session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                checks=[],
                evidence={
                    "git": {"branch": "fabrique/task-9"},
                    "workspace_root": "/repo/.git/fabrique-worktrees/task-9",
                },
            )
        )
        sqlite_session.commit()
        assert (
            developer_tools.tool_root(company.id, 9, engine=engine)
            == "/repo/.git/fabrique-worktrees/task-9"
        )
        # Without the Git workflow the evidence names the project itself.
        sqlite_session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                checks=[],
                evidence={"workspace_root": "/elsewhere"},
            )
        )
        sqlite_session.commit()
        assert developer_tools.tool_root(company.id, 9, engine=engine) == str(project)
        assert developer_tools.tool_root(company.id, None, engine=engine) == str(
            project
        )
    finally:
        developer_tools.unbind()
