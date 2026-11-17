"""The specification the harness's ``issue`` step runs a sprint on (docs/design/rsi-loop.md, PR 6).

Without ``--spec``: the built-in issue #535 (``fixtures/issue-535-*.json``),
exactly as before, which the e2e step reads. With ``--spec FILE``: a
finding's specification, in the PO's shape, plus ``target_measure``: the
repository path of the person-written check that fails on the baseline and
should pass once the sprint's change is in (REQ-2: no cycle without a
finding that has a target measure). Its architecture change is ``--revision``,
by default the file beside it named ``<name>-architecture.json`` for
``<name>-specification.json``.

Before anything is posted, the file is checked with the product's own
``spec_check`` (``backend/app/services/planning/spec_check.py``), imported
from this repository, the baseline's copy, never the snapshot's: a blocking
finding or a missing target measure refuses it, and the warnings are
reported. The measure runs from this repository too, against the backend of
the build under test, and its output is hashed here (REQ-27: never a digest
from the build under test). It runs in the daemon's container sandbox
(``scripts/bench/sandbox_grading.py``, PR 12 of docs/design/rsi-loop.md):
the build read-only, the measure read-only from this repository, no network,
limits, the verdict read from outside; a sandbox that cannot run it leaves
the measure ``unmeasured``, never passed. ``--in-process-measure`` keeps the
earlier run on the runner's own Python for local development, and the report
says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.append(str(REPO / "scripts" / "bench"))  # the sandbox, with the graders
import sandbox_grading

BACKEND_APP = REPO / "backend" / "app"
DEFAULT_SPEC = HERE / "fixtures" / "issue-535-specification.json"
DEFAULT_REVISION = HERE / "fixtures" / "issue-535-architecture.json"
DEFAULT_SPRINT_NAME = "Saved views 1"
MEASURE_TIMEOUT_SECONDS = 900
MEASURE_TAIL_LINES = 30
# Imports pytest from the interpreter's own packages, then puts the build's
# paths last on ``sys.path`` (see run_measure).
RUNNER = (
    "import sys, pytest; sys.path += ['.', 'app']; "
    "sys.exit(pytest.main(sys.argv[1:]))"
)


class SpecRefused(ValueError):
    """A ``--spec`` file the harness will not post, and why."""


def spec_check():
    """The product's ``spec_check``, from this repository (it imports only
    the standard library and ``services.contracts``)."""
    if str(BACKEND_APP) not in sys.path:
        sys.path.insert(0, str(BACKEND_APP))
    from services.planning import spec_check as module

    return module


def _read(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SpecRefused(f"{what} {path}: {e}") from e
    if not isinstance(data, dict):
        raise SpecRefused(f"{what} {path}: not a JSON object")
    return data


def revision_path(spec: Path, revision: str | None = None) -> Path:
    """The architecture change for *spec*: *revision* when given, else the
    sibling ``<name>-architecture.json`` of ``<name>-specification.json``."""
    if revision:
        return Path(revision).resolve()
    stem = spec.name.removesuffix(".json").removesuffix("-specification")
    return spec.with_name(f"{stem}-architecture.json")


def measure_path(record: dict) -> Path:
    """The target measure the file names, inside this repository."""
    named = record.get("target_measure")
    if not isinstance(named, str) or not named.strip():
        raise SpecRefused(
            "the specification names no target_measure: a cycle needs a finding with a measure (REQ-2)"
        )
    path = (REPO / named).resolve()
    if REPO not in path.parents or not path.is_file():
        raise SpecRefused(f"target_measure {named}: no such file in this repository")
    return path


def load(spec: str | None = None, revision: str | None = None) -> dict:
    """The issue step's input: ``spec`` (the record to post), ``change`` (the
    architecture revision), ``sprint_name``, and for a ``--spec`` file its
    ``source``, ``target_measure`` and ``warnings``. Raises ``SpecRefused``."""
    if not spec:
        return {
            "spec": _read(DEFAULT_SPEC, "specification"),
            "change": _read(DEFAULT_REVISION, "architecture change"),
            "sprint_name": DEFAULT_SPRINT_NAME,
        }
    path = Path(spec).resolve()
    record = _read(path, "specification")
    checker = spec_check()
    result = checker.check(checker.normalize(record))
    blocking = [f for f in result["findings"] if f["severity"] == "blocking"]
    if blocking:
        listed = "; ".join(
            f"{f['where']} {f['code']}: {f['message']}" for f in blocking
        )
        raise SpecRefused(
            f"{path} does not pass the product's specification check: {listed}"
        )
    measure = measure_path(record)
    change_file = revision_path(path, revision)
    change = _read(change_file, "architecture change")
    if not change.get("title"):
        raise SpecRefused(f"architecture change {change_file}: no title")
    return {
        "spec": record,
        "change": change,
        "sprint_name": str(record["title"])[:80],
        "source": str(path),
        "revision": str(change_file),
        "target_measure": measure.relative_to(REPO).as_posix(),
        "warnings": [
            (f["where"], f["code"])
            for f in result["findings"]
            if f["severity"] != "blocking"
        ],
    }


def revised_architecture(base: dict, change: dict) -> dict:
    """The draft version *base* revised by a change: new title, the new
    specification's covers, appended interfaces and decisions. A module keeps
    what *base* holds for the earlier specifications (``earlier_covers``)."""
    covers = change.get("covers") or {}
    return {
        "title": change["title"],
        "overview": base["overview"],
        "modules": [
            {**m, "covers": list(covers.get(m["name"], []))} for m in base["modules"]
        ],
        "interfaces": list(base["interfaces"]) + list(change.get("interfaces") or []),
        # The accepted decisions keep their dates; a new one is accepted
        # when a person approves the revision.
        "decisions": list(base["decisions"]) + list(change.get("decisions") or []),
        "conventions": list(base["conventions"]),
    }


def add_measure_arguments(parser) -> None:
    """The ``measure`` step's options of ``self_host.py``."""
    parser.add_argument("--python", default=sys.executable, help="measure, --in-process-measure only: a Python with the backend's dependencies")
    parser.add_argument("--grading-image", default=os.environ.get(sandbox_grading.IMAGE_ENV), help=f"measure: the sandbox's image, on this machine, with the backend's dependencies and pytest (default: {sandbox_grading.IMAGE_ENV})")
    parser.add_argument("--in-process-measure", action="store_true", help="measure: local development only, the build's code runs in --python; never a cycle's measurement")


