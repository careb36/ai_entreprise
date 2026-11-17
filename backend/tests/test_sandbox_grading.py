"""Grading candidate code in the sandbox (docs/design/rsi-loop.md, PR 12;
docs/design/SANDBOX_GRADING_THREAT_MODEL.md): the self-hosting harness's
target measure (``scripts/self_host/spec_source.run_measure``) and the bench's
acceptance tests (``scripts/bench/bench_grade.grade``) run in the daemon's
container sandbox (``vscode-daemon/sandbox.py``), and nothing the candidate
controls turns a failing measure into a pass.

Without a container runtime the sandbox is faked at its boundary: a
``docker`` on the PATH that records its arguments and runs the command on the
host with only the variables passed with ``-e``, each ``-v`` mount a symbolic
link at its container path (under ``tmp_path``). That covers the decision
logic, the wrapper and the frame, the environment and every attack that does
not need the kernel's isolation. The attacks that do (the network, the
read-only mounts, a fork bomb, a memory hog) run against a real runtime when
``docker info`` answers and ``FABRIQUE_GRADING_TEST_IMAGE`` names an image on
the machine with Python and pytest; they are skipped otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "bench"))
sys.path.append(str(REPO / "scripts" / "self_host"))
import bench_grade  # scripts/bench/bench_grade.py, by path
import sandbox_grading  # scripts/bench/sandbox_grading.py, by path
import spec_source  # scripts/self_host/spec_source.py, by path

BENCH = REPO / "bench"
IMAGE = "fabrique-grading:test"

FAKE_DOCKER = r'''#!{python}
"""A container runtime's command line, faked on the host (see the module)."""
import json, os, sys

args = sys.argv[1:]
mode = os.environ.get("FAKE_DOCKER_MODE", "")
if args[:2] == ["image", "inspect"]:
    if mode == "no_image":
        sys.stderr.write("Error: No such image\n")
        sys.exit(1)
    print(json.dumps([{{"Id": "sha256:" + "a" * 64, "RepoDigests": []}}]))
    sys.exit(0)
if args[:1] == ["kill"]:
    sys.exit(0)
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if mode == "down":
    sys.stderr.write("Cannot connect to the Docker daemon\n")
    sys.exit(125)
if mode == "oom":
    sys.exit(137)
with_value = {{"--name", "--network", "--cpus", "--memory", "--memory-swap",
    "--pids-limit", "--cap-drop", "--security-opt", "--tmpfs", "--user",
    "--userns", "-v", "-w", "-e"}}
env, mounts, workdir, i = {{}}, {{}}, None, 1
while i < len(args) and args[i].startswith("-"):
    flag = args[i]
    if flag in with_value:
        value = args[i + 1]
        if flag == "-e":
            key, _, val = value.partition("=")
            env[key] = val
        elif flag == "-v":
            host, ctr = value.split(":")[:2]
            mounts[ctr] = host
        elif flag == "-w":
            workdir = value
        i += 2
    else:
        i += 1
script = args[i + 3]  # image, /bin/sh, -c, script
for ctr, host in mounts.items():
    if ctr.startswith("/workspace"):
        continue
    os.makedirs(os.path.dirname(ctr), exist_ok=True)
    if not os.path.lexists(ctr):
        os.symlink(host, ctr)
