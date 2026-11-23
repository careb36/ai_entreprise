"""A check the delivery rewrote is not evidence about it (sprint 4, 2026-11-23).

A QA task rewrote ``scripts/check_file_sizes.py`` and
``scripts/check_architecture.py``, the every-delivery commands that judged
it. ``command_runs`` refutes such a run whatever it exited, from the paths the
daemon reported writing, never from what the agent declared."""

from __future__ import annotations

from services.evidence import command_runs, verification


def _check(command: str, folder: str = "", exit_code: int = 0) -> dict:
    return {
        "kind": "every_delivery",
        "folder": folder,
        "command": command,
        "exit_code": exit_code,
        "output_tail": "",
    }


def _wrote(*paths: str) -> dict:
    return {"workspace_writes": [{"path": p, "status": "written"} for p in paths]}


def test_a_passing_check_whose_file_the_delivery_rewrote_refutes():
    record = _check("python3 scripts/check_file_sizes.py")
    outcome, text = command_runs.outcome_of(
        record, _wrote("scripts/check_file_sizes.py")
    )
    assert outcome == "refuted"
    assert "the delivery changed scripts/check_file_sizes.py" in text
    assert "not evidence" in text


def test_the_required_commands_check_refutes_and_says_which_file():
    evidence = {
        **_wrote("scripts/check_architecture.py", "backend/app/x.py"),
        "command_runs": [
            _check("python3 scripts/check_architecture.py"),
            _check("python3 scripts/check_frontend_architecture.py"),
        ],
    }
    result = command_runs.check(evidence)
    assert result["outcome"] == "refuted"
    assert [r["outcome"] for r in result["runs"]] == ["refuted", "pass"]
    assert "scripts/check_architecture.py" in result["observation"]


def test_a_check_the_delivery_did_not_touch_is_judged_by_its_exit():
    evidence = _wrote("backend/app/x.py")
    assert (
        command_runs.outcome_of(_check("python3 scripts/check.py"), evidence)[0]
        == "pass"
    )
    failed = _check("python3 scripts/check.py", exit_code=1)
    assert command_runs.outcome_of(failed, evidence)[0] == "refuted"


def test_a_written_file_that_failed_to_write_does_not_count():
    evidence = {
        "workspace_writes": [{"path": "scripts/c.py", "status": "rejected_syntax"}]
    }
    assert (
        command_runs.outcome_of(_check("python3 scripts/c.py"), evidence)[0] == "pass"
    )


def test_only_what_the_daemon_wrote_counts_not_what_the_agent_declared():
    evidence = {"claimed_files": ["scripts/c.py"], "workspace_writes": []}
    assert (
        command_runs.outcome_of(_check("python3 scripts/c.py"), evidence)[0] == "pass"
    )


def test_suites_and_installs_may_change_their_own_files():
    """Tests change with the code they test: the rule is for checks only."""
    evidence = _wrote("tests/test_x.py", "package.json")
    suite = {**_check("python -m pytest tests/test_x.py"), "kind": "suite"}
    install = {**_check("npm ci"), "kind": "install"}
    assert command_runs.outcome_of(suite, evidence)[0] == "pass"
    assert command_runs.outcome_of(install, evidence)[0] == "pass"


def test_the_command_files_are_resolved_from_the_check_folder():
    assert command_runs.command_files("node ./lint.mjs --fix", "frontend") == [
        "frontend/lint.mjs"
    ]
    assert command_runs.command_files("bash 'scripts/a b.sh' x.txt") == [
        "scripts/a b.sh"
    ]
    # Outside the repository, flags and absolute paths are never its files.
    assert command_runs.command_files("python /usr/bin/x.py ../y.py --cfg=z.py") == []
    rewritten = command_runs.rewritten_by_delivery(
        _check("node ./lint.mjs", folder="frontend"), _wrote("frontend/lint.mjs")
    )
    assert rewritten == ["frontend/lint.mjs"]


def test_a_console_producer_change_counts_as_written():
    evidence = {"console_run": {"files": [{"path": "scripts/c.py"}]}}
    assert (
        command_runs.outcome_of(_check("python3 scripts/c.py"), evidence)[0]
        == "refuted"
    )


def test_the_verifier_reads_the_rule_through_required_commands():
    assert verification.command_runs is command_runs
