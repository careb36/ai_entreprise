"""The bench's ``compare.py`` (issue #316), and the section ablation it shows (#443).

The same numbers are no regression; a drop beyond the threshold is, a change
within it is not; a new specification is listed, not judged; the previous
scorecard is the newest earlier one, the command exits by what it found, and
the committed reference compares clean with itself.

The section ablation (``scripts/bench/ablation.py``, issues #443 and #438):
the request is read strictly (``each`` or known optional names), ``each``
runs the sections the baseline sent, each arm's delta against the baseline
is computed from the person-written grade, the board's rework requests and
the token counts over the specifications both ran in full, the block is
schema v4 and checked, and ``compare.py`` prints it without judging it.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import ablation  # scripts/bench/ablation.py, by path
import compare  # scripts/bench/compare.py, by path
import prompt_weight
import scorecard
from bench_helpers import BENCH, REPO, _card, _entry


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


def test_runs_of_different_specifications_judge_the_tokens_both_ran():
    # The nightly bench after 06-repo-shopcart was added: the totals grew
    # with the new specification's tokens, not with any regression.
    added = compare.compare(
        _card(_entry("01-a", tokens=100)),
        _card(_entry("01-a", tokens=120), _entry("02-b", tokens=500)),
    )
    assert added["regressions"] == []
    assert added["totals_judged"] is False
    text = compare.render(
        _card(_entry("01-a")), _card(_entry("01-a"), _entry("02-b")), added
    )
    assert "the totals are shown, not judged" in text
    # The tokens of the specifications both ran are still judged.
    grew = compare.compare(
        _card(_entry("01-a", tokens=100)),
        _card(_entry("01-a", tokens=200), _entry("02-b", tokens=100)),
    )
    assert grew["regressions"] == [
        "the 1 specification(s) both ran: reported tokens grew from 100 to 200"
    ]
    # The same specifications on both sides: the totals are judged as before.
    same = compare.compare(_card(_entry(tokens=100)), _card(_entry(tokens=200)))
    assert same["totals_judged"] is True
    assert any(r.startswith("total: reported tokens") for r in same["regressions"])


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
# The section ablation (#443, #438)
# ---------------------------------------------------------------------------


def _measure(**sections):
    return {
        "tokens": sum(sections.values()),
        "system_tokens": 10,
        "sections": [{"name": n, "tokens": t} for n, t in sections.items()],
    }


FULL = _measure(output_format=20, task=30, memory=40, related_code=60, lessons=0)
WITHOUT_MEMORY = _measure(output_format=20, task=30, related_code=60)


def _armed(name="01-a", measure=FULL, prompts=2, **entry):
    spec = _entry(name, **entry)
    spec["prompt"] = prompt_weight.weigh([measure] * prompts)
    return spec


def _arm_card(*entries, off=("memory",), stamp="20260102T000000Z"):
    card = _card(*entries, stamp=stamp)
    card["sections_off"] = list(off)
    return card


def test_the_section_names_are_the_backends():
    from services.loop.worker import prompt_budget

    names, required = ablation.known_sections()
    assert names == prompt_budget.SECTION_NAMES
    assert required == prompt_budget.REQUIRED_NAMES
    assert "related_code" in ablation.optional_sections()
    assert "task" not in ablation.optional_sections()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("each", ablation.EACH),
        (" EACH ", ablation.EACH),
        ("memory", ["memory"]),
        # Prompt order, trimmed, case-insensitive, without repeats.
        (
            " Memory, related_code ,lessons,memory",
            ["lessons", "related_code", "memory"],
        ),
    ],
)
def test_the_request_is_each_or_known_optional_names(text, expected):
    assert ablation.parse_request(text) == expected


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "no section"),
        (" , ", "no section"),
        ("memroy", "memroy"),
        ("task", "required"),
        ("each,memory", "each"),
    ],
)
def test_a_bad_request_is_refused_and_names_what_is_wrong(text, problem):
    # Never ignored as the backend's setting ignores it: an arm that turns
    # nothing off would be measured as if it had.
    with pytest.raises(ValueError, match=problem) as raised:
        ablation.parse_request(text)
    if problem in ("memroy", "required"):
        assert "related_code" in str(raised.value)  # The known names are listed.


def test_the_names_for_one_run_refuse_each():
    assert ablation.parse_names("wiki, memory") == ["wiki", "memory"]
    with pytest.raises(ValueError, match="each"):
        ablation.parse_names("each")


def test_each_runs_the_optional_sections_the_baseline_sent():
    baseline = _card(_armed())
    run, not_run = ablation.plan(ablation.EACH, baseline)
    # The required ones (format, task) and the weightless ones are not run.
    assert run == ["related_code", "memory"]
    assert {"section": "lessons", "reason": ablation.NO_WEIGHT} in not_run
    assert all(row["section"] not in ("task", "output_format") for row in not_run)
    assert ablation.plan(["lessons"], baseline) == (["lessons"], [])


def test_the_delta_is_the_arm_less_the_baseline_over_the_specs_run_in_full():
    baseline = _card(_armed("01-a"), _armed("02-b", rework=1, tokens=300))
    arm = _arm_card(
        _armed(
            "01-a",
            WITHOUT_MEMORY,
            results={"REQ-1.1": "pass", "REQ-1.2": "fail"},
            rework=1,
            tokens=80,
        ),
        _armed("02-b", WITHOUT_MEMORY, rework=1, tokens=250),
    )
    block = ablation.build(baseline, {"memory": arm}, ["memory"])
    assert block["request"] == "memory" and block["baseline"] == baseline["stamp"]
    row = block["arms"][0]
    assert (row["section"], row["stamp"], row["status"]) == (
        "memory",
        arm["stamp"],
        "complete",
    )
    assert row["specs"] == ["01-a", "02-b"]
    assert row["applied"] is True and row["same_models"] is True
    assert row["baseline"]["section_tokens"] == 160
    assert row["arm"]["section_tokens"] == 0
    assert row["delta"] == {
        "criteria_passed": -1,
        "rework_requests": 1,
        "rework_per_task": 0.25,
        "prompt_tokens": -160,
        "prompt_tokens_mean": -40.0,
        "tokens_reported": -70,
    }
    # The person-written grade, never the factory's own verified criteria.
    assert row["baseline"]["criteria_passed"] == 4
    assert row["arm"]["criteria_passed"] == 3
    card = ablation.attach(baseline, block)
    assert scorecard.validate(card) == []
    assert "ablation" not in baseline  # The baseline is left as it was.


def test_a_spec_stopped_or_run_on_one_side_only_is_left_out():
    baseline = _card(_armed("01-a"), _armed("02-b"), _armed("03-c"))
    arm = _arm_card(
        _armed("01-a", WITHOUT_MEMORY),
        _armed("02-b", WITHOUT_MEMORY, status="budget_exceeded"),
    )
    arm["run"] = {
        "status": "budget_exceeded",
        "budget": 10,
        "tokens_spent": 20,
        "turns_reported": 1,
        "turns_unreported": 0,
        "skipped": [{"name": "03-c", "reason": "budget"}],
    }
    row = ablation.build(baseline, {"memory": arm}, ["memory"])["arms"][0]
    assert row["specs"] == ["01-a"] and row["status"] == "budget_exceeded"
    assert row["baseline"]["tasks"] == row["arm"]["tasks"] == 2


def test_an_unmeasured_grade_leaves_the_criteria_delta_unmeasured():
    baseline = _card(_armed())
    ungraded = _armed("01-a", WITHOUT_MEMORY, results={"REQ-1.1": "missing"})
    ungraded["criteria"]["unmeasured"] = "no sandbox"
    block = ablation.build(baseline, {"memory": _arm_card(ungraded)}, ["memory"])
    row = block["arms"][0]
    # Never a drop to zero: the tests did not run.
    assert row["criteria_unmeasured"] == ["01-a"]
    assert row["delta"]["criteria_passed"] is None
    assert row["delta"]["rework_requests"] == 0


def test_an_arm_that_still_sent_the_section_is_not_applied():
    block = ablation.build(_card(_armed()), {"memory": _arm_card(_armed())}, ["memory"])
    assert block["arms"][0]["applied"] is False


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda b, a: a.update(sections_off=["wiki"]), "sections off: wiki"),
        (lambda b, a: a.pop("sections_off"), "sections off: none"),
        (lambda b, a: b.update(sections_off=["memory"]), "baseline"),
        (lambda b, a: a.update(commit="def456"), "commit"),
        (lambda b, a: a.update(llm="http://proxy"), "llm"),
        (lambda b, a: a.update(policy="cheap"), "policy"),
    ],
)
def test_cards_that_do_not_belong_together_are_refused(change, problem):
    baseline, arm = _card(_armed()), _arm_card(_armed("01-a", WITHOUT_MEMORY))
    change(baseline, arm)
    with pytest.raises(ValueError, match=problem):
        ablation.build(baseline, {"memory": arm}, ["memory"])


def test_each_needs_one_card_per_planned_section_and_no_other():
    baseline = _card(_armed())
    arm = _arm_card(_armed("01-a", WITHOUT_MEMORY))
    with pytest.raises(ValueError, match="related_code"):
        ablation.build(baseline, {"memory": arm}, ablation.EACH)
    wiki = _arm_card(_armed("01-a", WITHOUT_MEMORY), off=("wiki",))
    with pytest.raises(ValueError, match="wiki"):
        ablation.build(baseline, {"memory": arm, "wiki": wiki}, ["memory"])
    related = _arm_card(_armed("01-a", WITHOUT_MEMORY), off=("related_code",))
    block = ablation.build(baseline, {"memory": arm, "related_code": related}, "each")
    assert [row["section"] for row in block["arms"]] == ["related_code", "memory"]
    assert block["request"] == "each"
    assert {"section": "lessons", "reason": ablation.NO_WEIGHT} in block["not_run"]


def _ablation_card():
    baseline = _card(_armed())
    arm = _arm_card(_armed("01-a", WITHOUT_MEMORY, rework=1))
    block = ablation.build(baseline, {"memory": arm}, ["memory"])
    return ablation.attach(baseline, block)


def _first_arm(card):
    return card["ablation"]["arms"][0]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c.update(schema_version=3), "schema v4"),
        (lambda c: c["ablation"].update(arms="x"), "ablation.arms"),
        (lambda c: c["ablation"].update(baseline="other"), "ablation.baseline"),
        (lambda c: _first_arm(c).update(applied="yes"), "applied"),
        (lambda c: _first_arm(c).update(status="done"), "status"),
        (lambda c: _first_arm(c)["arm"].update(tasks=-1), "arm.tasks"),
        (
            lambda c: _first_arm(c)["delta"].update(rework_requests=5),
            "delta.rework_requests",
        ),
        (
            lambda c: _first_arm(c)["delta"].update(prompt_tokens=float("nan")),
            "delta.prompt_tokens",
        ),
        (lambda c: c["ablation"].update(not_run=[{"section": 1}]), "not_run"),
        (lambda c: c.update(sections_off=["memory"]), "sections_off"),
    ],
)
def test_the_schema_checks_the_ablation_block(change, problem):
    card = _ablation_card()
    change(card)
    problems = scorecard.validate(card)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (["memory"], True),
        (["memory", "memory"], False),
        ([""], False),
        ("memory", False),
    ],
)
def test_the_schema_checks_the_sections_a_run_turned_off(value, valid):
    card = _card(_armed())
    card["sections_off"] = value
    assert (scorecard.validate(card) == []) is valid


def test_a_v3_card_is_still_read_without_the_new_fields():
    old = _card(_armed())
    old["schema_version"] = 3
    assert scorecard.validate(old) == []
    old["sections_off"] = ["memory"]
    assert any("schema v4" in p for p in scorecard.validate(old))


def test_a_card_records_the_sections_its_run_turned_off(tmp_path):
    entry = tmp_path / "01-a.json"
    entry.write_text(json.dumps(_armed()))
    out = tmp_path / "cards"
    args = ["write", "--out-dir", str(out), "--stamp", "20260101T000000Z"]
    assert scorecard.main([*args, "--sections-off", "Memory", str(entry)]) == 0
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert card["schema_version"] == scorecard.SCHEMA_VERSION
    assert card["sections_off"] == ["memory"]
    assert "Sections off" in (out / "20260101T000000Z.md").read_text()
    assert scorecard.main([*args, str(entry)]) == 0
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert "sections_off" not in card
    assert scorecard.main([*args, "--sections-off", "task", str(entry)]) == 2


def test_the_markdown_and_compare_print_the_deltas_without_judging_them(
    tmp_path, capsys
):
    card = _ablation_card()
    assert "## Section ablation" in scorecard.render_markdown(card)
    lines = "\n".join(ablation.render_lines(card))
    assert "| memory | yes | 1 |" in lines
    assert "0 → 1 (+1)" in lines  # rework requests
    assert "never judged" in lines
    path = _write(tmp_path, card)
    # No previous card: the deltas are still printed, and nothing is judged.
    assert compare.main([str(path), "--dir", str(tmp_path)]) == 0
    assert "Section ablation" in capsys.readouterr().out
    previous = _write(tmp_path, _card(_armed(), stamp="19990101T000000Z"))
    assert compare.main([str(path), "--previous", str(previous)]) == 0
    out = capsys.readouterr().out
    assert "### Section ablation" in out and "0 → 1 (+1)" in out


def _write_cards(tmp_path):
    cards = {
        "baseline": _card(_armed()),
        "memory": _arm_card(_armed("01-a", WITHOUT_MEMORY)),
        "related": _arm_card(_armed("01-a", WITHOUT_MEMORY), off=("related_code",)),
    }
    paths = {}
    for name, card in cards.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(card))
    return paths


def test_the_command_writes_the_ablation_card(tmp_path, capsys):
    paths = _write_cards(tmp_path)
    plan = ["plan", "--request", "each", "--baseline", str(paths["baseline"])]
    assert ablation.main(plan) == 0
    assert capsys.readouterr().out.split() == ["related_code", "memory"]
    out = tmp_path / "out"
    head = ["write", "--baseline", str(paths["baseline"]), "--request", "each"]
    memory = ["--arm", f"memory={paths['memory']}"]
    related = ["--arm", f"related_code={paths['related']}"]
    assert ablation.main([*head, *memory, *related, "--out-dir", str(out)]) == 0
    card = json.loads((out / "20260101T000000Z.json").read_text())
    assert [row["section"] for row in card["ablation"]["arms"]] == [
        "related_code",
        "memory",
    ]
    assert "## Section ablation" in (out / "20260101T000000Z.md").read_text()
    # A missing arm is refused, nothing written.
    assert ablation.main([*head, *memory, "--out-dir", str(tmp_path / "x")]) == 2
    assert not (tmp_path / "x").exists()
    assert ablation.main(["parse", "--request", "nope"]) == 2


RUN_SH = (REPO / "scripts" / "bench" / "run.sh").read_text(encoding="utf-8")
ABLATE_SH = REPO / "scripts" / "bench" / "ablate.sh"


def test_run_sh_turns_the_named_sections_off_and_says_so_on_the_card():
    # The backend gets exactly the bench's setting (empty by default, so an
    # ambient PROMPT_SECTIONS_OFF cannot ablate a nightly run unnoticed).
    assert 'PROMPT_SECTIONS_OFF="$SECTIONS_OFF"' in RUN_SH
    assert "SECTIONS_OFF=${BENCH_SECTIONS_OFF:-}" in RUN_SH
    assert 'ablation.py" parse --names' in RUN_SH
    assert '--sections-off "$SECTIONS_OFF"' in RUN_SH


def test_ablate_sh_runs_a_baseline_then_one_arm_per_section():
    script = ABLATE_SH.read_text(encoding="utf-8")
    assert 'run_arm baseline ""' in script
    assert 'run_arm "$section" "$section"' in script
    assert "BENCH_SECTIONS_OFF=" in script
    assert 'ABLATION="$REPO/scripts/bench/ablation.py"' in script
    assert '"$ABLATION" plan' in script and '"$ABLATION" write' in script


def test_ablate_sh_refuses_a_bad_request_before_starting_anything(tmp_path):
    env = {**os.environ, "BENCH_PYTHON": sys.executable}
    for request in ("memroy", "task", ""):
        done = subprocess.run(
            ["bash", str(ABLATE_SH), "--out", str(tmp_path / "out"), request],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert done.returncode == 2, done.stdout + done.stderr
    assert not (tmp_path / "out").exists()
