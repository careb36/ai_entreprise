"""Landing a branch locally (``scripts/land.sh``, issue #728).

Each test builds a throwaway repository with a bare remote standing in for
GitHub, a fake ``scripts/check.sh`` (it fails when the tree holds ``BROKEN``
or ``LAND_TEST_FAIL=1`` is set, and writes ``CHECK_REPORT`` as the real one
does) and the real protected list, then drives the script and observes git:
where ``main`` points, the merge's parents, the record file, the note and the
remote's refs. No test reads what the script prints to decide success.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "land.sh"
CHECK = REPO / "scripts" / "check.sh"
PROTECTED = REPO / "backend/app/services/contracts/protected_paths.py"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)

FAKE_CHECK = """#!/usr/bin/env bash
set -u
state=ok
rc=0
if [ -e BROKEN ] || [ "${LAND_TEST_FAIL:-0}" = 1 ]; then state="FAILED (1)"; rc=1; fi
echo "fake checks: $state"
if [ -n "${CHECK_REPORT:-}" ]; then
  printf 'lint\\tok\\t0.1\\nunit\\t%s\\t0.2\\n' "$state" > "$CHECK_REPORT"
fi
exit "$rc"
"""


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LAND_", "GIT_", "CHECK_"))
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Land Test",
        GIT_AUTHOR_EMAIL="",
        GIT_COMMITTER_NAME="Land Test",
        GIT_COMMITTER_EMAIL="",
    )
    env.update(extra)
    return env


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def land(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd,
        env=_env(**env),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str, **files: str) -> str:
    for path, text in files.items():
        write(repo, path.replace("__", "/"), text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def branch(repo: Path, name: str, files: dict[str, str]) -> str:
    """A branch cut from main with *files* committed, main checked out again."""
    git(repo, "checkout", "-q", "-b", name, "main")
    for path, text in files.items():
        write(repo, path, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"work on {name}")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    return sha


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    write(work, "scripts/check.sh", FAKE_CHECK)
    (work / "scripts/check.sh").chmod(0o755)
    write(
        work, "backend/app/services/contracts/protected_paths.py", PROTECTED.read_text()
    )
    write(work, "docs/architecture/contexts.json", json.dumps({"tests": {"files": {}}}))
    write(work, "app.txt", "one\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work


def head(repo: Path, ref: str = "refs/heads/main") -> str:
    return git(repo, "rev-parse", ref)


def record(repo: Path, sha: str) -> dict:
    return json.loads(
        (repo / ".git/fabrique/verifications" / f"{sha}.json").read_text()
    )


# ── Landing ──────────────────────────────────────────────────────────────────


def test_a_passing_branch_moves_main_to_the_checked_merge_and_records_it(repo):
    before = head(repo)
    tip = branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 0, result.stderr
    merged = head(repo)
    assert git(repo, "log", "-1", "--format=%P", merged).split() == [before, tip]
    assert head(repo, "refs/heads/feature") == tip
    assert (repo / "feature.txt").read_text() == "new\n"  # the checkout followed
    data = record(repo, merged)
    assert (
        data["nature"] == "claim"
        and "not an independent observation" in data["statement"]
    )
    assert data["commit"] == merged and data["status"] == "passed"
    assert data["main_before"] == before
    assert data["branch"] == {"name": "feature", "head": tip}
    assert data["steps"] == [
        {"name": "lint", "status": "ok", "seconds": 0.1},
        {"name": "unit", "status": "ok", "seconds": 0.2},
    ]
    assert data["command"] == "scripts/check.sh full"
    assert data["host"]["name"] and data["account"]["git_user_name"] == "Land Test"
    assert "git" in data["tools"] and data["protected_paths"] == []
    assert data["owner_override"] is False
    note = git(repo, "notes", "--ref=verification", "show", merged)
    assert json.loads(note) == data
    log = repo / ".git" / data["log"]["path"]
    assert "fake checks: ok" in log.read_text()


def test_the_checking_worktree_is_beside_the_checkout_never_under_git(repo):
    """Vite refuses **/.git/** by default: a worktree under .git loaded no
    vitest setup file and every frontend suite failed (issue #766)."""
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 0, result.stderr
    worktrees = git(repo, "worktree", "list", "--porcelain").splitlines()
    paths = [
        Path(line.split(" ", 1)[1])
        for line in worktrees
        if line.startswith("worktree ")
    ]
    beside = repo.parent / f"{repo.name}.land-worktree"
    assert beside.resolve() in [p.resolve() for p in paths]
    assert not any(".git" in p.parts for p in paths)


def test_main_not_checked_out_is_moved_with_a_compare_and_swap(repo):
    tip = branch(repo, "feature", {"feature.txt": "new\n"})
    git(repo, "checkout", "-q", "feature")

    result = land(repo, "feature")

    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%P", "main").split()[1] == tip
    assert head(repo, "HEAD") == tip


def test_a_failing_branch_leaves_main_and_names_the_failing_step(repo):
    before = head(repo)
    branch(repo, "broken", {"BROKEN": "x\n"})

    result = land(repo, "broken")

    assert result.returncode == 1
    assert head(repo) == before
    assert "unit: FAILED (1)" in result.stderr and "untouched" in result.stderr
    assert not list((repo / ".git/fabrique/verifications").glob("*.json"))
    assert git(repo, "notes", "--ref=verification", "list") == ""


def test_a_conflict_leaves_main(repo):
    branch(repo, "conflict", {"app.txt": "theirs\n"})
    commit(repo, "main changes the same line", **{"app.txt": "ours\n"})
    before = head(repo)

    result = land(repo, "conflict")

    assert result.returncode == 1 and "does not merge cleanly" in result.stderr
    assert head(repo) == before


def test_the_checks_run_on_the_merge_not_on_the_branch(repo):
    """main gains a breaking file after the branch was cut: the merge fails."""
    branch(repo, "innocent", {"feature.txt": "new\n"})
    commit(repo, "main breaks", BROKEN="x\n")
    git(repo, "push", "-q", "origin", "main")
    before = head(repo)

    result = land(repo, "innocent", LAND_CHECK_CMD="scripts/check.sh full")

    assert result.returncode == 1
    assert head(repo) == before


def test_nothing_to_land_and_bad_arguments_are_refused(repo):
    assert land(repo, "main").returncode == 2
    git(repo, "branch", "old", "main")
    assert land(repo, "old").returncode == 3
    assert land(repo, "missing").returncode == 2
    assert land(repo).returncode == 2
    assert land(repo, "a", "b").returncode == 2
    assert land(repo, "--verify").returncode == 2
    assert land(repo, "--verify", "main", "--push").returncode == 2
    assert land(repo, "--help").returncode == 0


# ── main moving, and the remote ──────────────────────────────────────────────


def test_main_behind_the_remote_lands_on_the_remote(repo, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(repo.parent / "remote.git"), str(other))
    remote_tip = commit(other, "landed elsewhere", **{"elsewhere.txt": "x\n"})
    git(other, "push", "-q", "origin", "main")
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%P", "main").split()[0] == remote_tip


def test_a_diverged_main_is_refused(repo, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(repo.parent / "remote.git"), str(other))
    commit(other, "remote line", **{"remote.txt": "x\n"})
    git(other, "push", "-q", "origin", "main")
    commit(repo, "local line", **{"local.txt": "x\n"})
    before = head(repo)
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 3 and "diverged" in result.stderr
    assert head(repo) == before


def test_main_moving_during_the_checks_merges_and_checks_again(repo, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(repo.parent / "remote.git"), str(other))
    marker = tmp_path / "moved"
    mover = tmp_path / "mover.sh"
    mover.write_text(
        "set -e\n"
        f"if [ ! -e '{marker}' ]; then touch '{marker}'\n"
        f"  cd '{other}' && echo y > moved.txt && git add moved.txt\n"
        "  git commit -q -m 'moved meanwhile' && git push -q origin main\n"
        "fi\n"
        "printf 'unit\\tok\\t0.1\\n' > \"$CHECK_REPORT\"\n"
    )
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature", LAND_CHECK_CMD=f"bash '{mover}'")

    assert result.returncode == 0, result.stderr
    assert "moved while the checks ran" in result.stderr
    first_parent = git(repo, "log", "-1", "--format=%P", "main").split()[0]
    assert first_parent == head(other)
    assert record(repo, head(repo))["main_before"] == first_parent


def test_push_mirrors_main_and_the_notes(repo):
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature", "--push")

    assert result.returncode == 0, result.stderr
    remote = repo.parent / "remote.git"
    assert head(remote) == head(repo)
    assert head(remote, "refs/notes/verification") == head(
        repo, "refs/notes/verification"
    )


def test_push_refuses_a_main_without_a_record(repo):
    commit(repo, "committed straight to main", **{"direct.txt": "x\n"})

    result = land(repo, "--push")

    assert result.returncode == 3 and "no verification record" in result.stderr
    assert head(repo.parent / "remote.git") != head(repo)


# ── Verification ─────────────────────────────────────────────────────────────


def test_verify_reproduces_the_record_on_another_clone(repo, tmp_path):
    branch(repo, "feature", {"feature.txt": "new\n"})
    assert land(repo, "feature", "--push").returncode == 0
    landed = head(repo)
    other = tmp_path / "second-machine"
    git(tmp_path, "clone", "-q", str(repo.parent / "remote.git"), str(other))

    result = land(other, "--verify", landed)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "reproduced" in result.stdout
    mine = list((other / ".git/fabrique/verifications").glob(f"{landed}.verify.*.json"))
    assert len(mine) == 1
    assert json.loads(mine[0].read_text())["mode"] == "verify"


def test_verify_reports_a_run_that_differs(repo):
    branch(repo, "feature", {"feature.txt": "new\n"})
    assert land(repo, "feature").returncode == 0

    result = land(repo, "--verify", "main", LAND_TEST_FAIL="1")

    assert result.returncode == 1
    assert "differs, status" in result.stdout


def test_verify_without_a_record_is_refused(repo):
    assert land(repo, "--verify", "main").returncode == 3


# ── Protected paths ──────────────────────────────────────────────────────────


def test_a_protected_path_is_refused_without_the_owner(repo):
    before = head(repo)
    branch(repo, "ci", {".github/workflows/ci.yml": "name: ci\n"})

    result = land(repo, "ci")

    assert result.returncode == 3
    assert ".github/workflows/ci.yml" in result.stderr
    assert "not a security boundary" in result.stderr
    assert head(repo) == before


def test_the_owner_lands_a_protected_path_and_the_record_says_so(repo):
    branch(repo, "ci", {".github/workflows/ci.yml": "name: ci\n"})

    result = land(repo, "ci", LAND_OWNER="1")

    assert result.returncode == 0, result.stderr
    data = record(repo, head(repo))
    assert data["owner_override"] is True
    assert data["protected_paths"] == [
        {"path": ".github/workflows/ci.yml", "pattern": ".github/workflows/**"}
    ]


def test_the_list_is_read_from_main_not_from_the_branch(repo):
    """A branch that empties the list in its own copy is judged by main's."""
    emptied = "def protected_patterns(t=None):\n    return ()\n\ndef matches(p, q=()):\n    return []\n"
    branch(
        repo,
        "sneaky",
        {
            "backend/app/services/contracts/protected_paths.py": emptied,
            "bench/answers.txt": "x\n",
        },
    )

    result = land(repo, "sneaky")

    assert result.returncode == 3
    assert "bench/answers.txt" in result.stderr


def test_an_unreadable_list_refuses(repo):
    git(repo, "rm", "-q", "docs/architecture/contexts.json")
    git(repo, "commit", "-q", "-m", "no test map")
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature", LAND_REMOTE="none")

    assert result.returncode == 3 and "cannot read the protected list" in result.stderr


# ── The lock ─────────────────────────────────────────────────────────────────


def test_a_live_landing_holds_the_lock(repo):
    lock = repo / ".git/fabrique/land.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"{os.getpid()} {socket.gethostname()}\n")
    before = head(repo)
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 4
    assert head(repo) == before and lock.exists()


def test_a_dead_landing_lock_is_taken_over_and_released(repo):
    lock = repo / ".git/fabrique/land.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"999999999 {socket.gethostname()}\n")
    branch(repo, "feature", {"feature.txt": "new\n"})

    result = land(repo, "feature")

    assert result.returncode == 0, result.stderr
    assert not lock.exists()


# ── The script itself ────────────────────────────────────────────────────────


def test_check_sh_writes_its_steps_for_the_record(tmp_path):
    report = tmp_path / "report.tsv"
    result = subprocess.run(
        ["bash", str(CHECK), "quick"],
        cwd=REPO,
        env={
            **_env(),
            "CHECK_DRY_RUN": "1",
            "CHECK_FILES": "",
            "CHECK_REPORT": str(report),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [line.split("\t") for line in report.read_text().splitlines()]
    assert ["file sizes", "dry-run", "0.0"] in rows


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")
def test_the_script_is_shellcheck_clean():
    result = subprocess.run(
        ["shellcheck", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout
