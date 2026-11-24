"""``workspace__lint``: the stack profile's lint on demand, narrowed to given paths (issue #766).

The daemon's command runner (``shell_exec``) runs for real against a project
in ``tmp_path``; the linter is a small script in the project that prints what
it was asked to read, so the test needs no linter installed. The tool is
offered only by the execution turn's toolbox (``developer_tools.toolbox_for``),
never by the read-only ones, and only paths inside the tool root reach the
command line.
"""

from __future__ import annotations

import json
import sys

import pytest
import shell_exec  # the daemon's modules, on sys.path via conftest
from config import settings
from services.tools import developer_tools, edit_lint, mcp_tools, workspace_lint

PY = sys.executable
LINTER = (
    "import os, sys\n"
    "print('ARGS ' + ' '.join(sys.argv[1:]))\n"
    "status = 0\n"
    "for arg in sys.argv[1:]:\n"
    "    if arg == '.':\n"
    "        arg = 'src'\n"
    "    files = [arg] if os.path.isfile(arg) else [\n"
    "        os.path.join(d, f) for d, _, fs in os.walk(arg) for f in sorted(fs)\n"
    "    ]\n"
    "    for name in files:\n"
    "        for n, line in enumerate(open(name), 1):\n"
    "            if line.startswith('import os'):\n"
    "                print(f'{name}:{n}:1: F401 `os` imported but unused')\n"
    "                status = 1\n"
    "sys.exit(status)\n"
)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "clean.py").write_text("def greet(name):\n    return name\n")
    (root / "src" / "dirty.py").write_text("import os\n")
    (root / "src" / "other.py").write_text("import os\n")
    (root / "src" / "notes.md").write_text("# notes\n")
    (root / "lint.py").write_text(LINTER)
    return root


class _Requester:
    """NATS, synchronously: the daemon's real command runner."""

    def __init__(self):
        self.requests: list[tuple[str, dict, float]] = []

    def __call__(self, subject, payload, timeout):
        self.requests.append((subject, payload, timeout))
        assert subject == "workspace.execute"
        return shell_exec.execute_shell_command(
            payload["command"],
            payload["workspace_root"],
            payload.get("sandbox"),
            payload.get("command_policy"),
            payload.get("timeout_seconds"),
        )

    @property
    def commands(self):
        return [p["command"] for s, p, _t in self.requests]


def _box(project, profile, **kwargs):
    box = developer_tools.WorkspaceToolbox(_Requester(), str(project), **kwargs)
    workspace_lint.offer(box, lambda: profile)
    return box


def _lint(box, **arguments):
    call = {
        "id": "c_lint",
        "type": "function",
        "function": {"name": "workspace__lint", "arguments": json.dumps(arguments)},
    }
    return box.call(call, 1)


def test_the_lint_runs_narrowed_to_the_paths(project):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py ."})
    assert box.tools["workspace__lint"]["read_only"] is True
    obs, text = _lint(box, paths=["src/dirty.py", "src/clean.py"])
    assert box.requester.commands == [f"{PY} lint.py src/dirty.py src/clean.py"]
    assert not obs["is_error"] and obs["paths"] == ["src/dirty.py", "src/clean.py"]
    [record] = obs["lint"]
    assert record["narrowed"] is True and record["ran"] is True
    assert record["exit_code"] == 1 and record["available"] is True
    assert len(record["output_sha256"]) == 64
    assert "src/dirty.py:1:1: F401" in text
    assert "other.py" not in text  # not asked: not linted
    # The timeout is the edit lint's.
    _s, payload, timeout = box.requester.requests[0]
    assert payload["timeout_seconds"] == float(settings.EDIT_LINT_TIMEOUT_SECONDS)
    assert timeout == payload["timeout_seconds"] + 15
    obs, text = _lint(box, paths=["src/clean.py"])
    assert "no problems" in text and obs["lint"][0]["exit_code"] == 0


def test_a_whole_project_command_shows_only_what_names_the_paths(project):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py src"})
    obs, text = _lint(box, paths=["src/dirty.py"])
    assert box.requester.commands == [f"{PY} lint.py src"]
    assert obs["lint"][0]["narrowed"] is False
    assert "src/dirty.py:1:1" in text and "other.py" not in text


def test_the_type_check_runs_too_when_the_profile_names_one(project):
    profile = {
        "stack": "python",
        "lint": f"{PY} lint.py .",
        "typecheck": f"{PY} lint.py .",
    }
    obs, text = _lint(_box(project, profile), paths=["src/clean.py"])
    assert [r["check"] for r in obs["lint"]] == ["lint", "type check"]
    assert "Type check (" in text


@pytest.mark.parametrize(
    "paths",
    [
        ["../outside.py"],
        ["/etc/passwd"],
        ["src/../../x.py"],
        [".env"],
        [".git/config"],
        ["src/a b.py"],
        ["src/x.py; rm -rf /"],
        ["$(whoami).py"],
        ["-rf"],
        [],
        "src/clean.py && echo hi",
        [f"src/f{n}.py" for n in range(workspace_lint.MAX_PATHS + 1)],
    ],
)
def test_a_path_outside_the_root_or_not_a_plain_path_is_refused(project, paths):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py ."})
    obs, text = _lint(box, paths=paths)
    assert obs["is_error"] and text.startswith("Error:")
    assert box.requester.requests == []  # nothing ran