def step_measure(api, args, state, report):
    """The harness's ``measure`` step: the ``--spec`` file's target measure
    (this repository's copy) on ``--build``'s backend, else the snapshot's,
    in the sandbox unless ``--in-process-measure``."""
    build = Path(args.build or args.checkout).resolve()
    in_process = getattr(args, "in_process_measure", False)
    if in_process:
        print(f"warning: {sandbox_grading.IN_PROCESS_WARNING}", file=sys.stderr)
    report["measure"] = run_measure(
        args.source["target_measure"],
        build,
        args.python,
        in_process=in_process,
        image=getattr(args, "grading_image", None),
    )


def exit_status(report: dict) -> int:
    """The harness's exit status for a measure: 3 failed, 4 unmeasured."""
    measured = report.get("measure")
    if not measured or measured.get("passed"):
        return 0
    return 4 if measured.get("result") == sandbox_grading.UNMEASURED else 3


def run_measure(
    measure: str,
    build: Path,
    python: str,
    timeout: int = MEASURE_TIMEOUT_SECONDS,
    *,
    in_process: bool = False,
    image: str | None = None,
    image_python: str = "python",
) -> dict:
    """Run the target measure (this repository's file) with pytest on
    *build*'s ``backend/``. ``result`` is ``pass``, ``fail`` or
    ``unmeasured`` (``reason`` says why), ``passed`` is ``result == "pass"``,
    and the sha256 of the output is computed here. In the sandbox (the
    default) the image's ``image_python`` runs it; *python* only with
    *in_process*."""
    if in_process:
        return _run_in_process(measure, build, python, timeout)
    target = REPO / measure
    graders = sandbox_grading.GRADERS
    argv = [
        image_python,
        *_pytest_args(graders, f"{graders}/{target.name}"),
    ]
    run = sandbox_grading.run_pytest(
        argv,
        image=image or os.environ.get(sandbox_grading.IMAGE_ENV),
        mounts={sandbox_grading.CANDIDATE: build, graders: target.parent},
        cwd=f"{sandbox_grading.CANDIDATE}/backend",
        timeout=timeout,
    )
    result, reason = sandbox_grading.measure_verdict(run)
    return {
        "target_measure": measure,
        "build": str(build),
        "isolation": sandbox_grading.SANDBOX,
        "command": " ".join(["pytest", *argv[5:]]),
        "exit_code": run["exit_code"],
        "result": result,
        "passed": result == sandbox_grading.PASS,
        "reason": reason,
        "sandbox": run["sandbox"],
        "cleanup": run.get("cleanup"),
        **sandbox_grading.output_record(run),
    }


