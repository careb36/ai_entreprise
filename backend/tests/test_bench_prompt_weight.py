"""The bench (issue #316): the prompts' weight on the scorecard (schema v3)
and the reference specifications as a set.

``scorecard.collect`` reads each task's latest run's ``prompt_sections``
(the backend's own measure of the Developer prompt it sent, #443) and
``prompt_weight.weigh`` sums them per spec: estimated tokens, never the
provider's, and a run without a measure counted apart, never as zero.
Version 1 and 2 cards are still read and compared; ``compare.py`` shows the
weight per section without judging it, and judges the mean only with
``--prompt-tokens``. The specifications, five now, keep their task and
requirement titles unique (the fake producer finds its answers by them), and
each fake producer passes every criterion of its own specification.
"""

import argparse
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "bench"
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import compare  # scripts/bench/compare.py, by path
import fake_llm  # scripts/bench/fake_llm.py, by path
import prompt_weight  # scripts/bench/prompt_weight.py, by path
import scorecard  # scripts/bench/scorecard.py, by path

SPEC_DIRS = sorted(
    p for p in (BENCH / "specs").iterdir() if (p / "spec.json").is_file()
)


def _section(name, tokens, cut=False, priority=2):
    return {
        "name": name,
        "chars": tokens * 4,
        "tokens": tokens,
        "priority": priority,
        "cut": cut,
    }


def _measure(*sections, system=50, budget=None):
    sent = [s for s in sections if not s["cut"]]
    return {
        "budget": budget,
        "tokens": sum(s["tokens"] for s in sent),
        "chars": sum(s["chars"] for s in sent),
        "system_tokens": system,
        "sections": list(sections),
        "cut": [s["name"] for s in sections if s["cut"]],
    }


# Three runs measured (one with a section cut, one carrying related code)
# and one from a backend before #443, with no measure.
MEASURES = [
    _measure(
        _section("output_format", 100, priority=0), _section("task", 60, priority=0)
    ),
    _measure(
        _section("output_format", 100, priority=0),
        _section("task", 40, priority=0),
        _section("related_code", 60, priority=3),
    ),
    _measure(
        _section("output_format", 100, priority=0),
        _section("task", 40, priority=0),
        _section("memory", 300, cut=True, priority=5),
        budget=200,
    ),
    None,
]


def _entry(name="01-a", prompt=None, tokens=100):
    return scorecard.summarize_spec(
        name=name,
        status="completed",
        items=[{"id": 1, "status": "done", "comments": []}],
        runs_per_task={1: 1},
        graded={"passed": 1, "total": 1, "results": {"REQ-1.1": "pass"}},
        factory_verified=1,
        wall_clock_seconds=3,
        tokens={"reported": tokens, "turns_reported": 1, "turns_unreported": 0},
        models={"Developer": ["fake-model"]},
        prompt=prompt,
    )


def _card(*entries, stamp="20260101T000000Z"):
    return scorecard.build_scorecard(
        list(entries) or [_entry()], stamp=stamp, commit="abc123", llm="fake"
    )


# ---------------------------------------------------------------------------
# Weighing the measures
# ---------------------------------------------------------------------------


def test_the_measures_are_weighed_per_section():
    weight = prompt_weight.weigh(MEASURES)
    assert weight["prompts"] == 3
    assert weight["prompt_sections_missing"] == 1
    assert weight["tokens_estimated"] == 160 + 200 + 140
    assert weight["tokens_mean"] == 166.7
    assert weight["system_tokens_estimated"] == 150
    assert weight["sections"]["output_format"] == {
        "tokens": 300,
        "prompts": 3,
        "share": 0.6,
    }
    assert weight["sections"]["task"]["tokens"] == 140
    # A section the budget cut was not sent: counted under cut, not in the weight.
    assert "memory" not in weight["sections"]
    assert (weight["cut_prompts"], weight["cut"]) == (1, {"memory": 1})
    assert weight["related_code_prompts"] == 1
    assert sum(s["share"] for s in weight["sections"].values()) == pytest.approx(1)