def test_no_free_command_text_is_taken(project):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py ."})
    properties = box.schemas()
    [schema] = [s for s in properties if s["function"]["name"] == "workspace__lint"]
    assert set(schema["function"]["parameters"]["properties"]) == {"paths"}
    _lint(box, paths=["src/clean.py"], command="rm -rf /")
    assert box.requester.commands == [f"{PY} lint.py src/clean.py"]


def test_files_the_linter_does_not_read_are_left_out(project):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py ."})
    _obs, text = _lint(box, paths=["src/notes.md"])
    assert box.requester.requests == [] and "Nothing to lint" in text
    _obs, text = _lint(box, paths=["src/notes.md", "src"])
    assert box.requester.commands == [f"{PY} lint.py src"]
    assert "Left out" in text and "src/notes.md" in text


def test_a_rewriting_command_the_policy_and_the_sandbox_are_honoured(
    project, monkeypatch
):
    box = _box(project, {"stack": "python", "lint": f"{PY} lint.py --fix ."})
    obs, text = _lint(box, paths=["src/dirty.py"])
    assert box.requester.requests == [] and "may rewrite files" in text
    monkeypatch.setenv("FABRIQUE_COMMAND_DENY", "lint.py")
    obs, text = _lint(
        _box(project, {"stack": "python", "lint": f"{PY} lint.py ."}),
        paths=["src/dirty.py"],
    )
    assert "refused by the command policy" in text and obs["lint"][0]["refused"]
    monkeypatch.delenv("FABRIQUE_COMMAND_DENY")
    boxed = _box(
        project,
        {"stack": "python", "lint": f"{PY} lint.py ."},
        sandbox_error="the sandbox policy is unreadable",
    )
    obs, text = _lint(boxed, paths=["src/dirty.py"])
    assert obs["is_error"] and "sandbox policy is unreadable" in text
    assert boxed.requester.requests == []
    sandboxed = _box(
        project,
        {"stack": "python", "lint": f"{PY} lint.py ."},
        sandbox={"mode": "host"},
    )
    _lint(sandboxed, paths=["src/dirty.py"])
    assert sandboxed.requester.requests[0][1]["sandbox"] == {"mode": "host"}


def test_a_secret_the_linter_prints_is_redacted(project):
    (project / "src" / "conf.py").write_text("import os\n")
    linter = project / "leak.py"
    linter.write_text(
        "print(\"src/conf.py:1: password = 'hunter2hunter2hunter2'\")\nraise SystemExit(1)\n"
    )
    obs, text = _lint(
        _box(project, {"stack": "python", "lint": f"{PY} leak.py ."}),
        paths=["src/conf.py"],
    )
    assert "hunter2" not in text and len(obs["lint"][0]["output_sha256"]) == 64


def test_no_lint_command_offers_no_lint(project):
    for profile in ({"stack": "python", "lint": None}, None):
        box = _box(project, profile)
        assert "workspace__lint" not in box.tools
        obs, _ = _lint(box, paths=["src"])
        assert obs["is_error"] and obs["reason"] == "unknown tool"

    def broken():
        raise RuntimeError("no daemon")

    box = developer_tools.WorkspaceToolbox(_Requester(), str(project))
    workspace_lint.offer(box, broken)
    assert "workspace__lint" not in box.tools


def test_offered_only_where_the_step_allows_runs_and_with_the_setting(
    project, monkeypatch
):
    from services.tools import review_tools, workspace_edits

    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: None)
    )
    monkeypatch.setattr(review_tools, "delivery_root", lambda *a, **k: str(project))
    loads = []
    profile = {"stack": "python", "lint": f"{PY} lint.py ."}
    monkeypatch.setattr(
        edit_lint, "load_profile", lambda *a: loads.append(a[1:3]) or profile
    )
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        # The execution turn's toolbox, with or without edits.
        reader = developer_tools.toolbox_for(1, "Developer").workspace
        assert "workspace__lint" in reader.tools
        assert getattr(reader, "lint_profile", None) is None  # no lint after edits
        assert loads == [(1, str(project))]
        monkeypatch.setattr(workspace_edits, "allowed", lambda *a: True)
        editor = developer_tools.toolbox_for(1, "Developer").workspace
        assert "workspace__lint" in editor.tools
        # The lint after an edit reuses the profile read for the offer.
        assert edit_lint.profile_of(editor) is profile and len(loads) == 2
        # Lint is family X: a step without it, or a role that drops it, has none.
        from services.contracts import tool_steps

        for step in (tool_steps.ARCHITECT_BRIEF, tool_steps.QA_REVIEW):
            brief = developer_tools.toolbox_for(1, "Developer", step=step).workspace
            assert "workspace__lint" not in brief.tools, step
        assert (
            "workspace__lint"
            in developer_tools.toolbox_for(1, "TechWriter").workspace.tools
        )
        monkeypatch.setattr(tool_steps, "ROLE_DENY", {"TechWriter": frozenset({"X"})})
        denied = developer_tools.toolbox_for(1, "TechWriter").workspace
        assert "workspace__lint" not in denied.tools
        # The read-only toolboxes: the Architect's brief and QA's review.
        for box in (
            developer_tools.read_tools(1),
            review_tools.read_only_toolbox(1, 2, engine=None, rounds=3),
        ):
            assert "workspace__lint" not in box.tools
            obs, _ = _lint(box, paths=["src/clean.py"])
            assert obs["is_error"] and obs["reason"] == "unknown tool"
        monkeypatch.setattr(settings, "EDIT_LINT_ENABLED", False)
        off = developer_tools.toolbox_for(1, "Developer").workspace
        assert "workspace__lint" not in off.tools
    finally:
        developer_tools.unbind()
