"""Lint after an edit (issue #359, A.9): the stack profile's lint command runs
on the edited file through ``workspace.execute`` and its errors come back in
the edit's own result (``services/edit_lint.py``).

The daemon's edit and its command runner run for real against ``tmp_path``;
the linter is a small script in the project, so the test needs no linter
installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest
import shell_exec  # the daemon's modules, on sys.path via conftest
import workspace_edit
import workspace_read
from config import settings
from models.company import Company
from services import developer_tools, edit_lint

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"
LINTER = (
    "import sys\n"
    "status = 0\n"
    "for path in sys.argv[1:]:\n"
    "    files = [path] if path != '.' else ['src/greet.py', 'src/other.py']\n"
    "    for name in files:\n"
    "        for n, line in enumerate(open(name), 1):\n"
    "            if line.startswith('import os'):\n"
    "                print(f'{name}:{n}:1: F401 `os` imported but unused')\n"
    "                status = 1\n"
    "sys.exit(status)\n"
)


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_lint", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon():
    return _load_daemon()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "greet.py").write_text("def greet(name):\n    return name\n")
    (root / "src" / "other.py").write_text("import os\n")
    (root / "src" / "config.json").write_text('{"debug": false}\n')
    (root / "lint.py").write_text(LINTER)
    (root / "requirements.txt").write_text("requests==2.32.0\n")
    return root


class _Requester:
    """NATS, synchronously: the daemon's edit and its real command runner."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.requests = []

    def __call__(self, subject, payload, timeout):
        self.requests.append((subject, payload, timeout))
        root, resolve = payload["workspace_root"], self.daemon._resolve_target_path
        try:
            if subject == "workspace.edit":
                return workspace_edit.edit_file(root, payload, resolve)
            if subject == "workspace.edit.finish":
                return workspace_edit.finish(root, payload.get("session"), resolve)
        except workspace_read.ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        if subject == "workspace.execute":
            return shell_exec.execute_shell_command(
                payload["command"],
                root,
                payload.get("sandbox"),
                payload.get("command_policy"),
                payload.get("timeout_seconds"),
            )
        raise AssertionError(subject)


def _call(name, **arguments):
    return {
        "id": f"c_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _box(daemon, project, lint, stack="python", **kwargs):
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True, **kwargs
    )
    box.lint_profile = lambda: {"stack": stack, "lint": lint}
    return box


PY = sys.executable


def test_the_linters_errors_come_back_in_the_same_turn_as_the_edit(project, daemon):
    box = _box(daemon, project, f"{PY} lint.py .")
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            start_line=1,
            end_line=0,
            new_text="import os\n",
        ),
        1,
    )
    assert observation["is_error"] is False
    command = f"{PY} lint.py src/greet.py"
    assert text.startswith(
        f"Lint (`{command}`) exited 1. Fix these before you deliver:\n"
    )
    assert "src/greet.py:1:1: F401 `os` imported but unused" in text
    # Narrowed to the edited file: the other file's error is not this edit's.
    assert "src/other.py" not in text
    assert "Applied to src/greet.py (checked: python)" in text
    lint = observation["lint"]
    assert lint["ran"] is True and lint["exit_code"] == 1 and lint["narrowed"] is True
    assert lint["available"] is True and lint["command"] == command
    assert len(lint["output_sha256"]) == 64 and lint["output_chars"] > 0
    subject, request, timeout = box.requester.requests[1]
    assert subject == "workspace.execute" and request["command"] == command
    assert request["timeout_seconds"] == settings.EDIT_LINT_TIMEOUT_SECONDS
    assert timeout > settings.EDIT_LINT_TIMEOUT_SECONDS
    # The fix: the same turn says the file is clean now.
    fixed, text = box.call(
        _call("workspace__edit", path="src/greet.py", search="import os\n", replace=""),
        2,
    )
    assert text.startswith(f"Lint (`{command}`): no problems.")
    assert fixed["lint"]["exit_code"] == 0
    box.close()
    assert (
        project / "src" / "greet.py"
    ).read_text() == "def greet(name):\n    return name\n"


def test_a_whole_project_linter_shows_only_what_it_says_about_the_file(project, daemon):
    # A command that does not end in " ." runs as named.
    box = _box(daemon, project, f"{PY} lint.py src/other.py")
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return name.title()",
        ),
        1,
    )
    assert (
        observation["lint"]["narrowed"] is False
        and observation["lint"]["exit_code"] == 1
    )
    assert text.startswith(
        f"Lint (`{PY} lint.py src/other.py`) exited 1; nothing it reported names src/greet.py."
    )
    assert "F401" not in text
    box.close()
    said = edit_lint._about(
        "a.py:1: x\nsrc/greet.py:2: y\n  detail\n", "src/greet.py", False
    )
    assert said == "src/greet.py:2: y\n  detail"
    assert edit_lint._about("a.py:1: x\n", "src/greet.py", False) == ""


