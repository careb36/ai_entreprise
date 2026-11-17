"""The bench's grade: a specification's person-written acceptance tests run
on the project the factory produced, read back per criterion
(``scorecard.py grade``; re-exported by ``scorecard``).

By default the tests run in the daemon's container sandbox
(``sandbox_grading``): the project read-only at ``/candidate``, the tests
(this commit's, or ``run.sh --graders``' commit's) read-only at
``/graders``, no network, CPU, memory, process and time limits, and the
JUnit report read from the run's output by the runner. A grade the sandbox
could not give is ``unmeasured``: every criterion ``missing``, never
``pass``. ``in_process=True`` (``scorecard.py grade --in-process``,
``BENCH_GRADE_IN_PROCESS=1``) keeps the earlier isolated pytest on the
runner's own Python, for local development; the grade says so.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import sandbox_grading
from junit_xml import FAIL as CASE_FAILED
from junit_xml import JUnitError, outcomes, testcases
from schema import FAIL, MISSING, PASS

_TEST_NAME = re.compile(r"^test_req_(\d+)_(\d+)(?:_|\[|$)")


def criteria_of(spec: dict[str, Any]) -> list[str]:
    """The criterion ids of *spec* in the order the backend numbers them
    (``services/planning/spec_check.normalize``: REQ-<n>.<m>, from 1, in order)."""
    return [
        f"REQ-{i}.{j}"
        for i, requirement in enumerate(spec.get("requirements") or [], start=1)
        for j, _criterion in enumerate(requirement.get("criteria") or [], start=1)
    ]


def criterion_of_test(name: str) -> str | None:
    """``test_req_1_2_reads`` (or ``test_req_1_2[case]``) → ``REQ-1.2``."""
    match = _TEST_NAME.match(name or "")
    return f"REQ-{match.group(1)}.{match.group(2)}" if match else None


def criteria_from_junit(xml_text: str, criteria: list[str]) -> dict[str, str]:
    """Per criterion: ``pass`` when it has tests and all passed, ``fail`` when
    one failed, errored or was skipped, ``missing`` when no test named it ran.
    A report that cannot be read, or that declares a DTD or an entity
    (``junit_xml``), leaves every criterion missing."""
    seen: dict[str, list[bool]] = {}
    try:
        cases = testcases(xml_text) if xml_text.strip() else []
    except JUnitError:
        cases = []
    for name, passed in cases:
        criterion = criterion_of_test(name)
        if criterion is not None:
            seen.setdefault(criterion, []).append(passed)
    return {
        c: (MISSING if c not in seen else PASS if all(seen[c]) else FAIL)
        for c in criteria
    }


# Written next to the copied tests by the grader. The project goes at the END
# of sys.path, after the standard library and pytest, so nothing in it (a
# pytest.py, a conftest.py, a json.py) can stand in for either; the grader
# itself starts isolated (python -I), so PYTHONPATH and the working directory
# put nothing first, and no sitecustomize of the project's runs in it.
GRADER_CONFTEST = """import sys