def test_a_run_without_a_measure_is_counted_apart_never_as_zero():
    weight = prompt_weight.weigh([None, {"tokens": "many"}, {"sections": []}])
    assert weight["prompts"] == 0
    assert weight["prompt_sections_missing"] == 3
    assert (weight["tokens_estimated"], weight["tokens_mean"]) == (0, 0.0)
    assert weight["sections"] == {}


def test_the_totals_merge_the_specs_sums():
    a, b = prompt_weight.weigh(MEASURES[:2]), prompt_weight.weigh(MEASURES[2:])
    merged = prompt_weight.merge([a, b, None])
    assert merged == prompt_weight.weigh(MEASURES)


# ---------------------------------------------------------------------------
# The card (schema v3)
# ---------------------------------------------------------------------------


def test_a_v3_card_carries_the_weight_per_spec_and_in_total():
    with_measures = _entry("01-a", prompt_weight.weigh(MEASURES))
    older_backend = _entry("02-b", prompt_weight.weigh([None, None]))
    card = _card(with_measures, older_backend)
    assert card["schema_version"] == 3
    assert scorecard.validate(card) == []
    total = card["totals"]["prompt"]
    assert (total["prompts"], total["prompt_sections_missing"]) == (3, 3)
    assert total["tokens_mean"] == 166.7
    # An entry with nothing read still has the object, all counts at zero.
    assert _entry()["prompt"] == prompt_weight.empty()


def test_the_markdown_labels_the_weight_as_estimated_tokens():
    card = _card(
        _entry("01-a", prompt_weight.weigh(MEASURES)),
        _entry("02-b", prompt_weight.weigh([None])),
    )
    text = scorecard.render_markdown(card)
    assert "## Prompt weight" in text
    assert "Estimated tokens (characters / 4)" in text
    assert "apart from the tokens the provider reported" in text
    assert "3 prompts, 167 estimated tokens per task" in text
    assert "1 with sections cut (memory ×1); 1 with related code" in text
    assert "- 02-b: 0 prompts" in text and "1 runs without a measure" in text
    assert "| output_format | 300 | 60% | 3 |" in text


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c["specs"][0].pop("prompt"), "specs[0].prompt must be an object"),
        (lambda c: c["totals"].pop("prompt"), "totals.prompt must be an object"),
        (
            lambda c: c["specs"][0]["prompt"].update(prompt_sections_missing=-1),
            "prompt_sections_missing must be a count",
        ),
        (
            lambda c: c["specs"][0]["prompt"].update(tokens_mean=float("nan")),
            "tokens_mean must be a finite number",
        ),
        (
            lambda c: c["specs"][0]["prompt"]["sections"]["task"].update(share=2),
            "sections.task needs",
        ),
        (
            lambda c: c["specs"][0]["prompt"]["cut"].update(memory="once"),
            "prompt.cut must map",
        ),
        (
            lambda c: c["specs"][0]["prompt"].update(related_code_prompts=9),
            "exceed prompts",
        ),
    ],
)
def test_the_schema_checks_the_weight(change, problem):
    card = _card(_entry(prompt=prompt_weight.weigh(MEASURES)))
    change(card)
    problems = scorecard.validate(card)
    assert any(problem in p for p in problems), problems


def _as_version(card, version):
    card = copy.deepcopy(card)
    card["schema_version"] = version
    card["totals"].pop("prompt")
    for spec in card["specs"]:
        spec.pop("prompt")
        if version == 1:
            spec.pop("routing")
    if version == 1:
        card.pop("policy")
    return card


@pytest.mark.parametrize("version", [1, 2])
def test_older_cards_are_still_read(version):
    assert scorecard.validate(_as_version(_card(), version)) == []
    assert scorecard.render_markdown(_as_version(_card(), version))


