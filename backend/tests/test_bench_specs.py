"""The bench's reference specifications (issue #316).

There are at least five; each is data the specification API accepts and
approves, numbered the way the backend numbers them; every criterion has a
test a person wrote, named after it and kept apart from the fake producer's
files.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import json
import sys
from pathlib import Path

import pytest
from services.planning import spec_check

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import SPEC_DIRS, TEST_DEF, _spec


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