def _pytest_args(rootdir: str, target: str) -> list[str]:
    """``-I -B -c RUNNER`` and pytest's options: no conftest, no ini file,
    no cache, no plugin auto-loaded, the measure's folder as rootdir."""
    return ["-I", "-B", "-c", RUNNER, "-q", "-p", "no:cacheprovider", "--noconftest", "-c", os.devnull, "--rootdir", rootdir, target]


def _run_in_process(measure: str, build: Path, python: str, timeout: int) -> dict:
    """The earlier run, on the runner's own *python*: local development only."""
    # The runner is loaded before any path of the build: ``-I`` keeps the
    # build's directory, PYTHONPATH and user site out of ``sys.path``, so a
    # ``backend/pytest.py`` in the build cannot stand in for pytest. The
    # build's ``.`` and ``app`` are appended after pytest is imported, its
    # ini files and conftests are not read (``-c`` an empty file, rootdir the
    # measure's folder, ``--noconftest``) and plugins are not auto-loaded.
    # The measure imports the build's code in this process's Python, with the
    # runner's files and network: run_measure's sandbox is the default.
    target = REPO / measure
    command = [python, *_pytest_args(str(target.parent), str(target))]
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")},
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    try:
        done = subprocess.run(
            command,
            cwd=build / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as e:
        said = "".join(
            x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
            for x in (e.stdout, e.stderr)
        )
        exit_code, output = None, f"{said}\n[stopped after {timeout} s]"
    except OSError as e:
        exit_code, output = None, f"{type(e).__name__}: {e}"
    return {
        "target_measure": measure,
        "build": str(build),
        "isolation": sandbox_grading.IN_PROCESS,
        "warning": sandbox_grading.IN_PROCESS_WARNING,
        "command": " ".join(["pytest", *command[5:]]),
        "exit_code": exit_code,
        "result": sandbox_grading.PASS if exit_code == 0 else sandbox_grading.FAIL,
        "passed": exit_code == 0,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "tail": output.strip().splitlines()[-MEASURE_TAIL_LINES:],
    }


def resume_sprint(state: dict, specification_id: int, from_file: bool) -> bool:
    """Whether the checkout's saved sprint is this specification's: resumed
    then. A sprint saved for another specification (or before the harness
    recorded one, under ``--spec``) is refused rather than reused."""
    if not state.get("sprint_id"):
        return False
    saved = state.get("sprint_specification_id")
    # A state saved before the specification was recorded is #535's.
    if saved == specification_id or (saved is None and not from_file):
        return True
    raise SpecRefused(
        f"sprint {state['sprint_id']} was started for specification {saved}, "
        f"not {specification_id}: use a fresh --checkout for a new finding"
    )