def test_a_missing_linter_says_so_and_the_edit_stands(project, daemon):
    box = _box(daemon, project, "definitely-not-a-linter-on-path check .")
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return name * 2",
        ),
        1,
    )
    assert observation["is_error"] is False
    assert text.startswith(
        "Lint unavailable: `definitely-not-a-linter-on-path check src/greet.py`"
    )
    assert observation["lint"]["available"] is False
    assert "Applied to src/greet.py" in text
    box.close()


def test_no_lint_command_or_another_file_type_runs_nothing(project, daemon):
    box = _box(daemon, project, None)
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return name + '!'",
        ),
        1,
    )
    assert text.startswith(
        "Lint: the project names no lint command; the syntax check (python) is the only check."
    )
    assert observation["lint"] == {"ran": False, "reason": "no lint command"}
    assert [s for s, _p, _t in box.requester.requests] == ["workspace.edit"]
    box.close()
    json_box = _box(daemon, project, f"{PY} lint.py .")
    observation, text = json_box.call(
        _call(
            "workspace__edit", path="src/config.json", search="false", replace="true"
        ),
        1,
    )
    assert text.startswith("Applied to src/config.json")
    assert (
        observation["lint"]["ran"] is False and ".json" in observation["lint"]["reason"]
    )
    assert "workspace.execute" not in [s for s, _p, _t in json_box.requester.requests]
    json_box.close()


def test_the_command_policy_the_sandbox_and_the_setting_are_honoured(
    project, daemon, monkeypatch
):
    monkeypatch.setenv("FABRIQUE_COMMAND_DENY", "lint.py")
    box = _box(daemon, project, f"{PY} lint.py .")
    observation, text = box.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return name or ''",
        ),
        1,
    )
    assert text.startswith("Lint refused by the command policy:")
    assert (
        observation["lint"]["refused"] is True and observation["lint"]["ran"] is False
    )
    box.close()
    monkeypatch.delenv("FABRIQUE_COMMAND_DENY")
    boxed = _box(
        daemon,
        project,
        f"{PY} lint.py .",
        sandbox_error="the sandbox policy is unreadable",
    )
    observation, text = boxed.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return str(name)",
        ),
        1,
    )
    assert "Lint not run: the sandbox policy is unreadable" in text
    boxed.close()
    sandboxed = _box(daemon, project, f"{PY} lint.py .", sandbox={"mode": "host"})
    sandboxed.call(
        _call("workspace__edit", path="src/greet.py", search="name)", replace="who)"), 1
    )
    executed = [
        p for s, p, _t in sandboxed.requester.requests if s == "workspace.execute"
    ]
    assert executed[0]["sandbox"] == {"mode": "host"}
    sandboxed.close()
    monkeypatch.setattr(settings, "EDIT_LINT_ENABLED", False)
    off = _box(daemon, project, f"{PY} lint.py .")
    observation, text = off.call(
        _call(
            "workspace__edit",
            path="src/greet.py",
            search="return name",
            replace="return 1",
        ),
        1,
    )
    assert "lint" not in observation and text.startswith("Applied to")
    off.close()


def test_the_profile_is_read_once_with_the_companys_override(project, sqlite_session):
    company = Company(company_name="Lint Co", stack_profile={"lint": "custom-lint ."})
    plain = Company(company_name="Plain Co")
    sqlite_session.add_all([company, plain])
    sqlite_session.commit()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        bridge = {"nats": None, "loop": loop}
        engine = sqlite_session.get_bind()
        own = edit_lint.load_profile(bridge, company.id, str(project), engine)
        detected = edit_lint.load_profile(bridge, plain.id, str(project), engine)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
    assert own["stack"] == "python" and own["lint"] == "custom-lint ."
    assert detected["lint"] == "python -m ruff check ."
    assert edit_lint.narrowed("python -m ruff check .", "src/a b.py") == (
        "python -m ruff check .",
        False,
    )
    calls = []
    box = developer_tools.WorkspaceToolbox(lambda *a: {}, str(project), can_edit=True)
    box.lint_profile = lambda: calls.append(1) or {"lint": None}
    assert edit_lint.profile_of(box) == {"lint": None}
    assert edit_lint.profile_of(box) == {"lint": None} and calls == [1]


def test_the_worker_s_toolbox_lints_only_where_it_may_edit(project, monkeypatch):
    from services import mcp_tools, workspace_edits

    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        reader = developer_tools.toolbox_for(1, "Developer").workspace
        assert (
            reader.can_edit is False and getattr(reader, "lint_profile", None) is None
        )
        monkeypatch.setattr(workspace_edits, "allowed", lambda *a: True)
        editor = developer_tools.toolbox_for(1, "Developer").workspace
        assert editor.lint_profile.func is edit_lint.load_profile
        assert editor.lint_profile.args[1:3] == (1, str(project))
    finally:
        developer_tools.unbind()