sys.path.append({project!r})
"""


def _pytest_args(python: str, rootdir: str, tests: str) -> list[str]:
    return [
        python,
        "-I",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        os.devnull,
        "--rootdir",
        rootdir,
        # Only the grader's conftest: none from a folder above it.
        "--confcutdir",
        rootdir,
        "--import-mode=importlib",
        tests,
    ]


def _sandboxed_report(
    graders: Path, project: Path, timeout: int, image: str | None, python: str
) -> tuple[str, int | None, str, dict[str, Any]]:
    """(report, exit status, output tail, extra fields) from the sandbox; the
    report is empty when the grade is unmeasured, and the extra says why."""
    run = sandbox_grading.run_pytest(
        _pytest_args(
            python, sandbox_grading.GRADERS, f"{sandbox_grading.GRADERS}/acceptance"
        ),
        image=image,
        mounts={sandbox_grading.CANDIDATE: project, sandbox_grading.GRADERS: graders},
        cwd=sandbox_grading.GRADERS,
        timeout=timeout,
        env={"PYTHONPATH": sandbox_grading.CANDIDATE},
    )
    extra = {
        "isolation": sandbox_grading.SANDBOX,
        "sandbox": run["sandbox"],
        "output_sha256": sandbox_grading.output_record(run)["output_sha256"],
    }
    code, report = run["exit_code"], run["report"] or ""
    reason = run["reason"]
    if reason is None and code not in (0, 1):
        reason = f"exit status {code}: pytest did not finish a run"
    if reason is None:
        try:
            # A skip is no failure: pytest exits 0 on passes and skips.
            failed = any(o == CASE_FAILED for _, o in outcomes(report))
        except JUnitError as exc:
            failed, reason = False, f"the report could not be read: {exc}"
        if reason is None and failed != (code == 1):
            reason = f"exit status {code} and the report disagree"
    if reason is not None:
        report = ""
        extra["unmeasured"] = reason
    output = (run["stdout"] + run["stderr"])[-4000:] if run["ran"] else reason
    return report, code, output, extra


def grade(
    spec_dir: Path,
    project: Path,
    python: str,
    timeout: int = 300,
    *,
    in_process: bool = False,
    image: str | None = None,
    image_python: str = "python",
) -> dict[str, Any]:
    """Run ``<spec_dir>/acceptance`` against *project* and read the criteria back.

    The tests are copied out of the repository so no configuration found
    above them applies, next to ``GRADER_CONFTEST``, which reaches the project
    only after everything else on ``sys.path``. In the sandbox (the default)
    that folder and the project are mounted read-only, ``image_python`` is
    the image's interpreter and ``PYTHONPATH`` names the project for the
    commands a test runs (``python -m <package>``); the isolated grader
    ignores it. With *in_process* pytest starts isolated (``-I``) on
    *python*, the runner's own, and the JUnit report goes to a folder with a
    random name, read once pytest has exited; the project's code then runs in
    the runner's Python, with its files and network. Either way a report
    pytest did not finish (exit status other than 0 or 1) is not read."""
    spec = json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))
    criteria = criteria_of(spec)
    project_path = str(project.resolve())
    with (
        tempfile.TemporaryDirectory(prefix="bench-grade-") as tmp,
        tempfile.TemporaryDirectory(prefix="bench-report-") as report_dir,
    ):
        tests = Path(tmp) / "acceptance"
        shutil.copytree(spec_dir / "acceptance", tests)
        seen_as = project_path if in_process else sandbox_grading.CANDIDATE
        (Path(tmp) / "conftest.py").write_text(
            GRADER_CONFTEST.format(project=seen_as), encoding="utf-8"
        )
        if not in_process:
            xml_text, exit_code, output, extra = _sandboxed_report(
                Path(tmp),
                project,
                timeout,
                image or os.environ.get(sandbox_grading.IMAGE_ENV),
                image_python,
            )
        else:
            extra = {
                "isolation": sandbox_grading.IN_PROCESS,
                "warning": sandbox_grading.IN_PROCESS_WARNING,
            }
            report = Path(report_dir) / f"{os.urandom(8).hex()}.xml"
            env = {
                **os.environ,
                "PYTHONPATH": project_path,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            command = [*_pytest_args(python, tmp, str(tests)), f"--junitxml={report}"]
            try:
                run = subprocess.run(
                    command,
                    cwd=tmp,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                output, exit_code = run.stdout[-4000:], run.returncode
            except subprocess.TimeoutExpired:
                output, exit_code = "acceptance tests timed out", None
            finished = exit_code in (0, 1) and report.exists()
            xml_text = report.read_text(encoding="utf-8") if finished else ""
    results = criteria_from_junit(xml_text, criteria)
    return {
        "passed": sum(1 for r in results.values() if r == PASS),
        "total": len(criteria),
        "results": results,
        "exit_code": exit_code,
        "output_tail": output,
        **extra,
    }