def test_the_committed_v1_reference_compares_with_a_v3_card(tmp_path, capsys):
    # The first reference kept, from before the split and the weight.
    reference = min((BENCH / "scorecards").glob("*.json"))
    old = json.loads(reference.read_text(encoding="utf-8"))
    assert old["schema_version"] == 1
    new = _card(
        *[_entry(s["name"], prompt_weight.weigh(MEASURES)) for s in old["specs"]],
        stamp="29990101T000000Z",
    )
    path = tmp_path / f"{new['stamp']}.json"
    path.write_text(json.dumps(new))
    status = compare.main([str(path), "--previous", str(reference)])
    out = capsys.readouterr().out
    assert "### Prompt weight (estimated tokens, characters / 4)" in out
    assert "| output_format | – → 600 | – → 60% |" in out
    assert "Mean per task – → 167;" in out
    assert status in (0, 1)  # judged on the v1 numbers as before, never on the weight


# ---------------------------------------------------------------------------
# compare.py: shown, and judged only with --prompt-tokens
# ---------------------------------------------------------------------------


def _heavier():
    heavier = [
        _measure(_section("output_format", 100), _section("related_code", 300))
    ] * 3
    return prompt_weight.weigh(heavier)


def test_the_weight_is_shown_before_and_after_never_judged_by_default():
    old = _card(_entry(prompt=prompt_weight.weigh(MEASURES)))
    new = _card(_entry(prompt=_heavier()), stamp="20260102T000000Z")
    result = compare.compare(old, new)
    assert result["regressions"] == []
    text = compare.render(old, new, result)
    assert "Mean per task 167 → 400" in text
    assert "with related code 1 → 3" in text
    assert "| related_code | 60 → 900 | 12% → 75% |" in text
    assert "| task | 140 → – | 28% → – |" in text


def test_the_prompt_tokens_threshold_judges_the_mean():
    old = _card(_entry(prompt=prompt_weight.weigh(MEASURES)))
    new = _card(_entry(prompt=_heavier()), stamp="20260102T000000Z")
    found = compare.compare(old, new, {"prompt_tokens": 0.2})["regressions"]
    assert "total: estimated prompt tokens per task grew from 167 to 400" in found
    assert "01-a: estimated prompt tokens per task grew from 167 to 400" in found
    assert compare.compare(old, new, {"prompt_tokens": 2.0})["regressions"] == []
    # A side with no prompt measured, or a card before v3, is never judged.
    blind = _card(_entry(prompt=prompt_weight.weigh([None])))
    assert compare.compare(blind, new, {"prompt_tokens": 0.0})["regressions"] == []
    assert (
        compare.compare(_as_version(old, 2), new, {"prompt_tokens": 0.0})["regressions"]
        == []
    )


def test_the_prompt_tokens_flag_from_the_command_line(tmp_path, capsys):
    old = _card(_entry(prompt=prompt_weight.weigh(MEASURES)))
    new = _card(_entry(prompt=_heavier()), stamp="20260102T000000Z")
    for card in (old, new):
        (tmp_path / f"{card['stamp']}.json").write_text(json.dumps(card))
    current = str(tmp_path / "20260102T000000Z.json")
    assert compare.main([current, "--dir", str(tmp_path)]) == 0
    assert (
        compare.main([current, "--dir", str(tmp_path), "--prompt-tokens", "0.2"]) == 1
    )
    assert "estimated prompt tokens per task grew" in capsys.readouterr().out
    assert compare.render_bands(old, new) == []  # no routing rows, no band table


# ---------------------------------------------------------------------------
# Collection: the measures read beside the board
# ---------------------------------------------------------------------------


