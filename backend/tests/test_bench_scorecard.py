"""The bench (issue #316): reference specifications, the scorecard and its diff.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The reference specifications are data the
specification API accepts and approves, numbered the way the backend
numbers them; every criterion has a test a person wrote, kept apart from the
fake producer's answers; the grade reads pytest's report per criterion; the
scorecard keeps to its schema; and ``compare.py`` fails beyond a threshold.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest
from services import spec_check

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "bench"
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import broker  # scripts/bench/broker.py, by path
import compare  # scripts/bench/compare.py, by path
import scorecard  # scripts/bench/scorecard.py, by path

SPEC_DIRS = sorted(
    p for p in (BENCH / "specs").iterdir() if (p / "spec.json").is_file()
)
TEST_DEF = re.compile(r"^def (test_\w+)\(", re.MULTILINE)


def _spec(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The reference specifications
# ---------------------------------------------------------------------------


def test_there_are_at_least_two_reference_specifications():
    assert len(SPEC_DIRS) >= 2


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


JUNIT = """<testsuites><testsuite>
<testcase classname="t" name="test_req_1_1_a"/>
<testcase classname="t" name="test_req_1_1_b[x]"/>
<testcase classname="t" name="test_req_1_2_a"><failure message="no"/></testcase>
<testcase classname="t" name="test_req_2_1_a"><skipped/></testcase>
<testcase classname="t" name="test_req_2_2_a"><error message="boom"/></testcase>
<testcase classname="t" name="test_helper"/>
</testsuite></testsuites>"""


def test_the_junit_report_is_read_per_criterion():
    criteria = ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-2.2", "REQ-3.1"]
    assert scorecard.criteria_from_junit(JUNIT, criteria) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-2.1": "fail",  # a skipped test decides nothing in the criterion's favour
        "REQ-2.2": "fail",
        "REQ-3.1": "missing",
    }


@pytest.mark.parametrize("text", ["", "not xml <", "<testsuites/>"])
def test_an_unreadable_or_empty_report_leaves_every_criterion_missing(text):
    assert scorecard.criteria_from_junit(text, ["REQ-1.1"]) == {"REQ-1.1": "missing"}


def test_grading_the_fake_producers_files_passes_every_criterion():
    spec_dir = BENCH / "specs" / "02-lib-roman"
    graded = scorecard.grade(spec_dir, spec_dir / "fake" / "files", sys.executable)
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_grading_an_empty_project_fails_every_criterion(tmp_path):
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable
    )
    assert graded["passed"] == 0
    assert set(graded["results"].values()) == {"fail"}


def test_grading_a_wrong_answer_fails_only_its_criteria(tmp_path):
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    ).read_text()
    # A reader that accepts IIII: only the criterion that refuses it fails.
    broken = source.replace("or to_roman(total) != s", "")
    (tmp_path / "romannum" / "__init__.py").write_text(broken)
    graded = scorecard.grade(BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable)
    assert [c for c, r in graded["results"].items() if r != "pass"] == ["REQ-2.2"]


# ---------------------------------------------------------------------------
# The scorecard
# ---------------------------------------------------------------------------


def _comment(author, text):
    return {"author": author, "text": text, "created_at": "2026-11-08T10:00:00"}


def _entry(
    name="01-a", status="completed", results=None, runs=None, rework=0, tokens=100
):
    results = results or {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    asked = [_comment("QA", "NEEDS WORK: add a test.")] * rework
    items = [
        {"id": 1, "status": "done", "comments": asked},
        {"id": 2, "status": "done", "comments": []},
    ]
    return scorecard.summarize_spec(
        name=name,
        status=status,
        items=items,
        runs_per_task=runs or {1: 1, 2: 1},
        graded={
            "passed": sum(r == "pass" for r in results.values()),
            "total": len(results),
            "results": results,
        },
        factory_verified=2,
        wall_clock_seconds=12.34,
        tokens={"reported": tokens, "turns_reported": 4, "turns_unreported": 1},
        models={"Developer": ["fake-model", "fake-model"]},
    )


def _card(*entries, stamp="20260101T000000Z"):
    return scorecard.build_scorecard(
        list(entries) or [_entry()], stamp=stamp, commit="abc123", llm="fake"
    )


def test_a_spec_entry_counts_first_attempts_from_the_board():
    deliverable = _comment("Dev", "Delivered.")
    items = [
        # One delivery, approved: the first attempt.
        {
            "id": 1,
            "status": "done",
            "comments": [deliverable, _comment("QA", "APPROVED: fine.")],
        },
        # A quality gate sent it back before any run was ingested: one run, not first.
        {
            "id": 2,
            "status": "done",
            "comments": [
                _comment("Quality gate", "NEEDS WORK: too short."),
                deliverable,
            ],
        },
        # QA asked twice, and the factory deduplicated a repeat: still one run.
        {
            "id": 3,
            "status": "done",
            "comments": [
                _comment("QA", "NEEDS WORK: a."),
                _comment("Verifier", "NEEDS WORK: b."),
            ],
        },
        # A person sent it back to work.
        {
            "id": 4,
            "status": "done",
            "comments": [_comment("user", "Sent back to work: again.")],
        },
        {"id": 5, "status": "blocked", "comments": []},
        # Two deliveries with no request on the board are not a first attempt either.
        {"id": 6, "status": "done", "comments": []},
        # A carry-over note quotes another task's verdict, not this one's.
        {
            "id": 7,
            "status": "done",
            "comments": [_comment("system", "Carried over from task 3: NEEDS WORK: x")],
        },
    ]
    entry = scorecard.summarize_spec(
        name="x",
        status="completed",
        items=items,
        runs_per_task={1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 1},
        graded={
            "passed": 1,
            "total": 2,
            "results": {"REQ-1.1": "pass", "REQ-1.2": "fail"},
        },
        factory_verified=None,
        wall_clock_seconds=5,
        tokens={},
        models={},
    )
    assert entry["tasks"] == {
        "total": 7,
        "done": 6,
        "blocked": 1,
        "done_first_attempt": 2,
    }
    assert entry["rework_requests"] == 3
    assert (entry["first_attempt_rate"], entry["blocked_share"]) == (0.2857, 0.1429)
    assert entry["tokens"] == {
        "reported": 0,
        "turns_reported": 0,
        "turns_unreported": 0,
    }


def test_the_scorecard_totals_and_its_schema():
    card = _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"}, tokens=50))
    assert scorecard.validate(card) == []
    totals = card["totals"]
    assert (
        totals["criteria_passed"],
        totals["criteria_total"],
        totals["criteria_rate"],
    ) == (2, 3, 0.6667)
    assert (
        totals["tasks"],
        totals["done_first_attempt"],
        totals["tokens_reported"],
    ) == (4, 4, 150)
    assert card["specs"][0]["models"] == {"Developer": ["fake-model"]}
    assert json.loads(json.dumps(card)) == card


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.update(schema_version=3), "schema_version"),
        (lambda c: c.update(commit=""), "commit"),
        (lambda c: c["specs"][0].update(status="done"), "status"),
        (lambda c: c["specs"][0]["criteria"].update(passed=7), "criteria.passed"),
        (
            lambda c: c["specs"][0]["criteria"]["results"].update({"REQ-9.9": "maybe"}),
            "pass, fail, missing",
        ),
        (
            lambda c: c["specs"][0]["tasks"].update(done_first_attempt=9),
            "done_first_attempt",
        ),
        (lambda c: c["specs"][0].pop("tokens"), "tokens"),
        (lambda c: c["totals"].pop("criteria_rate"), "totals.criteria_rate"),
        (lambda c: c.update(specs={}), "specs must be a list"),
    ],
)
def test_the_schema_names_what_is_wrong(change, problem):
    card = _card()
    change(card)
    assert any(problem in p for p in scorecard.validate(card))


def test_the_markdown_summary():
    text = scorecard.render_markdown(
        _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"}))
    )
    assert "| 01-a | completed | 2/2 | 2/2 | 0.00 | 0% | 12 s | 100 |" in text
    assert "| **Total** | 2/2 completed | 2/3 |" in text
    assert "- Criteria not passing: REQ-1.1" in text


def test_the_committed_scorecards_keep_to_the_schema():
    cards = sorted((BENCH / "scorecards").glob("*.json"))
    assert cards, "a reference scorecard is committed for compare.py"
    for path in cards:
        card = json.loads(path.read_text(encoding="utf-8"))
        assert scorecard.validate(card) == [], path.name
        assert path.stem == card["stamp"]
        assert (path.with_suffix(".md")).is_file()


# ---------------------------------------------------------------------------
# compare.py
# ---------------------------------------------------------------------------


def test_the_same_numbers_are_no_regression():
    card = _card()
    assert compare.compare(card, copy.deepcopy(card))["regressions"] == []


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (
            _entry(results={"REQ-1.1": "pass", "REQ-1.2": "fail"}),
            "criteria passed went from 1.00 to 0.50",
        ),
        (_entry(runs={1: 2, 2: 1}), "done at the first attempt went from 1.00 to 0.50"),
        (_entry(rework=2), "rework per task went from 0.00 to 1.00"),
        (_entry(tokens=200), "reported tokens grew from 100 to 200"),
        (_entry(status="timeout"), "01-a: completed before, timeout now"),
    ],
)
def test_a_drop_beyond_the_threshold_is_a_regression(after, expected):
    result = compare.compare(_card(_entry()), _card(after))
    assert any(expected in r for r in result["regressions"]), result["regressions"]


def test_a_change_within_the_threshold_is_not():
    result = compare.compare(_card(_entry(tokens=100)), _card(_entry(tokens=140)))
    assert result["regressions"] == []
    loose = compare.compare(
        _card(_entry()), _card(_entry(rework=2)), {"rework": 1.5, "rate": 0.5}
    )
    assert loose["regressions"] == []


def test_a_new_specification_is_listed_not_judged():
    result = compare.compare(
        _card(_entry("01-a")),
        _card(_entry("01-a"), _entry("02-b", results={"REQ-1.1": "fail"})),
    )
    assert result["new_specs"] == ["02-b"]
    assert all("02-b" not in r for r in result["regressions"])


def _write(directory: Path, card: dict) -> Path:
    path = directory / f"{card['stamp']}.json"
    path.write_text(json.dumps(card))
    return path


def test_the_previous_scorecard_is_the_newest_earlier_one(tmp_path):
    a = _write(tmp_path, _card(stamp="20260101T000000Z"))
    b = _write(tmp_path, _card(stamp="20260102T000000Z"))
    c = _write(tmp_path, _card(stamp="20260103T000000Z"))
    assert compare.previous_of(c, tmp_path) == b
    assert compare.previous_of(b, tmp_path) == a
    assert compare.previous_of(a, tmp_path) is None


def test_the_command_exits_by_what_it_found(tmp_path, capsys):
    first = _write(tmp_path, _card(stamp="20260101T000000Z"))
    assert compare.main([str(first), "--dir", str(tmp_path)]) == 0
    assert "first reference" in capsys.readouterr().out
    same = _write(tmp_path, _card(stamp="20260102T000000Z"))
    assert compare.main([str(same), "--dir", str(tmp_path)]) == 0
    assert "No regression beyond the thresholds." in capsys.readouterr().out
    worse = _write(
        tmp_path,
        _card(
            _entry(results={"REQ-1.1": "fail", "REQ-1.2": "fail"}),
            stamp="20260103T000000Z",
        ),
    )
    assert compare.main([str(worse), "--dir", str(tmp_path)]) == 1
    assert "### Regressions" in capsys.readouterr().out
    bad = tmp_path / "20260104T000000Z.json"
    bad.write_text(json.dumps({"schema_version": 1}))
    assert compare.main([str(bad), "--dir", str(tmp_path)]) == 2


def test_the_committed_reference_compares_clean_with_itself():
    latest = max((BENCH / "scorecards").glob("*.json"))
    assert compare.main([str(latest), "--previous", str(latest)]) == 0


# ---------------------------------------------------------------------------
# By difficulty and model, and two routing policies (issue #319)
# ---------------------------------------------------------------------------

ROUTED_ITEMS = [
    {"id": 1, "status": "done", "comments": [], "covers": ["REQ-1.1"]},
    {
        "id": 2,
        "status": "blocked",
        "comments": [_comment("QA", "NEEDS WORK: a.")],
        "covers": ["REQ-1.1", "REQ-1.2", "REQ-2.1"],
    },
]


def _routed(models, reported=(10, 30), policy=None):
    return scorecard.routing_rows(
        ROUTED_ITEMS,
        {1: models[0], 2: models[1]},
        {1: 1, 2: 2},
        {
            1: [("reported", reported[0])],
            2: [("reported", reported[1]), ("estimated", 0)],
        },
        policy,
    )


def test_the_entry_splits_tasks_by_difficulty_and_model():
    rows = _routed(["cheap", "strong"], policy={"bands": {"1-3": "a", "4-5": "b"}})
    assert [(r["difficulty"], r["band"], r["model"]) for r in rows] == [
        (2, "1-3", "cheap"),
        (4, "4-5", "strong"),
    ]
    easy, hard = rows
    assert easy["done_first_attempt"] == 1 and easy["tokens"]["reported"] == 10
    assert hard["blocked_share"] == 1.0 and hard["rework_per_task"] == 1.0
    assert hard["tokens"] == {
        "reported": 30,
        "turns_reported": 1,
        "turns_unreported": 1,
    }
    entry = _entry()
    entry["routing"] = rows
    card = scorecard.build_scorecard(
        [entry], stamp="20260101T000000Z", commit="c", llm="fake", policy="bands-a"
    )
    assert card["policy"] == "bands-a" and scorecard.validate(card) == []
    text = scorecard.render_markdown(card)
    assert "routing policy `bands-a`" in text
    assert "- Difficulty 4, strong: 0/1 at the first attempt, 0 done" in text


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.pop("policy"), "policy must be a non-empty string"),
        (lambda c: c["specs"][0].pop("routing"), "routing must be a list"),
        (lambda c: c["specs"][0]["routing"][0].update(difficulty=9), "difficulty"),
        (
            lambda c: c["specs"][0]["routing"][0].update(done=5),
            "done_first_attempt <= done",
        ),
        (
            lambda c: c["specs"][0]["routing"][0]["tokens"].pop("reported"),
            "routing[0].tokens.reported must be a count",
        ),
        (
            lambda c: c["specs"][0]["routing"][0].update(blocked_share=float("nan")),
            "blocked_share must be a finite number",
        ),
    ],
)
def test_the_schema_checks_the_split(change, problem):
    entry = _entry()
    entry["routing"] = _routed(["cheap", "strong"])
    card = _card(entry)
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_a_version_1_card_is_still_read_without_the_split():
    card = _card()
    card["schema_version"] = 1
    card.pop("policy")
    for spec in card["specs"]:
        spec.pop("routing")
    assert scorecard.validate(card) == []


def test_two_policies_compare_band_by_band(tmp_path, capsys):
    before, after = _entry(), _entry()
    before["routing"] = _routed(["strong", "strong"], reported=(40, 60))
    after["routing"] = _routed(["cheap", "strong"], reported=(10, 60))
    old = _card(before, stamp="20260101T000000Z")
    old["policy"] = "all-strong"
    new = _card(after, stamp="20260102T000000Z")
    new["policy"] = "cheap-easy"
    bands = compare.by_difficulty(new)
    assert bands[2]["models"] == {"cheap"} and bands[2]["tokens"] == 10
    text = compare.render(old, new, compare.compare(old, new))
    assert "routing policy `all-strong` → `cheap-easy`" in text
    assert "### By difficulty (policy `all-strong` → `cheap-easy`)" in text
    assert (
        "| 2 | 1 → 1 | 1.00 → 1.00 | 0.00 → 0.00 | 0.00 → 0.00 | 40 → 10 | strong → cheap |"
        in text
    )
    # Written with --policy and compared from the command line.
    entries = []
    for name, entry in (("old", before), ("new", after)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(entry))
        entries.append(path)
    out = tmp_path / "cards"
    for stamp, path, policy in (
        ("20260101T000000Z", entries[0], "all-strong"),
        ("20260102T000000Z", entries[1], "cheap-easy"),
    ):
        args = ["write", "--out-dir", str(out), "--stamp", stamp, "--policy", policy]
        assert scorecard.main([*args, str(path)]) == 0
    capsys.readouterr()
    assert compare.main([str(out / "20260102T000000Z.json"), "--dir", str(out)]) == 0
    assert "### By difficulty" in capsys.readouterr().out
    # Against a card from before the split, the table shows the new side only;
    # two such cards show none.
    legacy = copy.deepcopy(old)
    legacy["specs"][0].pop("routing")
    assert "By difficulty" in compare.render(legacy, new, compare.compare(legacy, new))
    assert compare.render_bands(legacy, legacy) == []


# ---------------------------------------------------------------------------
# What the produced project cannot do to its grade
# ---------------------------------------------------------------------------

FORGED_REPORT = """import sys

