"""A narrower test command runs narrowed (issue #766).

An audit of 108 runs found every one of 20 ``workspace__run_tests`` calls
asking for a narrower command ran the whole backend suite: the folder's
command starts with ``PYTHONPATH=.:app``, which was compared as the runner.
``services/tools/test_commands.py`` sets environment assignments aside (the
folder's own come back, never the requested ones), drops a leading ``cd
<folder> &&`` for the test folder and a trailing ``| tail`` / ``; echo $?``,
and says each change in the reply. A shell operator anywhere else is still
refused. Check scripts accept a wrapper, ``../scripts/`` from a folder and the
same trailing noise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from developer_tools_helpers import _call
from services.tools import developer_tools, test_commands
from test_check_scripts import SUITE, _box, _Daemon, repo  # noqa: F401

BASE = "PYTHONPATH=.:app poetry run python -m pytest -q"
PLANS = {
    "backend": {"test": BASE, "install": None},
    "frontend": {"test": "npm test --silent", "install": None},
}
OK = {"exit_code": 0, "stdout": "1 passed\n", "stderr": ""}


class _Recorder:
    def __init__(self):
        self.commands = []

    def __call__(self, subject, payload, timeout):
        assert subject == "workspace.execute", subject
        self.commands.append(payload["command"])
        return OK


def _run(command, folder="backend"):
    recorder = _Recorder()
    box = developer_tools.WorkspaceToolbox(recorder, "/p", folder_tests=PLANS)
    arguments = {"command": command}
    if folder is not None:
        arguments["folder"] = folder
    observation, text = box.call(_call("workspace__run_tests", **arguments), 1)
    assert recorder.commands == [
        f"cd {observation['folder']} && {observation['command']}"
    ]
    return observation, text


def test_the_exact_env_prefixed_base_with_a_narrower_path_runs_narrowed():
    observation, text = _run(f"{BASE} tests/test_x.py")
    assert observation["command"] == f"{BASE} tests/test_x.py"
    assert text == "exit status 0\n1 passed\n"  # As asked: no note.


def test_requested_env_assignments_are_not_applied_the_folder_s_are():
    observation, text = _run("PATH=/tmp/evil PYTHONPATH=x pytest tests/test_x.py -q")
    assert (
        observation["command"]
        == "PYTHONPATH=.:app poetry run python -m pytest tests/test_x.py -q"
    )
    assert "PATH=/tmp/evil" not in observation["command"]
    assert text.startswith(
        "[`PATH=/tmp/evil PYTHONPATH=x pytest tests/test_x.py -q` ran as "
        f"`{observation['command']}`: "
    )
    assert "`PATH=/tmp/evil PYTHONPATH=x` asked for were not applied" in text
    assert "the folder's own `PYTHONPATH=.:app` was applied" in text
    assert "under the folder's `poetry run`" in text
    assert "`pytest` runs as the folder's `python -m pytest`" in text


def test_cd_into_the_test_folder_narrows_and_is_said():
    observation, text = _run("cd backend && pytest tests/x.py")
    assert observation["command"] == f"{BASE[:-3]} tests/x.py"
    assert "`cd backend &&` was dropped" in text
    # With no folder argument the cd names it.
    observation, _text = _run("cd backend && pytest tests/x.py", folder=None)
    assert observation["folder"] == "backend"
    assert observation["command"] == f"{BASE[:-3]} tests/x.py"


def test_cd_into_another_folder_is_refused_with_why():
    observation, text = _run("cd frontend && pytest tests/x.py")
    assert observation["command"] == BASE
    assert "`cd frontend` is not the folder the tests run in (`backend`)" in text


def test_trailing_tail_and_echo_are_dropped_and_said():
    observation, text = _run(
        "poetry run python -m pytest tests/x.py 2>&1 | tail -n 40; echo EXIT=$?"
    )
    assert observation["command"] == f"{BASE[:-3]} tests/x.py"
    assert "`| tail -n 40` was dropped" in text
    assert "`2>&1` was dropped" in text
    assert "`; echo EXIT=$?` was dropped" in text


@pytest.mark.parametrize(
    "command",
    [
        f"{BASE} tests/x.py; rm -rf /",
        f"{BASE} tests/x.py && id",
        f"{BASE} tests/x.py | tee out",
        f"{BASE} $(id)",
        f"{BASE} `id`",
        f"{BASE} > out",
        f"{BASE} tests/x.py; echo $(id) $?",
        "cd backend && pytest x && id",
        "cd backend; pytest x",
        "X=$(id) pytest tests/x.py",
        f"{BASE}\nid",
        "uv run python -m pytest tests/x.py",
        "python -m pytest_custom tests",
    ],
)
def test_shell_operators_and_other_runners_are_still_refused(command):
    observation, _text = _run(command)
    assert observation["command"] == BASE


def test_narrow_keeps_a_plain_narrower_command_as_asked():
    assert test_commands.narrow("pytest  tests/x.py", "pytest -q") == (
        "pytest  tests/x.py",
        [],
        None,
    )
    assert test_commands.narrow("npx vitest run a", "npm test --silent")[0] == (
        "npm test --silent"
    )


@pytest.mark.parametrize(
    ("command", "folder", "note"),
    [
        (
            "python ../scripts/check_file_sizes.py",
            "backend",
            (
                "`../scripts/check_file_sizes.py` from `backend/` is the root's "
                "`scripts/check_file_sizes.py`"
            ),
        ),
        (
            "poetry run python scripts/check_file_sizes.py",
            None,
            "`poetry run` was dropped",
        ),
        (
            "python3 scripts/check_file_sizes.py; echo EXIT=$?",
            None,
            "`; echo EXIT=$?` was dropped",
        ),
    ],
)
def test_the_check_script_forms_agents_asked_for_run(repo, command, folder, note):  # noqa: F811
    ran = ("python" if "python3" not in command else "python3") + (
        " scripts/check_file_sizes.py"
    )
    daemon = _Daemon({ran: {"exit_code": 0, "stdout": "0 over\n"}, SUITE: OK})
    arguments = {"command": command}
    if folder:
        arguments["folder"] = folder
    observation, text = _box(repo, daemon).call(
        _call("workspace__run_tests", **arguments), 1
    )
    assert daemon.executed() == [ran]
    assert observation["command"] == ran
    assert observation["check_script"]["path"] == "scripts/check_file_sizes.py"
    assert note in text.splitlines()[0]
    assert text.endswith("exit status 0\n0 over\n")


def test_parent_scripts_from_the_root_leave_the_project_and_are_refused(repo):  # noqa: F811
    daemon = _Daemon({SUITE: OK})
    observation, _text = _box(repo, daemon).call(
        _call("workspace__run_tests", command="python ../scripts/check_file_sizes.py"),
        1,
    )
    assert daemon.executed() == [SUITE]
    assert "check_script" not in observation