def test_collect_weighs_each_tasks_latest_measure(tmp_path, monkeypatch):
    board = {
        "columns": {
            "done": [
                {"id": 11, "status": "done", "comments": []},
                {"id": 12, "status": "done", "comments": []},
                {"id": 13, "status": "done", "comments": []},
            ],
            "todo": [{"id": 14, "status": "todo", "comments": []}],
        }
    }

    def fake_get(api, path):
        if path.endswith("/board"):
            return board
        if path.startswith("/v1/agent_runs"):
            return {"agent_runs": [{"model": "fake-model"}]}
        return {"coverage": {"verified": 1}}

    monkeypatch.setattr(scorecard, "_get", fake_get)
    monkeypatch.setattr(
        scorecard,
        "reported_tokens",
        lambda dsn, company: (
            {"reported": 10, "turns_reported": 1, "turns_unreported": 0},
            {},
            {},
        ),
    )
    # No stored difficulty estimates (issue #319): the split recomputes them.
    monkeypatch.setattr(scorecard, "difficulty_estimates", lambda dsn, company: {})
    # Task 13's latest run has no measure (an older backend); task 14 has no
    # run at all; task 99 belongs to another sprint of the company.
    monkeypatch.setattr(
        scorecard,
        "prompt_sections",
        lambda dsn, company: {
            11: MEASURES[0],
            12: MEASURES[1],
            13: None,
            99: MEASURES[2],
        },
    )
    grade = tmp_path / "grade.json"
    grade.write_text(
        json.dumps({"passed": 1, "total": 1, "results": {"REQ-1.1": "pass"}})
    )
    args = argparse.Namespace(
        api="http://bench",
        name="03-x",
        status="completed",
        company=1,
        sprint=2,
        spec_id=3,
        grade=str(grade),
        wall=4.0,
        pg=["h", "5432", "u", "p", "db"],
        error="",
    )
    entry = scorecard.collect(args)
    assert entry["status"] == "completed", entry["error"]
    prompt = entry["prompt"]
    assert (prompt["prompts"], prompt["prompt_sections_missing"]) == (2, 1)
    assert prompt["tokens_estimated"] == 360
    assert prompt["related_code_prompts"] == 1 and prompt["cut_prompts"] == 0


# ---------------------------------------------------------------------------
# The reference specifications as a set
# ---------------------------------------------------------------------------


def _plan(spec_dir):
    return json.loads((spec_dir / "fake" / "plan.json").read_text(encoding="utf-8"))


def _spec(spec_dir):
    return json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))


def test_task_and_requirement_titles_are_unique_across_the_bench():
    tasks = [t["title"] for d in SPEC_DIRS for t in _plan(d)["tasks"]]
    requirements = [r["title"] for d in SPEC_DIRS for r in _spec(d)["requirements"]]
    specs = [_spec(d)["title"] for d in SPEC_DIRS]
    for titles in (tasks, requirements, specs):
        assert len(titles) == len(set(titles)), titles
    # The fake producer finds one answer per task and per QA test task.
    _plans, deliveries = fake_llm.load(BENCH / "specs")
    assert len(deliveries) == len(tasks) + len(requirements)


@pytest.mark.parametrize("spec_dir", SPEC_DIRS, ids=lambda p: p.name)
def test_the_fake_plan_is_consistent(spec_dir):
    plan = _plan(spec_dir)
    titles = [t["title"] for t in plan["tasks"]]
    for task in plan["tasks"]:
        assert set(task["depends_on"]) <= set(titles) - {task["title"]}
        assert task["assignee"] and task["files"]
    # Every criterion is covered once; a dependency comes earlier in the plan.
    covers = [c for t in plan["tasks"] for c in t["covers"]]
    assert sorted(covers) == sorted(scorecard.criteria_of(_spec(spec_dir)))
    for index, task in enumerate(plan["tasks"]):
        assert all(titles.index(d) < index for d in task["depends_on"])


@pytest.mark.parametrize(
    "spec_dir",
    [d for d in SPEC_DIRS if d.name >= "03"],
    ids=lambda p: p.name,
)
def test_the_fake_producer_passes_every_criterion(spec_dir, tmp_path):
    graded = scorecard.grade(spec_dir, spec_dir / "fake" / "files", sys.executable)
    assert graded["passed"] == graded["total"] > 0, graded["output_tail"]
    empty = scorecard.grade(spec_dir, tmp_path, sys.executable)
    assert empty["passed"] == 0, empty["results"]