os.chdir(mounts.get(workdir, "/"))
os.execve("/bin/sh", ["/bin/sh", "-c", script], env)
'''


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    """The faked ``docker``; returns a reader of the ``run`` argument lists."""
    log = tmp_path / "docker.log"
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(FAKE_DOCKER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.delenv("FABRIQUE_SANDBOX_RUNTIME", raising=False)
    monkeypatch.delenv("FABRIQUE_SANDBOX_REQUIRED", raising=False)
    monkeypatch.setenv(sandbox_grading.IMAGE_ENV, IMAGE)
    root = tmp_path / "ctr"
    monkeypatch.setattr(sandbox_grading, "CANDIDATE", str(root / "candidate"))
    monkeypatch.setattr(sandbox_grading, "GRADERS", str(root / "graders"))

    def runs() -> list[list[str]]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return runs


# --- the target measure ---------------------------------------------------------

MEASURE = """from candidate_mod import value


def test_value_is_42():
    assert value() == 42
"""
FAILING = "def value():\n    return 41\n"
FORGED_JUNIT = (
    '<testsuites><testsuite><testcase classname="m" name="test_value_is_42"/>'
    "</testsuite></testsuites>"
)


def _measure(tmp_path, monkeypatch, candidate_mod: str = FAILING) -> Path:
    """A graders folder (the runner's repository) holding the measure, and a
    build whose backend's ``candidate_mod`` makes it fail."""
    graders = tmp_path / "graders"
    (graders / "m").mkdir(parents=True)
    (graders / "m" / "test_measure.py").write_text(MEASURE)
    backend = tmp_path / "build" / "backend"
    backend.mkdir(parents=True)
    (backend / "candidate_mod.py").write_text(candidate_mod)
    monkeypatch.setattr(spec_source, "REPO", graders)
    return tmp_path / "build"


def _run(build: Path, **kwargs) -> dict:
    return spec_source.run_measure(
        "m/test_measure.py", build, "unused", image_python=sys.executable, **kwargs
    )


def test_the_measure_runs_in_the_sandbox_and_is_read_from_outside(
    fake_runtime, tmp_path, monkeypatch
):
    build = _measure(tmp_path, monkeypatch, "def value():\n    return 42\n")
    passed = _run(build)
    assert (passed["result"], passed["passed"], passed["exit_code"]) == (
        "pass",
        True,
        0,
    )
    assert passed["isolation"] == "sandbox" and passed["reason"] is None
    assert len(passed["output_sha256"]) == 64
    (build / "backend" / "candidate_mod.py").write_text(FAILING)
    failed = _run(build)
    assert (failed["result"], failed["passed"], failed["exit_code"]) == (
        "fail",
        False,
        1,
    )
    # The sandbox the daemon uses for agent commands, with grading's own floor.
    argv = fake_runtime()[-1]
    joined = " ".join(argv)
    assert argv[0] == "run"  # the log holds the arguments after the program
    for flag in (
        "--network none",
        "--cap-drop ALL",
        "--read-only",
        "--pull=never",
        "--memory 2048m",
        "--pids-limit 256",
        "no-new-privileges",
    ):
        assert flag in joined, flag
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert f"{build.resolve()}:{sandbox_grading.CANDIDATE}:ro" in mounts
    assert any(m.endswith(f":{sandbox_grading.GRADERS}:ro") for m in mounts)
    # Only the scratch is mounted writable.
    assert [m for m in mounts if not m.endswith(":ro")] == [
        m for m in mounts if m.endswith(":/workspace:rw")
    ]
    assert "a" * 64 in joined  # run from the image's content ID, observed first
    box = failed["sandbox"]
    assert box["network"] == "off" and box["read_only_root"] and box["pull"] == "never"
    assert box["image_identity"]["id"] == "sha256:" + "a" * 64


ATTACKS = {
    "conftest.py": "import os\nprint('1 passed')\nos._exit(0)\n",
    "pytest.ini": "[pytest]\naddopts = -p no:python --co\n",
    "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '--co'\n",
    "sitecustomize.py": "import os\nos._exit(0)\n",
    "usercustomize.py": "import os\nos._exit(0)\n",
    "pytest.py": "import sys\nprint('1 passed')\nsys.exit(0)\n",
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_a_candidate_file_cannot_turn_a_failing_measure_into_a_pass(
    fake_runtime, tmp_path, monkeypatch, name
):
    build = _measure(tmp_path, monkeypatch)
    (build / "backend" / name).write_text(ATTACKS[name])
    (build / name).write_text(ATTACKS[name])
    result = _run(build)
    assert result["result"] == "fail", result["tail"]
    assert result["exit_code"] == 1


def test_the_runners_environment_does_not_reach_the_sandbox(
    fake_runtime, tmp_path, monkeypatch
):
    build = _measure(tmp_path, monkeypatch)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "pytest.py").write_text(ATTACKS["pytest.py"])
    (shadow / "evil_plugin.py").write_text("import os\nos._exit(0)\n")
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p evil_plugin")
    monkeypatch.setenv("PYTEST_PLUGINS", "evil_plugin")
    monkeypatch.setenv("PYTHONSTARTUP", str(shadow / "evil_plugin.py"))
    result = _run(build)
    assert result["result"] == "fail", result["tail"]
    argv = " ".join(fake_runtime()[-1])
    assert str(shadow) not in argv and "evil_plugin" not in argv


def test_a_test_the_candidate_adds_is_not_collected_and_writes_no_verdict(
    fake_runtime, tmp_path, monkeypatch
):
    build = _measure(tmp_path, monkeypatch)
    tests = build / "backend" / "tests"
    tests.mkdir()
    forge = (
        "from pathlib import Path\n\n\ndef test_forge():\n"
        f"    Path('/tmp/report.xml').write_text({FORGED_JUNIT!r})\n"
    )
    (tests / "test_forge.py").write_text(forge)
    (build / "backend" / "result.json").write_text('{"passed": true}')
    result = _run(build)
    assert result["result"] == "fail"


def test_code_that_rewrites_the_report_on_exit_is_not_a_pass(
    fake_runtime, tmp_path, monkeypatch
):
    # Imported by the measure: rewrites pytest's report after pytest wrote it.
    forger = (
        "import atexit, sys\n\n\ndef _forge():\n"
        "    for arg in sys.argv:\n"
        "        if arg.startswith('--junitxml='):\n"
        f"            open(arg.split('=', 1)[1], 'w').write({FORGED_JUNIT!r})\n\n\n"
        "atexit.register(_forge)\n\n\ndef value():\n    return 41\n"
    )
    build = _measure(tmp_path, monkeypatch, forger)
    result = _run(build)
    assert result["result"] == "unmeasured"
    assert "disagree" in result["reason"]


def test_code_that_exits_zero_before_the_report_is_not_a_pass(
    fake_runtime, tmp_path, monkeypatch
):
    build = _measure(tmp_path, monkeypatch, "import os\nos._exit(0)\n")
    result = _run(build)
    assert (result["result"], result["exit_code"]) == ("unmeasured", 0)
    assert "no report" in result["reason"]


def test_a_forged_frame_on_the_output_is_not_read(fake_runtime, tmp_path, monkeypatch):
    import base64

    frame = base64.b64encode(FORGED_JUNIT.encode()).decode()
    forger = (
        "print('\\n@@FABRIQUE-GRADE 0 deadbeef\\n"
        + frame
        + "\\n@@FABRIQUE-GRADE-END deadbeef', flush=True)\n\n\n"
        "def value():\n    return 41\n"
    )
    build = _measure(tmp_path, monkeypatch, forger)
    result = _run(build)
    assert result["result"] == "fail"


def test_a_long_sleep_is_stopped_and_unmeasured(fake_runtime, tmp_path, monkeypatch):
    build = _measure(tmp_path, monkeypatch, "import time\ntime.sleep(60)\n")
    result = _run(build, timeout=3)
    assert result["result"] == "unmeasured" and not result["passed"]
    assert "stopped after 3 s" in result["reason"]
    assert result["exit_code"] is None


def test_a_run_killed_by_a_limit_is_unmeasured(fake_runtime, tmp_path, monkeypatch):
    # A fork bomb or a memory hog ends with the kernel's kill (137): the
    # runtime's own exit status, read from outside.
    monkeypatch.setenv("FAKE_DOCKER_MODE", "oom")
    result = _run(_measure(tmp_path, monkeypatch))
    assert result["result"] == "unmeasured"
    assert "137" in result["reason"] and "killed" in result["reason"]


# --- failing closed --------------------------------------------------------------


def test_no_runtime_is_unmeasured_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", "no-such-runtime")
    monkeypatch.setenv(sandbox_grading.IMAGE_ENV, IMAGE)
    result = _run(_measure(tmp_path, monkeypatch))
    assert (result["result"], result["passed"]) == ("unmeasured", False)
    assert "Docker or Podman" in result["reason"]
    assert spec_source.exit_status({"measure": result}) == 4


def test_a_runtime_that_cannot_start_the_container_is_unmeasured(
    fake_runtime, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "down")
    result = _run(_measure(tmp_path, monkeypatch))
    assert result["result"] == "unmeasured"
    assert "could not start the container" in result["reason"]


def test_an_image_not_on_the_machine_is_never_pulled(
    fake_runtime, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "no_image")
    result = _run(_measure(tmp_path, monkeypatch))
    assert result["result"] == "unmeasured"
    assert "never pulled" in result["reason"]
    assert fake_runtime() == []  # nothing was run


def test_no_image_named_and_a_mode_without_limits_are_unmeasured(
    fake_runtime, tmp_path, monkeypatch
):
    monkeypatch.delenv(sandbox_grading.IMAGE_ENV)
    result = _run(_measure(tmp_path, monkeypatch))
    assert result["result"] == "unmeasured" and "no grading image" in result["reason"]
    run = sandbox_grading.run_pytest(
        ["python"], image=IMAGE, mounts={}, cwd="/", timeout=5, mode="bwrap"
    )
    assert (run["ran"], run["exit_code"]) == (False, None)
    assert "no CPU or memory limit" in run["reason"]
    assert sandbox_grading.measure_verdict(run)[0] == "unmeasured"


def test_a_missing_daemon_is_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setenv(sandbox_grading.DAEMON_DIR_ENV, str(tmp_path))
    run = sandbox_grading.run_pytest(
        ["python"], image=IMAGE, mounts={}, cwd="/", timeout=5
    )
    assert not run["ran"] and "sandbox.py" in run["reason"]


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ({"ran": True, "exit_code": 0, "report": FORGED_JUNIT}, "pass"),
        ({"ran": True, "exit_code": 1, "report": FORGED_JUNIT}, "unmeasured"),
        ({"ran": True, "exit_code": 0, "report": None, "reason": "x"}, "unmeasured"),
        ({"ran": True, "exit_code": 5, "report": FORGED_JUNIT}, "unmeasured"),
        ({"ran": True, "exit_code": 0, "report": "<testsuites/>"}, "unmeasured"),
        ({"ran": True, "exit_code": 0, "report": "<!DOCTYPE x><x/>"}, "unmeasured"),
        (
            {
                "ran": True,
                "exit_code": 2,
                "report": FORGED_JUNIT.replace("/>", "><error/></testcase>", 1),
            },
            "fail",
        ),
        (
            {"ran": False, "exit_code": None, "report": None, "reason": "x"},
            "unmeasured",
        ),
    ],
)
def test_the_verdict_is_a_pass_only_from_a_consistent_finished_run(run, expected):
    assert sandbox_grading.measure_verdict({"timed_out": False, **run})[0] == expected


def test_the_frame_must_be_this_runs_and_the_last_output():
    import base64

    body = base64.b64encode(b"<testsuites/>").decode()
    good = f"out\n@@FABRIQUE-GRADE 1 abc\n{body}\n@@FABRIQUE-GRADE-END abc\n"
    assert sandbox_grading.read_frame(good, "abc", 1) == ("<testsuites/>", None)
    assert sandbox_grading.read_frame(good, "other", 1)[0] is None
    assert sandbox_grading.read_frame(good, "abc", 0)[0] is None  # another exit status
    assert sandbox_grading.read_frame(good + "late output\n", "abc", 1)[0] is None
    assert sandbox_grading.read_frame("no frame", "abc", 1)[0] is None


# --- the in-process path, explicit only ---------------------------------------------


def test_the_in_process_path_is_explicit_and_says_so(tmp_path, monkeypatch, capsys):
    build = _measure(tmp_path, monkeypatch)
    result = spec_source.run_measure(
        "m/test_measure.py", build, sys.executable, in_process=True
    )
    assert (result["isolation"], result["result"]) == ("in_process", "fail")
    assert "local development only" in result["warning"]
    # The harness's default is the sandbox; the flag says so on stderr.
    parser = __import__("argparse").ArgumentParser()
    spec_source.add_measure_arguments(parser)
    assert parser.parse_args([]).in_process_measure is False
    args = parser.parse_args(["--in-process-measure", "--python", sys.executable])
    args.build, args.checkout = str(build), str(build)
    args.source = {"target_measure": "m/test_measure.py"}
    report: dict = {}
    spec_source.step_measure(None, args, {}, report)
    assert report["measure"]["isolation"] == "in_process"
    assert "graded in-process" in capsys.readouterr().err
    assert spec_source.exit_status(report) == 3


# --- the bench's acceptance tests ---------------------------------------------------


def test_the_bench_grade_runs_in_the_sandbox(fake_runtime, tmp_path):
    spec_dir = BENCH / "specs" / "02-lib-roman"
    graded = bench_grade.grade(
        spec_dir, spec_dir / "fake" / "files", "unused", image_python=sys.executable
    )
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]
    assert graded["isolation"] == "sandbox" and "unmeasured" not in graded
    mounts = [a for a in fake_runtime()[-1] if a.endswith(":ro")]
    assert len(mounts) == 2


def test_a_project_cannot_forge_the_bench_grade_in_the_sandbox(fake_runtime, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    forged = (
        "import sys\nfor a in sys.argv:\n    if a.startswith('--junitxml='):\n"
        "        open(a.split('=', 1)[1], 'w').write('<testsuites/>')\nsys.exit(0)\n"
    )
    for name in ("pytest.py", "sitecustomize.py", "conftest.py"):
        (project / name).write_text(forged)
    (project / "pytest.ini").write_text("[pytest]\naddopts = --co\n")
    graded = bench_grade.grade(
        BENCH / "specs" / "01-cli-wordcount",
        project,
        "unused",
        image_python=sys.executable,
    )
    assert graded["exit_code"] == 1, graded["output_tail"]
    assert set(graded["results"].values()) == {"fail"}


def test_a_bench_grade_the_sandbox_cannot_give_is_unmeasured(tmp_path, monkeypatch):
    monkeypatch.delenv(sandbox_grading.IMAGE_ENV, raising=False)
    spec_dir = BENCH / "specs" / "02-lib-roman"
    graded = bench_grade.grade(spec_dir, spec_dir / "fake" / "files", sys.executable)
    assert graded["passed"] == 0 and set(graded["results"].values()) == {"missing"}
    assert "no grading image" in graded["unmeasured"]


# --- a real container runtime (skipped without one) ---------------------------------


def _real_image() -> str | None:
    image = os.environ.get("FABRIQUE_GRADING_TEST_IMAGE", "").strip()
    docker = shutil.which("docker") or shutil.which("podman")
    if not image or not docker:
        return None
    try:
        info = subprocess.run(
            [docker, "info"], capture_output=True, timeout=20, check=False
        )
        has = subprocess.run(
            [docker, "image", "inspect", image],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return image if info.returncode == 0 and has.returncode == 0 else None


real = pytest.mark.skipif(
    _real_image() is None,
    reason="needs a container runtime that answers and FABRIQUE_GRADING_TEST_IMAGE "
    "(an image on the machine with Python and pytest)",
)

REAL_ATTACKS = {
    # The network is off: a test that needs it fails, never passes.
    "network": (
        (
            "import socket\n\n\ndef value():\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n    return 42\n"
        ),
        {"fail"},
    ),
    # The build is read-only: the candidate cannot rewrite the measure's folder.
    "read_only": (
        (
            "import pathlib\n\n\ndef value():\n"
            "    pathlib.Path('/graders/test_measure.py').write_text('def test_x(): pass')\n"
            "    return 42\n"
        ),
        {"fail"},
    ),
    "fork_bomb": (
        "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n",
        {"fail", "unmeasured"},
    ),
    "memory": (
        "hog = bytearray(8 * 1024 ** 3)\n\n\ndef value():\n    return 42\n",
        {"unmeasured", "fail"},
    ),
    "sleep": ("import time\ntime.sleep(600)\n", {"unmeasured"}),
}


@real
@pytest.mark.parametrize("name", sorted(REAL_ATTACKS))
def test_a_real_sandbox_holds(tmp_path, monkeypatch, name):
    source, allowed = REAL_ATTACKS[name]
    build = _measure(tmp_path, monkeypatch, source)
    result = spec_source.run_measure(
        "m/test_measure.py", build, "unused", timeout=60, image=_real_image()
    )
    assert result["result"] in allowed, result
    assert not result["passed"]