# A grader impostor: writes a JUnit report where every criterion passes.
for arg in sys.argv:
    if arg.startswith("--junitxml="):
        cases = "".join(
            f'<testcase classname="x" name="test_req_{r}_{c}_forged"/>' for r in (1, 2) for c in (1, 2)
        )
        open(arg.split("=", 1)[1], "w").write(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
sys.exit(0)
"""


def test_a_project_cannot_stand_in_for_the_grader(tmp_path):
    # The old grader (project on PYTHONPATH, then `-m pytest`) ran this
    # pytest.py instead of pytest and took its report: 4 of 4.
    (tmp_path / "pytest.py").write_text(FORGED_REPORT)
    (tmp_path / "sitecustomize.py").write_text(FORGED_REPORT)
    (tmp_path / "usercustomize.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "conftest.py").write_text("raise SystemExit(0)\n")
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable
    )
    assert graded["exit_code"] == 1, graded["output_tail"]
    assert set(graded["results"].values()) == {"fail"}


def test_the_project_comes_after_the_standard_library(tmp_path):
    # A json.py in the project does not replace the one pytest's report needs.
    (tmp_path / "json.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    )
    (tmp_path / "romannum" / "__init__.py").write_text(source.read_text())
    graded = scorecard.grade(BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable)
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_a_report_pytest_did_not_finish_is_not_read(tmp_path):
    # A python that writes a passing report and exits 2 (interrupted).
    fake = tmp_path / "python"
    fake.write_text(
        f"#!{sys.executable}\n" + FORGED_REPORT.replace("sys.exit(0)", "sys.exit(2)")
    )
    fake.chmod(0o755)
    graded = scorecard.grade(BENCH / "specs" / "01-cli-wordcount", tmp_path, str(fake))
    assert graded["exit_code"] == 2
    assert set(graded["results"].values()) == {"missing"}


# ---------------------------------------------------------------------------
# Missing observations are never green
# ---------------------------------------------------------------------------


def test_a_failed_collection_marks_the_entry_error(tmp_path):
    grade_file = tmp_path / "grade.json"
    grade_file.write_text(json.dumps({"passed": 0, "total": 0, "results": {}}))
    out = tmp_path / "entry.json"
    status = scorecard.main(
        [
            "collect",
            "--api",
            "http://127.0.0.1:9",
            "--name",
            "01-a",
            "--status",
            "completed",
            "--company",
            "1",
            "--sprint",
            "2",
            "--grade",
            str(grade_file),
            "--out",
            str(out),
        ]
    )
    entry = json.loads(out.read_text())
    assert status == 0
    assert entry["status"] == "error"
    assert "collection failed" in entry["error"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda c: c["totals"].update(criteria_rate=float("nan")),
            "totals.criteria_rate must be a finite number",
        ),
        (
            lambda c: c["totals"].update(tokens_reported="many"),
            "totals.tokens_reported must be a finite number",
        ),
        (
            lambda c: c["totals"].update(blocked_share=float("inf")),
            "totals.blocked_share",
        ),
        (lambda c: c["totals"].update(tasks=True), "totals.tasks"),
        (
            lambda c: c["specs"][0]["tokens"].pop("turns_unreported"),
            "tokens.turns_unreported must be a count",
        ),
        (
            lambda c: c["specs"][0]["tokens"].update(reported=-1),
            "tokens.reported must be a count",
        ),
        (
            lambda c: c["specs"][0].update(rework_per_task=float("nan")),
            "rework_per_task must be a finite number",
        ),
        (
            lambda c: c["specs"][0]["criteria"].update(total="2"),
            "criteria needs counts",
        ),
    ],
)
def test_the_schema_wants_finite_numbers_and_every_counter(change, problem):
    card = _card()
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_writing_a_card_off_the_schema_exits_2(tmp_path):
    entry = _entry()
    entry["tokens"] = {"reported": 1}
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(entry))
    assert scorecard.main(["write", "--out-dir", str(tmp_path / "out"), str(path)]) == 2
    assert not (tmp_path / "out").exists()


def test_compare_refuses_a_card_with_nan(tmp_path):
    card = _card(stamp="20260105T000000Z")
    card["totals"]["criteria_rate"] = float("nan")
    path = tmp_path / "20260105T000000Z.json"
    path.write_text(json.dumps(card))  # json writes NaN, and reads it back
    assert compare.main([str(path), "--dir", str(tmp_path)]) == 2


# ---------------------------------------------------------------------------
# The bench's broker is its own
# ---------------------------------------------------------------------------


class _FakeNats:
    def __init__(self, answers):
        self.answers = answers
        self.closed = False

    async def request(self, subject, payload, timeout):
        assert subject == "workspace.status"
        if not self.answers:
            raise TimeoutError("no responders")
        return object()

    async def close(self):
        self.closed = True


def _connect(answers=False, reachable=True):
    async def connect(url, **kwargs):
        if not reachable:
            raise ConnectionRefusedError("refused")
        return _FakeNats(answers)

    return connect


@pytest.mark.parametrize(
    "url", ["nats://127.0.0.1:4222", "nats://localhost", "127.0.0.1:4222"]
)
def test_the_default_port_is_refused(url):
    status, message = broker.check(url, connect=_connect())
    assert status == 1
    assert "default NATS port" in message


def test_a_broker_where_a_daemon_answers_is_refused():
    status, message = broker.check(
        "nats://127.0.0.1:54222", connect=_connect(answers=True)
    )
    assert status == 1
    assert "already answers" in message


def test_a_quiet_broker_of_its_own_is_accepted():
    assert broker.check("nats://127.0.0.1:54222", connect=_connect())[0] == 0


def test_an_unreachable_broker_is_said_so():
    status, message = broker.check(
        "nats://127.0.0.1:54222", connect=_connect(reachable=False)
    )
    assert status == 2
    assert "cannot reach" in message
