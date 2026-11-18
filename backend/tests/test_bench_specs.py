"""The bench's reference specifications (issue #316).

There are at least five; each is data the specification API accepts and
approves, numbered the way the backend numbers them; every criterion has a
test a person wrote, named after it and kept apart from the fake producer's
files. A specification with a ``seed/`` folder starts from that existing
repository (``scripts/bench/seed.py``): committed as the base, its own tests
pass, it passes no criterion alone and passes every one with the fake's
delivery on top; a seed holding a link, a ``.git`` or nothing is refused.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from services.planning import spec_check

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import scorecard  # scripts/bench/scorecard.py, by path
import seed  # scripts/bench/seed.py, by path
from bench_helpers import REPO, SPEC_DIRS, TEST_DEF, _spec


def test_there_are_at_least_five_reference_specifications():
    assert len(SPEC_DIRS) >= 5


@pytest.mark.parametrize("spec_dir", SPEC_DIRS, ids=lambda p: p.name)
def test_a_reference_specification_is_approvable_data(spec_dir):
    spec = _spec(spec_dir)
    normalized = spec_check.normalize(spec)
    result = spec_check.check(normalized)
    assert result["ok"], result["findings"]
    # The grade numbers the criteria the way the backend does.
    backend_ids = [c["id"] for r in normalized["requirements"] for c in r["criteria"]]
    assert scorecard.criteria_of(spec) == backend_ids


@pytest.mark.parametrize("spec_dir", SPEC_DIRS, ids=lambda p: p.name)
def test_every_criterion_has_a_person_written_test(spec_dir):
    names = [
        name
        for path in (spec_dir / "acceptance").glob("test_*.py")
        for name in TEST_DEF.findall(path.read_text(encoding="utf-8"))
    ]
    graded = {scorecard.criterion_of_test(n) for n in names}
    assert set(scorecard.criteria_of(_spec(spec_dir))) <= graded
    assert None not in graded, "every acceptance test is named after its criterion"


@pytest.mark.parametrize("spec_dir", SPEC_DIRS, ids=lambda p: p.name)
def test_the_fake_producer_covers_the_spec_and_never_the_grading_tests(spec_dir):
    plan = json.loads((spec_dir / "fake" / "plan.json").read_text(encoding="utf-8"))
    spec = _spec(spec_dir)
    covered = {c for task in plan["tasks"] for c in task["covers"]}
    assert covered == set(scorecard.criteria_of(spec))
    assert sorted(plan["acceptance"]) == [
        f"REQ-{i}" for i in range(1, len(spec["requirements"]) + 1)
    ]
    written = [p for task in plan["tasks"] for p in task["files"]] + list(
        plan["acceptance"].values()
    )
    assert all((spec_dir / "fake" / "files" / p).is_file() for p in written)
    graders = {
        p.read_text(encoding="utf-8") for p in (spec_dir / "acceptance").glob("*.py")
    }
    assert not graders & {
        (spec_dir / "fake" / "files" / p).read_text(encoding="utf-8") for p in written
    }


# ---------------------------------------------------------------------------
# Seeded specifications: the project starts from an existing repository
# ---------------------------------------------------------------------------

SEEDED = [d for d in SPEC_DIRS if (d / seed.SEED).is_dir()]


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_at_least_one_specification_starts_from_an_existing_repository():
    assert SEEDED, "a reference specification against an existing repository"


@pytest.mark.parametrize("spec_dir", SEEDED, ids=lambda p: p.name)
def test_a_seed_is_a_committed_repository_whose_own_tests_pass(spec_dir, tmp_path):
    project = tmp_path / "project"
    result = seed.prepare(spec_dir, project)
    assert result["seeded"] is True
    assert result["base"] == _git(project, "rev-parse", "HEAD")
    assert _git(project, "status", "--porcelain") == ""
    assert sorted(_git(project, "ls-files").splitlines()) == sorted(
        p.as_posix() for p in seed.seed_files(spec_dir / seed.SEED)
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("spec_dir", SEEDED, ids=lambda p: p.name)
def test_the_seed_passes_no_criterion_and_seed_plus_fake_passes_all(spec_dir, tmp_path):
    project = tmp_path / "project"
    seed.prepare(spec_dir, project)
    before = scorecard.grade(spec_dir, project, sys.executable, in_process=True)
    assert before["passed"] == 0 and before["total"] > 0, before["results"]
    shutil.copytree(spec_dir / "fake" / "files", project, dirs_exist_ok=True)
    after = scorecard.grade(spec_dir, project, sys.executable, in_process=True)
    assert after["passed"] == after["total"], after["output_tail"]
    # The fake changes files the seed already had: the work is on existing code.
    plan = json.loads((spec_dir / "fake" / "plan.json").read_text(encoding="utf-8"))
    delivered = {p for task in plan["tasks"] for p in task["files"]}
    existing = {p.as_posix() for p in seed.seed_files(spec_dir / seed.SEED)}
    assert delivered & existing


def test_the_same_seed_gives_the_same_base_and_empties_an_earlier_project(tmp_path):
    spec_dir = tmp_path / "07-seeded"
    (spec_dir / "seed" / "pkg").mkdir(parents=True)
    (spec_dir / "seed" / "pkg" / "__init__.py").write_text("X = 1\n")
    (spec_dir / "seed" / "pkg" / "__pycache__").mkdir()
    (spec_dir / "seed" / "pkg" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"0")
    (spec_dir / "seed" / "stale.pyc").write_bytes(b"0")
    first = seed.prepare(spec_dir, tmp_path / "a")
    (tmp_path / "a" / "left-over.txt").write_text("from an earlier run")
    again = seed.prepare(spec_dir, tmp_path / "a")
    other = seed.prepare(spec_dir, tmp_path / "b")
    assert first == again == other
    assert (first["seeded"], first["files"]) == (True, 1)
    assert not (tmp_path / "a" / "left-over.txt").exists()
    assert _git(tmp_path / "a", "ls-files").splitlines() == ["pkg/__init__.py"]
    assert _git(tmp_path / "a", "log", "--format=%an <%ae>") == (
        f"{seed.IDENTITY} <{seed.IDENTITY_EMAIL}>"
    )


def test_a_specification_without_a_seed_starts_as_before(tmp_path):
    project = tmp_path / "02-lib-roman"
    (project / "old").mkdir(parents=True)
    result = seed.prepare(SPEC_DIRS[0], project)
    assert result == {"seeded": False, "base": None, "files": 1}
    assert [p.name for p in project.iterdir()] == ["README.md"]
    assert (project / "README.md").read_text() == f"# {SPEC_DIRS[0].name}\n"


@pytest.mark.parametrize("bad", ["symlink", "git", "empty", "seed-is-a-file"])
def test_a_seed_holding_what_it_may_not_is_refused(tmp_path, bad):
    spec_dir = tmp_path / "08-bad"
    spec_dir.mkdir()
    root = spec_dir / "seed"
    if bad == "seed-is-a-file":
        root.write_text("not a folder")
    else:
        root.mkdir()
        (root / "ok.py").write_text("")
    if bad == "symlink":
        (root / "link.py").symlink_to(tmp_path / "outside.py")
    elif bad == "git":
        (root / ".git").mkdir()
    elif bad == "empty":
        (root / "ok.py").unlink()
        (root / "__pycache__").mkdir()
    with pytest.raises(seed.SeedError):
        seed.prepare(spec_dir, tmp_path / "project")
    # The command line says so with exit 2, never a half-made project.
    args = ["--spec", str(spec_dir), "--project", str(tmp_path / "p2")]
    assert seed.main(args) == 2


def test_run_sh_prepares_each_project_with_the_seeder():
    script = (REPO / "scripts" / "bench" / "run.sh").read_text(encoding="utf-8")
    assert '"$REPO/scripts/bench/seed.py" --spec "$dir" --project "$project"' in script
    assert "git_flag='\"git_workflow\": false,'" in script
    assert 'echo "# $name" > "$project/README.md"' not in script


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "criterion"),
    [
        ("test_req_1_2_reads", "REQ-1.2"),
        ("test_req_10_3", "REQ-10.3"),
        ("test_req_2_1[1-I]", "REQ-2.1"),
        ("test_req_1_23_x", "REQ-1.23"),
        ("test_request_1_2", None),
        ("test_req_1", None),
        ("test_something", None),
    ],
)
def test_a_test_name_names_its_criterion(name, criterion):
    assert scorecard.criterion_of_test(name) == criterion
