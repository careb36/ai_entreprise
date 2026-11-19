"""The bench's scorecard (issue #316).

First attempts are counted from the board; the totals, the schema and the
Markdown summary; the committed scorecards keep to the schema; missing
observations are never green: a failed collection is an error entry, the
schema wants finite numbers and every counter, and a card off the schema is
not written or compared.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import compare  # scripts/bench/compare.py, by path
import humaneval  # scripts/bench/humaneval.py, by path
import humaneval_card  # scripts/bench/humaneval_card.py, by path
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import BENCH, _card, _comment, _entry


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
        (lambda c: c.update(schema_version=8), "schema_version"),
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
# HumanEval's block on the card (schema v5, scripts/bench/humaneval.py)
# ---------------------------------------------------------------------------


def _he_index(*numbers):
    return {
        "dataset": {
            "name": "HumanEval",
            "source": humaneval.DATASET_URL,
            "sha256": humaneval.DATASET_SHA256,
            "license": "MIT",
            "problems": 164,
        },
        "subset": [f"HumanEval/{n}" for n in numbers],
        "problems": [
            {
                "task_id": f"HumanEval/{n}",
                "spec": humaneval.spec_name(f"HumanEval/{n}"),
                "entry_point": f"f{n}",
                "fake": False,
            }
            for n in numbers
        ],
    }


def _he_entry(n, result="pass", unmeasured=None, tokens=100):
    entry = _entry(
        humaneval.spec_name(f"HumanEval/{n}"),
        results={"REQ-1.1": result},
        tokens=tokens,
    )
    entry["criteria"]["unmeasured"] = unmeasured
    return entry


def _write_he(tmp_path, entries, index, *extra):
    paths = []
    for entry in entries:
        path = tmp_path / f"{entry['name']}.json"
        path.write_text(json.dumps(entry))
        paths.append(str(path))
    (tmp_path / "problems.json").write_text(json.dumps(index))
    out = tmp_path / "cards"
    status = scorecard.main(
        [
            "write",
            "--out-dir",
            str(out),
            "--stamp",
            "20260101T000000Z",
            "--humaneval",
            str(tmp_path / "problems.json"),
            *extra,
            *paths,
        ]
    )
    return status, out


def test_humaneval_results_are_a_block_apart_from_the_specs(tmp_path):
    entries = [_he_entry(0), _he_entry(2, "fail", tokens=50), _he_entry(23)]
    status, out = _write_he(tmp_path, [*entries, _entry("01-a")], _he_index(0, 2, 23))
    assert status == 0
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert card["schema_version"] >= 5 and scorecard.validate(card) == []
    assert [s["name"] for s in card["specs"]] == ["01-a"]  # the totals stay the specs'
    block = card["humaneval"]
    assert (block["graded"], block["passed"], block["failed"]) == (3, 2, 1)
    assert block["pass_at_1"] == 0.6667
    assert [(r["task_id"], r["outcome"]) for r in block["problems"]] == [
        ("HumanEval/0", "pass"),
        ("HumanEval/2", "fail"),
        ("HumanEval/23", "pass"),
    ]
    assert block["tokens_reported"] == 250 and block["turns_unreported"] == 3
    assert block["dataset"]["sha256"] == humaneval.DATASET_SHA256
    text = (out / "20260101T000000Z.md").read_text()
    assert "## HumanEval" in text and "pass@1 67%: 2 of 3 problems passed" in text
    assert "| HumanEval/2 | f2 | fail | completed | 50 | 12 s |" in text


def test_an_unmeasured_problem_is_neither_a_pass_nor_a_fail(tmp_path):
    entries = [_he_entry(0), _he_entry(2, "missing", unmeasured="no grading image")]
    status, out = _write_he(tmp_path, entries, _he_index(0, 2))
    block = json.loads((out / "20260101T000000Z.json").read_text())["humaneval"]
    assert status == 0
    assert (block["passed"], block["failed"], block["unmeasured"]) == (1, 0, 1)
    assert block["pass_at_1"] is None
    assert block["problems"][1]["reason"] == "no grading image"
    assert "pass@1 unmeasured" in (out / "20260101T000000Z.md").read_text()


def test_problems_the_budget_skipped_are_listed_never_graded(tmp_path):
    status, out = _write_he(
        tmp_path,
        [_he_entry(0)],
        _he_index(0, 2),
        "--budget",
        "100",
        "--cut",
        "--skipped",
        "humaneval-002",
    )
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert status == 0, scorecard.validate(card)
    assert card["humaneval"]["skipped"] == ["HumanEval/2"]
    assert card["humaneval"]["graded"] == 1 and card["humaneval"]["pass_at_1"] == 1.0


def _he_card():
    card = _card(_entry("01-a"))
    index = _he_index(0, 2)
    card["humaneval"] = humaneval_card.block(
        [_he_entry(0), _he_entry(2, "fail")], index
    )
    return card


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.update(schema_version=4), "schema v5"),
        (lambda c: c["humaneval"].update(passed=2), "humaneval.passed"),
        (lambda c: c["humaneval"].update(pass_at_1=1.0), "pass_at_1"),
        (lambda c: c["humaneval"]["problems"][0].update(outcome="maybe"), "outcome"),
        (lambda c: c["humaneval"]["dataset"].update(sha256="abc"), "sha256"),
        (
            lambda c: c["humaneval"]["problems"][1].update(outcome="unmeasured"),
            "says why",
        ),
        (
            lambda c: c["specs"].append(_he_entry(0)),
            "also a reference specification",
        ),
    ],
)
def test_the_schema_checks_the_humaneval_block(change, problem):
    card = _he_card()
    assert scorecard.validate(card) == []
    change(card)
    assert any(problem in p for p in scorecard.validate(card)), scorecard.validate(card)


def test_a_v4_card_is_still_read_without_the_block():
    card = _card()
    card["schema_version"] = 4
    assert scorecard.validate(card) == []


def test_compare_prints_humaneval_before_and_after_without_judging_it(tmp_path, capsys):
    before, after = _he_card(), _he_card()
    before["stamp"], after["stamp"] = "20260101T000000Z", "20260102T000000Z"
    after["humaneval"] = humaneval_card.block(
        [_he_entry(0, "fail"), _he_entry(2, "fail")], _he_index(0, 2)
    )
    for card in (before, after):
        (tmp_path / f"{card['stamp']}.json").write_text(json.dumps(card))
    assert (
        compare.main([str(tmp_path / "20260102T000000Z.json"), "--dir", str(tmp_path)])
        == 0
    )
    printed = capsys.readouterr().out
    assert "### HumanEval" in printed and "pass@1 50% → 0%" in printed
    assert "| HumanEval/0 | pass | fail |" in printed
    # The first HumanEval card prints its block with no reference to compare.
    (tmp_path / "20260101T000000Z.json").unlink()
    assert (
        compare.main([str(tmp_path / "20260102T000000Z.json"), "--dir", str(tmp_path)])
        == 0
    )
    assert "### HumanEval" in capsys.readouterr().out


RUN_SH = BENCH.parent / "scripts" / "bench" / "run.sh"


def test_run_sh_runs_humaneval_only_when_asked_and_apart():
    script = RUN_SH.read_text(encoding="utf-8")
    assert 'humaneval.py" prepare' in script
    assert '--humaneval "$WORK/humaneval/problems.json"' in script
    assert "bench/scorecards/humaneval" in script
    # Never part of the default run: only --humaneval reaches the dataset.
    assert 'HUMANEVAL=""' in script and "--humaneval) HUMANEVAL=1" in script
    # The fake checks its own circuit on the block, not on the empty totals.
    assert 'h["passed"] == h["graded"] == len(h["subset"]) > 0' in script


@pytest.mark.parametrize(
    "args",
    [
        ["--humaneval", "bench/specs/01-cli-wordcount"],
        ["--humaneval", "--graders", "HEAD"],
        ["--humaneval=nope!"],
    ],
)
def test_run_sh_refuses_humaneval_with_specs_or_graders_before_starting(tmp_path, args):
    import subprocess

    done = subprocess.run(
        ["bash", str(RUN_SH), "--out", str(tmp_path / "out"), *args],
        cwd=BENCH.parent,
        env={
            "PATH": "/usr/bin:/bin",
            "BENCH_PYTHON": sys.executable,
            "BENCH_WORKDIR": str(tmp_path / "w"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# run.sh --swebench (issue #687, scripts/bench/swebench.py): only when asked
# ---------------------------------------------------------------------------


def test_run_sh_runs_swebench_only_when_asked_and_grades_it_with_its_own_tests():
    script = RUN_SH.read_text(encoding="utf-8")
    assert 'SWEBENCH=""' in script and "--swebench) SWEBENCH=1" in script
    assert 'swebench.py" prepare' in script and "bench/scorecards/swebench" in script
    assert (
        'swebench_grade.py" grade' in script
        and '"$dir/acceptance/instance.json"' in script
    )
    assert 's["resolved"] == s["measured"] > 0' in script


@pytest.mark.parametrize(
    ("args", "in_process"),
    [
        (["--swebench"], ""),
        (["--swebench", "bench/specs/01-cli-wordcount"], "1"),
        (["--swebench", "--graders", "HEAD"], "1"),
        (["--swebench", "--humaneval"], "1"),
        (["--swebench=nope!"], "1"),
    ],
)
def test_run_sh_refuses_swebench_before_starting(tmp_path, args, in_process):
    import subprocess

    done = subprocess.run(
        ["bash", str(RUN_SH), "--out", str(tmp_path / "out"), *args],
        cwd=BENCH.parent,
        env={
            "PATH": "/usr/bin:/bin",
            "BENCH_PYTHON": sys.executable,
            "BENCH_WORKDIR": str(tmp_path / "w"),
            "BENCH_GRADE_IN_PROCESS": in_process,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 2, done.stdout + done.stderr
    assert (
        not (tmp_path / "out").exists() and not (tmp_path / "w" / "swebench").exists()
    )
