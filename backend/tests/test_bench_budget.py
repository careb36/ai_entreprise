"""The bench's token budget and its nightly real-model job (issue #316).

No stack here: ``scripts/bench/budget.py`` is checked with stub readers and a
stub clock. Past the budget the running sprint is stopped and the remaining
specifications are skipped, the card says ``budget_exceeded`` with the budget
and the tokens spent, unreported turns are counted apart, ``compare.py``
judges a cut run only on the specifications both cards ran in full, older
cards are still read, and ``.github/workflows/bench.yml`` keeps the real job
behind its kill switch and its keys in secrets.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import budget  # scripts/bench/budget.py, by path
import compare  # scripts/bench/compare.py, by path
import scorecard  # scripts/bench/scorecard.py, by path

WORKFLOW = REPO / ".github" / "workflows" / "bench.yml"


def _entry(name, status="completed", tokens=100, unreported=1, results=None):
    results = results or {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    return scorecard.summarize_spec(
        name=name,
        status=status,
        items=[
            {"id": 1, "status": "done", "comments": []},
            {"id": 2, "status": "done", "comments": []},
        ],
        runs_per_task={1: 1, 2: 1},
        graded={
            "passed": sum(r == "pass" for r in results.values()),
            "total": len(results),
            "results": results,
        },
        factory_verified=2,
        wall_clock_seconds=10.0,
        tokens={
            "reported": tokens,
            "turns_reported": 4,
            "turns_unreported": unreported,
        },
        models={"Developer": ["model-a"]},
    )


def _cut_card(stamp="20260102T000000Z"):
    entries = [_entry("01-a", tokens=700), _entry("02-b", "budget_exceeded", 900)]
    run = budget.run_record(entries, budget=1000, cut=True, skipped=["03-c", "04-d"])
    return scorecard.build_scorecard(
        entries, stamp=stamp, commit="abc123", llm="real", run=run
    )


def _full_card(stamp="20260101T000000Z"):
    entries = [_entry(n) for n in ("01-a", "02-b", "03-c", "04-d")]
    run = budget.run_record(entries, budget=None, cut=False, skipped=[])
    return scorecard.build_scorecard(
        entries, stamp=stamp, commit="abc123", llm="real", run=run
    )


# ---------------------------------------------------------------------------
# The budget check
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _wait(statuses, spent, *, budget_cap, timeout=600):
    clock, stops = _Clock(), []
    readings = iter(spent)
    states = iter(statuses)
    outcome = budget.wait(
        status_of=lambda: next(states, "running"),
        stop=lambda: stops.append("stop") or "cancelled",
        spent_of=lambda: next(readings),
        timeout=timeout,
        budget=budget_cap,
        interval=3,
        clock=clock,
        sleep=clock.sleep,
    )
    return outcome, stops


def _spent(reported, unreported=0):
    return {"reported": reported, "turns_reported": 1, "turns_unreported": unreported}


def test_passing_the_budget_stops_the_running_sprint():
    outcome, stops = _wait(
        ["running"] * 5, [_spent(100), _spent(900), _spent(1001)], budget_cap=1000
    )
    assert outcome["status"] == "budget_exceeded"
    assert stops == ["stop"]
    assert "1001 reported > 1000" in outcome["error"]
    assert outcome["spent"]["reported"] == 1001


def test_reaching_the_budget_exactly_is_not_passing_it():
    assert not budget.over_budget(_spent(1000), 1000)
    assert budget.over_budget(_spent(1001), 1000)
    assert not budget.over_budget(_spent(10**9), None)


def test_a_completed_sprint_is_never_stopped():
    outcome, stops = _wait(["running", "completed"], [_spent(10)], budget_cap=1000)
    assert (outcome["status"], stops) == ("completed", [])


def test_without_a_budget_only_the_timeout_stops_it():
    outcome, stops = _wait([], [], budget_cap=None, timeout=9)
    assert outcome["status"] == "timeout"
    assert stops == ["stop"]
    assert "did not close within 9s" in outcome["error"]


def test_a_budget_that_cannot_be_read_stops_the_sprint():
    def broken():
        raise OSError("no database")

    stops = []
    outcome = budget.wait(
        status_of=lambda: "running",
        stop=lambda: stops.append(1) or "cancelled",
        spent_of=broken,
        timeout=600,
        budget=10,
        sleep=lambda _s: None,
    )
    assert outcome["status"] == "error" and stops == [1]
    assert "could not be read" in outcome["error"]


def test_the_run_is_read_whole_with_the_scorecards_reader():
    calls = []

    def reader(dsn, company_id):
        calls.append(company_id)
        return {"reported": 50, "turns_reported": 2, "turns_unreported": 3}, {}, {}

    assert budget.run_spent({"host": "h"}, reader) == {
        "reported": 50,
        "turns_reported": 2,
        "turns_unreported": 3,
    }
    assert calls == [None]  # every company of the run, not one


@pytest.mark.parametrize("text", ["", "  ", None])
def test_an_empty_budget_is_no_budget(text):
    assert budget.parse_budget(text) is None


@pytest.mark.parametrize("text", ["0", "-5", "1e6", "lots"])
def test_a_budget_must_be_a_positive_integer(text):
    with pytest.raises(ValueError):
        budget.parse_budget(text)


def test_the_check_exits_3_past_the_budget(monkeypatch, capsys):
    monkeypatch.setattr(budget, "run_spent", lambda dsn: _spent(5000, 2))
    pg = ["--pg", "h", "1", "u", "p", "d"]
    assert budget.main(["check", "--budget", "4000", *pg]) == budget.EXIT_OVER
    assert json.loads(capsys.readouterr().out)["reported"] == 5000
    assert budget.main(["check", "--budget", "6000", *pg]) == 0
    assert budget.main(["check", "--budget", "", *pg]) == 0


def test_the_check_says_when_it_cannot_read(monkeypatch):
    def broken(dsn):
        raise OSError("down")

    monkeypatch.setattr(budget, "run_spent", broken)
    pg = ["--pg", "h", "1", "u", "p", "d"]
    assert budget.main(["check", "--budget", "10", *pg]) == budget.EXIT_UNREADABLE


def test_run_sh_skips_the_rest_once_the_budget_is_passed():
    script = (REPO / "scripts" / "bench" / "run.sh").read_text(encoding="utf-8")
    assert 'budget.py" wait' in script and 'budget.py" check' in script
    # Past the budget: the rest are skipped and named on the card, the run cut.
    assert 'SKIPPED+=("$(basename "$dir")")' in script
    assert "--skipped" in script and "${CUT:+--cut}" in script
    assert '[ "$SPEC_STATUS" = budget_exceeded ]' in script


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def test_the_budget_exceeded_card():
    card = _cut_card()
    assert scorecard.validate(card) == []
    assert card["schema_version"] == 3
    assert card["run"] == {
        "status": "budget_exceeded",
        "budget": 1000,
        "tokens_spent": 1600,
        "turns_reported": 8,
        "turns_unreported": 2,
        "skipped": [
            {"name": "03-c", "reason": "budget"},
            {"name": "04-d", "reason": "budget"},
        ],
    }
    # The skipped specs have no entry: never graded, never a zero.
    assert [s["name"] for s in card["specs"]] == ["01-a", "02-b"]
    assert card["totals"]["specs"] == 2
    assert card["totals"]["criteria_total"] == 4


def test_the_markdown_of_a_cut_run():
    text = scorecard.render_markdown(_cut_card())
    assert "Run cut: token budget exceeded." in text
    assert "1600 reported tokens spent against a budget of 1000" in text
    assert "| 03-c | skipped: budget | – |" in text
    assert "| 02-b | budget_exceeded |" in text
    rows = [line for line in text.splitlines() if line.startswith("| 04-d")]
    assert rows and rows[0].count("|") == 9  # as wide as the table


def test_unreported_turns_are_counted_apart_and_said():
    entries = [_entry("01-a", tokens=10, unreported=3)]
    run = budget.run_record(entries, budget=500, cut=False, skipped=[])
    assert (run["tokens_spent"], run["turns_unreported"]) == (10, 3)
    card = scorecard.build_scorecard(
        entries, stamp="20260101T000000Z", commit="c", llm="real", run=run
    )
    text = scorecard.render_markdown(card)
    assert "Run complete: 10 reported tokens, a budget of 500." in text
    assert "3 turn(s) had no usage reported" in text
    assert "could not include them" in text


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda c: c["run"].update(status="partial"), "run.status"),
        (lambda c: c["run"].update(budget=None), "run.budget is required"),
        (lambda c: c["run"].update(budget=-1), "run.budget must be"),
        (lambda c: c["run"].update(tokens_spent=-1), "run.tokens_spent"),
        (lambda c: c["run"].update(skipped=[{"name": "x"}]), "run.skipped must list"),
        (
            lambda c: c["run"]["skipped"].append({"name": "01-a", "reason": "budget"}),
            "which has an entry",
        ),
        (lambda c: c.update(run=[]), "run must be an object"),
        (
            lambda c: c["run"].update(status="complete"),
            "run.skipped must be empty",
        ),
    ],
)
def test_the_schema_checks_the_run_block(change, problem):
    card = _cut_card()
    change(card)
    problems = scorecard.validate(card)
    assert any(problem in p for p in problems), problems


def test_a_spec_stopped_for_the_budget_needs_a_cut_run():
    card = _cut_card()
    card["run"].update(status="complete", skipped=[])
    assert any(
        "needs run.status budget_exceeded" in p for p in scorecard.validate(card)
    )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_older_cards_without_a_run_block_are_still_read(version):
    card = _full_card()
    card.pop("run")
    card["schema_version"] = version
    if version < 3:
        card["totals"].pop("prompt")
        for spec in card["specs"]:
            spec.pop("prompt")
    if version == 1:
        card.pop("policy")
        for spec in card["specs"]:
            spec.pop("routing")
    assert scorecard.validate(card) == []
    assert not budget.is_cut(card)
    assert "Run cut" not in scorecard.render_markdown(card)


def test_the_committed_references_read_as_complete_runs():
    for path in sorted((REPO / "bench" / "scorecards").glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        assert scorecard.validate(card) == [], path.name
        assert not budget.is_cut(card)


# ---------------------------------------------------------------------------
# compare.py with a cut run
# ---------------------------------------------------------------------------


def test_a_cut_run_against_a_full_one_is_no_regression_in_criteria():
    result = compare.compare(_full_card(), _cut_card())
    assert result["regressions"] == []
    assert result["cut"]
    assert result["unjudged_specs"] == ["02-b"]
    assert result["skipped_specs"] == ["03-c", "04-d"]
    assert result["dropped_specs"] == []
    text = compare.render(_full_card(), _cut_card(), result)
    assert "This run was cut: token budget 1000 exceeded, 1600 reported tokens" in text
    assert "The totals are shown, not judged" in text
    assert "Skipped for the budget (not run, not graded): 03-c, 04-d" in text
    assert "| total |" in text  # its numbers are shown


def test_a_cut_run_is_still_judged_on_the_specs_both_ran_in_full():
    worse = _cut_card()
    worse["specs"][0]["criteria"] = {
        "passed": 0,
        "total": 2,
        "results": {"REQ-1.1": "fail", "REQ-1.2": "fail"},
    }
    result = compare.compare(_full_card(), worse)
    assert result["regressions"] == ["01-a: criteria passed went from 1.00 to 0.00"]


def test_a_full_run_after_a_cut_one_is_judged_on_the_common_specs():
    result = compare.compare(_cut_card(), _full_card(stamp="20260103T000000Z"))
    assert result["regressions"] == []
    assert result["unjudged_specs"] == ["02-b"]
    assert "The previous run was cut" in compare.render(
        _cut_card(), _full_card(stamp="20260103T000000Z"), result
    )


def test_the_command_exits_0_for_a_cut_run(tmp_path, capsys):
    for card in (_full_card(), _cut_card()):
        (tmp_path / f"{card['stamp']}.json").write_text(json.dumps(card))
    current = tmp_path / "20260102T000000Z.json"
    assert compare.main([str(current), "--dir", str(tmp_path)]) == 0
    assert "was cut" in capsys.readouterr().out


def test_real_cards_are_kept_apart_from_the_fake_ones(tmp_path):
    (tmp_path / "real").mkdir()
    fake = tmp_path / "20260101T000000Z.json"
    real = tmp_path / "real" / "20260102T000000Z.json"
    for path in (fake, real):
        path.write_text(json.dumps(_full_card(stamp=path.stem)))
    current = tmp_path / "29990101T000000Z.json"
    assert compare.previous_of(current, tmp_path) == fake
    assert compare.previous_of(current, tmp_path / "real") == real


def test_an_older_full_reference_compares_with_a_cut_card():
    reference = copy.deepcopy(_full_card())
    reference.pop("run")  # a card from before the budget
    result = compare.compare(reference, _cut_card())
    assert result["cut"] and result["regressions"] == []


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


def _workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = data.get("on", data.get(True))  # YAML 1.1 reads `on` as true
    return data, triggers


def test_the_real_job_is_behind_the_kill_switch():
    data, triggers = _workflow()
    real = data["jobs"]["bench-real"]
    assert "vars.BENCH_REAL_MODELS == 'true'" in real["if"]
    assert "github.event_name == 'schedule'" in real["if"]
    assert "inputs.models == 'real'" in real["if"]
    assert triggers["schedule"] and "workflow_dispatch" in triggers
    models = triggers["workflow_dispatch"]["inputs"]["models"]
    assert models["options"] == ["fake", "real"] and models["default"] == "fake"
    assert real["timeout-minutes"] <= 120
    assert "vars.BENCH_TOKEN_BUDGET" in real["env"]["BENCH_TOKEN_BUDGET"]
    assert re.search(r"\|\| '\d+'", real["env"]["BENCH_TOKEN_BUDGET"])
    assert real["env"]["BENCH_TIMEOUT"]


def test_the_real_job_reads_keys_only_from_secrets_and_never_prints_them():
    data, _ = _workflow()
    real = data["jobs"]["bench-real"]
    secret_names = set()
    for step in real["steps"]:
        assert "secrets." not in step.get("run", ""), step.get("name")
        for name, value in (step.get("env") or {}).items():
            if "secrets." in str(value):
                assert value == f"${{{{ secrets.{name} }}}}", name
                secret_names.add(name)
    assert {"GEMINI_API_KEY", "DEEPSEEK_API_KEY"} <= secret_names
    proxy = (REPO / "llm-proxy" / "app.py").read_text(encoding="utf-8")
    for name in secret_names:  # names the proxy already reads, none invented
        assert f'os.getenv("{name}"' in proxy
    printing = re.compile(
        r"\b(echo|printf|cat)\b[^\n]*\$\{?(" + "|".join(secret_names) + r")\b"
    )
    for step in real["steps"]:
        assert not printing.search(step.get("run", "")), step.get("name")
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text.split("bench-real:")[0]  # the fake job has none


def test_a_missing_key_fails_with_the_secret_name():
    data, _ = _workflow()
    proxy_step = next(
        s
        for s in data["jobs"]["bench-real"]["steps"]
        if "LLM proxy" in s.get("name", "")
    )
    run = proxy_step["run"]
    assert "for name in GEMINI_API_KEY DEEPSEEK_API_KEY" in run
    assert '"${!name}"' in run
    assert (
        "::error::the real-model bench needs the repository secret(s):$missing" in run
    )
    assert "vars.BENCH_LLM_URL" in proxy_step["env"]["PROXY_URL"]  # preferred when set


def test_the_real_job_compares_with_real_cards_and_files_its_own_issue():
    data, _ = _workflow()
    steps = {s.get("name", ""): s for s in data["jobs"]["bench-real"]["steps"]}
    compare_step = steps["Compare with the newest real-model reference"]
    assert "--dir bench/scorecards/real" in compare_step["run"]
    assert "No real-model reference" in compare_step["run"]
    issue = steps["Open an issue on a nightly real-model regression"]
    assert "github.event_name == 'schedule'" in issue["if"]
    assert "Bench regression (real models) $(date -u +%F)" in issue["run"]
    upload = steps["Keep the scorecard and the logs"]
    assert upload["with"]["name"] == "bench-real"


def test_the_fake_job_is_kept_as_it_was():
    data, _ = _workflow()
    fake = data["jobs"]["bench"]
    assert fake["if"] == "inputs.models != 'real'"
    assert fake["timeout-minutes"] == 30
    steps = {s.get("name", ""): s for s in fake["steps"]}
    run = steps["Run the bench (fake LLM)"]["run"]
    assert "BENCH_LLM_URL" not in run and "BENCH_TOKEN_BUDGET" not in json.dumps(fake)
    assert (
        "--dir bench/scorecards " in steps["Compare with the previous scorecard"]["run"]
    )
    issue = steps["Open an issue on a nightly regression"]
    assert 'gh issue create --title "Bench regression $(date -u +%F)"' in issue["run"]
    assert "github.event_name == 'schedule'" in issue["if"]
