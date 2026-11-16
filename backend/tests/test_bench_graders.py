"""The bench's graders taken from a commit (``run.sh --graders``; REQ-4 of
``docs/design/rsi-loop.md``).

The grading paths are read from the commit's objects, never from the working
tree: a test edited or added in the checkout after the commit does not reach
the grade. A symbolic link under them is refused. The record carries the
full sha, git's tree id per path (the same ``git rev-parse`` gives) and a
sha256 of the files taken, which the runner computes itself. The scorecard
carries that record, the schema checks it, the Markdown and ``compare.py``
show it, and ``run.sh`` takes its specifications and grader from it.

No stack here: ``run.sh`` is run only up to its database step.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import graders  # scripts/bench/graders.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import _card, _entry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """A repository with a spec and its acceptance test committed."""
    repo = tmp_path / "repo"
    spec = repo / "bench" / "specs" / "01-a"
    (spec / "acceptance").mkdir(parents=True)
    (spec / "spec.json").write_text('{"title": "a"}\n')
    (spec / "acceptance" / "test_a.py").write_text(
        "def test_req_1_1_a():\n    assert True\n"
    )
    (repo / "scripts" / "bench").mkdir(parents=True)
    (repo / "scripts" / "bench" / "scorecard.py").write_text("# the grader\n")
    (repo / "README.md").write_text("not a grading path\n")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-qm",
        "b",
    )
    return repo


def test_the_graders_come_from_the_commit_not_the_working_tree(tmp_path):
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    test = repo / "bench" / "specs" / "01-a" / "acceptance" / "test_a.py"
    test.write_text("def test_req_1_1_a():\n    pass  # weakened in the checkout\n")
    (test.parent / "test_added.py").write_text("def test_req_1_2_new(): pass\n")
    dest = tmp_path / "graders"
    record = graders.checkout(repo, "HEAD", dest)
    taken = dest / "bench" / "specs" / "01-a" / "acceptance"
    assert (
        taken / "test_a.py"
    ).read_text() == "def test_req_1_1_a():\n    assert True\n"
    assert not (taken / "test_added.py").exists()
    assert (dest / "scripts" / "bench" / "scorecard.py").is_file()
    assert not (dest / "README.md").exists()  # only the grading paths
    assert record["sha"] == sha
    assert record["trees"] == {
        "bench": _git(repo, "rev-parse", f"{sha}:bench"),
        "scripts/bench": _git(repo, "rev-parse", f"{sha}:scripts/bench"),
    }
    assert graders.problems({"graders": record}) == []


def test_the_digest_is_computed_from_the_files_taken(tmp_path):
    repo = _repo(tmp_path)
    dest = tmp_path / "graders"
    record = graders.checkout(repo, "HEAD", dest)
    expected = hashlib.sha256()
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        own = hashlib.sha256(path.read_bytes()).hexdigest()
        expected.update(f"{path.relative_to(dest).as_posix()}\0{own}\n".encode())
    assert record["sha256"] == expected.hexdigest()
    # A second checkout empties the folder first: a stray file does not stay.
    (dest / "bench" / "stray.py").write_text("x = 1\n")
    assert graders.checkout(repo, "HEAD", dest)["sha256"] == record["sha256"]
    # Another commit's tests give another digest and tree.
    (repo / "bench" / "specs" / "01-a" / "acceptance" / "test_b.py").write_text(
        "x = 2\n"
    )
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-qm",
        "c",
    )
    other = graders.checkout(repo, "HEAD", tmp_path / "other")
    assert other["sha256"] != record["sha256"]
    assert other["trees"]["bench"] != record["trees"]["bench"]
    assert other["trees"]["scripts/bench"] == record["trees"]["scripts/bench"]


def test_a_symbolic_link_or_an_unknown_commit_is_refused(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(graders.GradersError, match="not a commit"):
        graders.checkout(repo, "no-such-ref", tmp_path / "g")
    with pytest.raises(graders.GradersError, match="not a revision"):
        graders.checkout(repo, "--all", tmp_path / "g")
    os.symlink("/etc/passwd", repo / "bench" / "specs" / "01-a" / "acceptance" / "link")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-qm",
        "l",
    )
    with pytest.raises(graders.GradersError, match="not a regular file"):
        graders.checkout(repo, "HEAD", tmp_path / "g")


def test_the_command_writes_the_record_or_exits_2(tmp_path, capsys):
    repo = _repo(tmp_path)
    out = tmp_path / "graders.json"
    args = [
        "checkout",
        "--repo",
        str(repo),
        "--dest",
        str(tmp_path / "g"),
        "--out",
        str(out),
    ]
    assert graders.main([*args, "--rev", "HEAD"]) == 0
    assert json.loads(out.read_text())["sha"] == _git(repo, "rev-parse", "HEAD")
    assert graders.main([*args, "--rev", "nope"]) == 2
    assert "could not be taken" in capsys.readouterr().err


RECORD = {
    "sha": "a" * 40,
    "trees": {"bench": "b" * 40, "scripts/bench": "c" * 40},
    "sha256": "d" * 64,
}


def test_the_card_carries_the_graders_and_the_schema_checks_them(tmp_path, capsys):
    card = scorecard.build_scorecard(
        [_entry()],
        stamp="20260101T000000Z",
        commit="abc",
        llm="fake",
        graders_record=RECORD,
    )
    assert card["graders"] == RECORD and scorecard.validate(card) == []
    assert f"Graded by the tests of commit `{'a' * 40}`" in scorecard.render_markdown(
        card
    )
    assert "Graded by" not in scorecard.render_markdown(_card())  # a card without them
    for bad, problem in (
        ({**RECORD, "sha": "abc"}, "graders.sha"),
        ({**RECORD, "trees": {}}, "graders.trees"),
        ({**RECORD, "sha256": "e" * 63}, "graders.sha256"),
    ):
        assert any(problem in p for p in scorecard.validate({**card, "graders": bad}))
    assert scorecard.validate({**card, "graders": "x"}) == ["graders must be an object"]
    # write --graders FILE puts the record on the card.
    record, entry = tmp_path / "graders.json", tmp_path / "01-a.json"
    record.write_text(json.dumps(RECORD))
    entry.write_text(json.dumps(_entry()))
    out = tmp_path / "cards"
    argv = ["write", "--out-dir", str(out), "--stamp", "20260102T000000Z"]
    assert scorecard.main([*argv, "--graders", str(record), str(entry)]) == 0
    written = json.loads((out / "20260102T000000Z.json").read_text())
    assert written["graders"] == RECORD
    # compare.py names the graders of both sides.
    capsys.readouterr()
    path = out / "20260102T000000Z.json"
    assert compare.main([str(path), "--previous", str(path)]) == 0
    assert "graders `aaaaaaaaaaaa` → `aaaaaaaaaaaa`" in capsys.readouterr().out
    assert graders.label(_card()) == "the build's own"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_sh(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """run.sh up to its database step: Postgres on a closed port stops it there."""
    env = {
        **os.environ,
        "BENCH_PYTHON": sys.executable,
        "BENCH_WORKDIR": str(tmp_path / "work"),
        "BENCH_POSTGRES_HOST": "127.0.0.1",
        "BENCH_POSTGRES_PORT": str(_free_port()),
        "BENCH_POSTGRES_PASSWORD": "unused",
        "BENCH_BACKEND_PORT": str(_free_port()),
    }
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "bench" / "run.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_run_sh_takes_the_specs_and_the_grader_from_the_graders_commit(tmp_path):
    pytest.importorskip("psycopg2")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        pytest.skip("the checkout has no commit")
    sha = head.stdout.strip()
    run = _run_sh(
        tmp_path, "--graders", sha, str(REPO / "bench" / "specs" / "02-lib-roman")
    )
    assert f"graders: {sha} sha256 " in run.stdout, run.stdout + run.stderr
    assert "could not recreate the database" in run.stdout  # it got past the graders
    assert run.returncode == 2
    work = tmp_path / "work"
    record = json.loads((work / "graders.json").read_text())
    assert record["sha"] == sha and graders.problems({"graders": record}) == []
    assert (
        work / "graders" / "bench" / "specs" / "02-lib-roman" / "spec.json"
    ).is_file()
    assert (work / "graders" / "scripts" / "bench" / "scorecard.py").is_file()
    # A spec the graders' commit does not hold is named under the graders' tree.
    missing = _run_sh(tmp_path, "--graders", sha, "bench/specs/99-not-there")
    assert missing.returncode == 2
    assert (
        f"{work}/graders/bench/specs/99-not-there needs a spec.json" in missing.stderr
    )


def test_run_sh_refuses_graders_it_cannot_take(tmp_path):
    run = _run_sh(tmp_path, "--graders", "no-such-commit-here")
    assert run.returncode == 2
    assert "the graders could not be taken" in run.stdout + run.stderr
    assert _run_sh(tmp_path, "--graders").returncode == 2
    for args in (["--graders", ""], ["--graders="]):
        empty = _run_sh(tmp_path, *args, str(REPO / "bench" / "specs" / "02-lib-roman"))
        assert empty.returncode == 2, empty.stdout + empty.stderr
        assert "--graders requires a commit" in empty.stderr


def test_run_sh_grades_with_the_graders_and_points_the_fake_at_their_specs():
    script = (REPO / "scripts" / "bench" / "run.sh").read_text(encoding="utf-8")
    assert '"$PY" "$GRADER" grade --spec' in script
    assert 'GRADER="$WORK/graders/scripts/bench/scorecard.py"' in script
    assert 'BENCH_SPECS="$SPECS_ROOT"' in script
    assert '${GRADERS_FLAG[@]+"${GRADERS_FLAG[@]}"}' in script
