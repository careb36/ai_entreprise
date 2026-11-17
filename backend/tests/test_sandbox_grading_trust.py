"""What the review of PR #634 found in grading candidate code in the sandbox
(rsi-loop PR 12; docs/design/SANDBOX_GRADING_THREAT_MODEL.md), each a
negative test first:

1. The grader loads the daemon's sandbox from B's git objects, hash-checked,
   never from the candidate's checkout (``graders.py daemon``,
   ``sandbox_grading.daemon_sandbox``).
2. The writable scratch is a bounded tmpfs, not a bind mount of the host's
   disk; filling it, or ``/tmp``, is ``unmeasured``.
6. A skipped test is not a failure: a run with a pass and a skip (exit 0)
   is read, and a skipped criterion fails only itself.
7. The cleanup after a timeout has a deadline; a runtime that does not answer
   leaves the run ``unmeasured`` with the cleanup's failure recorded.

The sandbox is faked at its boundary (``sandbox_grading_helpers``).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from sandbox_grading_helpers import IMAGE, REPO, fake_runtime_fixture  # noqa: F401 - by name

sys.path.insert(0, str(REPO / "scripts" / "bench"))
sys.path.append(str(REPO / "scripts" / "self_host"))
import bench_grade  # scripts/bench/bench_grade.py, by path
import graders  # scripts/bench/graders.py, by path
import junit_xml  # scripts/bench/junit_xml.py, by path
import sandbox_grading  # scripts/bench/sandbox_grading.py, by path
import spec_source  # scripts/self_host/spec_source.py, by path

DAEMON = REPO / "vscode-daemon"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _daemon_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose commit B holds the daemon's modules as they are."""
    repo = tmp_path / "repo"
    (repo / "vscode-daemon").mkdir(parents=True)
    for name in graders.DAEMON_FILES:
        (repo / "vscode-daemon" / name).write_bytes((DAEMON / name).read_bytes())
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(
        repo, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "B"
    )
    return repo, _git(repo, "rev-parse", "HEAD")


# --- 1. the daemon comes from B, hash-checked ----------------------------------------


def test_the_grader_imports_the_daemon_and_everything_it_loads_from_one_folder():
    # What the grader runs on the host before any container: the daemon's
    # sandbox and every daemon module it imports, all of them protected.
    assert set(graders.DAEMON_FILES) == {
        "sandbox.py",
        "sandbox_image.py",
        "sandbox_cache.py",
        "run_logs.py",
    }
    sys.path.insert(0, str(REPO / "backend" / "app"))
    from services.contracts import protected_paths

    for name in graders.DAEMON_FILES:
        assert protected_paths.matches([f"vscode-daemon/{name}"]), name


def test_the_daemon_is_taken_from_bs_objects_not_the_working_tree(tmp_path):
    repo, b_sha = _daemon_repo(tmp_path)
    # The candidate's checkout changes the daemon: none of it is taken.
    (repo / "vscode-daemon" / "run_logs.py").write_text("raise SystemExit('C ran')\n")
    record = graders.daemon(repo, b_sha, tmp_path / "trusted")
    assert record["sha"] == b_sha
    taken = tmp_path / "trusted" / "run_logs.py"
    assert taken.read_bytes() == (DAEMON / "run_logs.py").read_bytes()
    assert sorted(p.name for p in (tmp_path / "trusted").iterdir()) == sorted(
        graders.DAEMON_FILES
    )
    assert record["sha256"] == graders.daemon_digest(tmp_path / "trusted")


def test_only_the_hash_checked_copy_is_loaded(tmp_path, monkeypatch):
    repo, b_sha = _daemon_repo(tmp_path)
    record = graders.daemon(repo, b_sha, tmp_path / "trusted")
    monkeypatch.setenv(sandbox_grading.DAEMON_DIR_ENV, str(tmp_path / "trusted"))
    monkeypatch.setenv(sandbox_grading.DAEMON_SHA256_ENV, record["sha256"])
    loaded = sandbox_grading.daemon_sandbox()
    assert Path(loaded.__file__).parent == (tmp_path / "trusted").resolve()
    # Its own modules, not whatever ``sandbox`` or ``run_logs`` was imported first.
    assert Path(loaded.run_logs.__file__).parent == (tmp_path / "trusted").resolve()
    assert (
        Path(loaded.sandbox_cache.__file__).parent == (tmp_path / "trusted").resolve()
    )
    # A copy changed after it was taken is refused before anything is imported.
    (tmp_path / "trusted" / "sandbox_cache.py").write_text(
        "raise SystemExit('tampered')\n"
    )
    with pytest.raises(sandbox_grading.Unavailable, match="sha256"):
        sandbox_grading.daemon_sandbox()


def test_a_daemon_folder_without_its_digest_is_refused_when_one_is_required(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(sandbox_grading.DAEMON_DIR_ENV, str(DAEMON))
    monkeypatch.setenv(sandbox_grading.DAEMON_SHA256_ENV, "0" * 64)
    run = sandbox_grading.run_pytest(
        ["python"], image=IMAGE, mounts={}, cwd="/", timeout=5
    )
    assert not run["ran"] and "sha256" in run["reason"]


def test_run_sh_takes_the_daemon_from_the_graders_commit():
    text = (REPO / "scripts" / "bench" / "run.sh").read_text()
    assert 'graders.py" daemon --repo' in text
    assert 'FABRIQUE_DAEMON_DIR="$DAEMON_DIR"' in text
    assert 'FABRIQUE_DAEMON_SHA256="$DAEMON_SHA256"' in text
    assert 'FABRIQUE_DAEMON_DIR="$REPO/vscode-daemon"' not in text


# --- 2. a bounded scratch ------------------------------------------------------------


def test_the_scratch_is_a_bounded_tmpfs_not_a_bind_mount(fake_runtime, tmp_path):
    spec_dir = REPO / "bench" / "specs" / "02-lib-roman"
    bench_grade.grade(
        spec_dir, spec_dir / "fake" / "files", "unused", image_python=sys.executable
    )
    argv = fake_runtime()[-1]
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert all(m.endswith(":ro") for m in mounts), mounts  # nothing writable on disk
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    size = f"size={sandbox_grading.SCRATCH_MB}m"
    assert any(t.startswith("/workspace:") and size in t for t in tmpfs), tmpfs


def test_a_full_scratch_or_tmp_is_unmeasured():
    import base64

    body = base64.b64encode(b"<testsuites/>").decode()
    full = f"x\n@@FABRIQUE-GRADE 0 abc 1\n{body}\n@@FABRIQUE-GRADE-END abc\n"
    report, why = sandbox_grading.read_frame(full, "abc", 0)
    assert report is None and "filled" in why
    room = full.replace(" abc 1\n", " abc 0\n")
    assert sandbox_grading.read_frame(room, "abc", 0) == ("<testsuites/>", None)
    script = sandbox_grading.wrapper(["python"], "/", "abc")
    assert "df -Pk" in script and "/workspace" in script


def test_a_daemon_without_a_bounded_scratch_is_unavailable(tmp_path, monkeypatch):
    # An older sandbox.py (no scratch_mb) cannot bound the scratch: refused.
    folder = tmp_path / "old"
    folder.mkdir()
    for name in graders.DAEMON_FILES:
        (folder / name).write_bytes((DAEMON / name).read_bytes())
    old = (folder / "sandbox.py").read_text()
    (folder / "sandbox.py").write_text(
        old.replace(", scratch_mb: int | None = None", "")
    )
    monkeypatch.setenv(sandbox_grading.DAEMON_DIR_ENV, str(folder))
    monkeypatch.delenv(sandbox_grading.DAEMON_SHA256_ENV, raising=False)
    monkeypatch.setenv("FABRIQUE_SANDBOX_RUNTIME", sys.executable)  # any program
    run = sandbox_grading.run_pytest(
        ["python"], image=IMAGE, mounts={}, cwd="/", timeout=5
    )
    assert not run["ran"] and "bounded scratch" in run["reason"]


# --- 6. a skip is not a failure ------------------------------------------------------

MIXED = (
    '<testsuites><testsuite><testcase name="test_req_1_1_a"/>'
    '<testcase name="test_req_1_2_a"><skipped/></testcase>'
    "</testsuite></testsuites>"
)


def test_skips_and_failures_are_told_apart():
    assert junit_xml.outcomes(MIXED) == [
        ("test_req_1_1_a", "pass"),
        ("test_req_1_2_a", "skip"),
    ]
    failed = MIXED.replace("<skipped/>", '<failure message="no"/>')
    assert junit_xml.outcomes(failed)[1] == ("test_req_1_2_a", "fail")
    errored = MIXED.replace("<skipped/>", "<error/>")
    assert junit_xml.outcomes(errored)[1] == ("test_req_1_2_a", "fail")


def test_a_run_with_a_pass_and_a_skip_is_read_and_the_skip_fails_only_itself(
    tmp_path, fake_runtime
):
    spec = tmp_path / "spec"
    (spec / "acceptance").mkdir(parents=True)
    (spec / "spec.json").write_text(
        '{"requirements": [{"criteria": [{"ears": "a"}, {"ears": "b"}]}]}'
    )
    (spec / "acceptance" / "test_x.py").write_text(
        "import pytest\n\n\ndef test_req_1_1_a():\n    pass\n\n\n"
        "def test_req_1_2_a():\n    pytest.skip('not here')\n"
    )
    project = tmp_path / "project"
    project.mkdir()
    graded = bench_grade.grade(spec, project, "unused", image_python=sys.executable)
    assert graded["exit_code"] == 0 and "unmeasured" not in graded, graded
    assert graded["results"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}


@pytest.mark.parametrize(
    ("code", "xml", "expected"),
    [
        (0, MIXED, "fail"),  # a skipped measure did not pass, and is no disagreement
        (1, MIXED, "unmeasured"),  # exit 1 with nothing failed disagrees
        (0, MIXED.replace("<skipped/>", "<failure/>"), "unmeasured"),
    ],
)
def test_the_measure_reads_skips_apart_from_failures(code, xml, expected):
    run = {"ran": True, "timed_out": False, "exit_code": code, "report": xml}
    assert sandbox_grading.measure_verdict(run)[0] == expected


# --- 7. a deadline on the cleanup ----------------------------------------------------


def test_a_runtime_that_does_not_answer_the_kill_leaves_the_run_unmeasured(
    fake_runtime, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "hang_kill")
    monkeypatch.setattr(sandbox_grading, "KILL_SECONDS", 1)
    graders_dir = tmp_path / "graders"
    (graders_dir / "m").mkdir(parents=True)
    (graders_dir / "m" / "test_measure.py").write_text("def test_x():\n    pass\n")
    backend = tmp_path / "build" / "backend"
    backend.mkdir(parents=True)
    (backend / "sitecustomize.py").write_text("")
    monkeypatch.setattr(spec_source, "REPO", graders_dir)
    (graders_dir / "m" / "conftest.py").write_text("")
    (graders_dir / "m" / "test_measure.py").write_text(
        "import time\n\n\ndef test_x():\n    time.sleep(60)\n"
    )
    started = time.monotonic()
    result = spec_source.run_measure(
        "m/test_measure.py",
        tmp_path / "build",
        "unused",
        timeout=2,
        image_python=sys.executable,
    )
    assert time.monotonic() - started < 20
    assert result["result"] == "unmeasured" and "stopped after 2 s" in result["reason"]
    assert "did not answer" in result["cleanup"]
