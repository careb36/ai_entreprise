"""The kept output of a command ends with its result (sprint 4, 2026-11-23).

Every refuted run of sprint 4 showed pytest-asyncio's deprecation warning,
printed on stderr, and never which test failed: the tail of
``stdout + stderr`` was stderr alone. ``workspace_daemon.output_tail`` keeps
the end of stdout last and gives it most of the room."""

from __future__ import annotations

from services.evidence import verification
from services.workspace import folder_commands, workspace_daemon
from services.workspace.workspace_daemon import (
    STDERR_HEADING,
    STDOUT_HEADING,
    TEST_OUTPUT_TAIL_CHARS,
    output_tail,
)

PYTEST_END = (
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_browser_image.py::test_split - AssertionError: 641 != 642\n"
    "1 failed, 6864 passed, 14 warnings in 512.10s\n"
)
WARNING = (
    "PytestDeprecationWarning: The configuration option "
    '"asyncio_default_fixture_loop_scope" is unset.\n'
) * 80


def test_output_that_fits_is_kept_whole_as_before():
    assert output_tail("out\n", "err\n", 100) == "out\nerr\n"
    assert output_tail(None, None, 100) == ""


def test_a_long_stderr_no_longer_hides_the_test_result():
    stdout = "." * 5000 + "\n" + PYTEST_END
    old = (stdout + WARNING)[-TEST_OUTPUT_TAIL_CHARS:]
    assert "FAILED" not in old  # the defect, as sprint 4 saw it
    kept = output_tail(stdout, WARNING, TEST_OUTPUT_TAIL_CHARS)
    assert len(kept) <= TEST_OUTPUT_TAIL_CHARS
    assert kept.endswith(PYTEST_END)
    assert kept.startswith(STDERR_HEADING) and STDOUT_HEADING in kept
    stderr_part = kept[len(STDERR_HEADING) : kept.index(STDOUT_HEADING)]
    assert 0 < len(stderr_part) <= TEST_OUTPUT_TAIL_CHARS // 4


def test_a_short_stdout_is_kept_whole_and_stderr_takes_the_rest():
    kept = output_tail(PYTEST_END, WARNING, TEST_OUTPUT_TAIL_CHARS)
    assert kept.endswith(STDOUT_HEADING + PYTEST_END)
    assert len(kept) == TEST_OUTPUT_TAIL_CHARS


def test_one_stream_alone_is_a_plain_tail():
    assert output_tail("x" * 50 + "END", "", 20) == ("x" * 50 + "END")[-20:]
    assert output_tail("  \n", "y" * 50 + "END", 20) == ("y" * 50 + "END")[-20:]


def test_the_test_run_record_keeps_the_result():
    evidence: dict = {}
    reply = {"exit_code": 1, "stdout": "." * 5000 + PYTEST_END, "stderr": WARNING}
    workspace_daemon.record_test_run(evidence, "pytest -q", reply)
    run = evidence["test_run"]
    assert run["output_tail"].endswith(PYTEST_END)
    assert run["output_truncated"] is True
    check = verification.check_tests_run(evidence)
    assert check["outcome"] == "refuted"
    assert "FAILED tests/test_browser_image.py::test_split" in check["observation"]


def test_a_folder_command_record_keeps_the_result():
    reply = {"exit_code": 1, "stdout": "." * 5000 + PYTEST_END, "stderr": WARNING}
    record, output = folder_commands.command_record(
        "suite", "backend", "pytest -q", reply
    )
    assert record["output_tail"].endswith(PYTEST_END)
    # The whole output still goes to the readers that parse it.
    assert output == reply["stdout"] + reply["stderr"]
