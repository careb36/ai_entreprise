"""The watch after a merge (``scripts/rsi/watch.py``, rsi-loop §1.6, REQ-19).

Refusals first: a regression must exceed both the threshold of decision 4 and
B's own spread to open a revert; a card that is fake, made before the merge,
on a commit that does not contain the merge, or graded by another commit's
graders does not count; with no counted card the watch is unmeasured, never
ok; the cycle closes after two watched nights without a regression. The
ancestry is git's word over a repository in ``tmp_path``, never the card's.
"""

import json

import pytest
from watch_helpers import (
    BEFORE_MERGE,
    NIGHT_1,
    NIGHT_2,
    SERVED,
    baseline_spec,
    card,
    cut_run,
    make_repo,
    record,
    run_watch,
    two_ok_nights,
)


@pytest.fixture
def shas(tmp_path):
    return make_repo(tmp_path)


# --- the closing of a cycle ------------------------------------------------------


def test_two_nights_without_a_regression_close_the_cycle(tmp_path, shas, capsys):
    code, out = run_watch(tmp_path, shas, record(shas), two_ok_nights(shas), capsys)
    assert (code, out["decision"]) == (0, "ok")
    assert out["nights_ok"] == 2
    assert [n["decision"] for n in out["nights"]] == ["ok", "ok"]
    assert out["reasons"][0]["code"] == "closed"


def test_one_night_alone_keeps_the_watch_open(tmp_path, shas, capsys):
    code, out = run_watch(
        tmp_path, shas, record(shas), [card(shas["after"], NIGHT_1)], capsys
    )
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "watching"
    assert "1 of 2" in out["reasons"][0]["detail"]


def test_two_cards_of_one_night_are_one_night(tmp_path, shas, capsys):
    second = card(shas["after"], NIGHT_1.replace("T021700Z", "T031800Z"))
    code, out = run_watch(
        tmp_path, shas, record(shas), [card(shas["after"], NIGHT_1), second], capsys
    )
    assert (code, out["decision"]) == (2, "unmeasured")
    assert len(out["nights"]) == 1 and out["nights_ok"] == 1


# --- the regression rule: beyond the threshold AND beyond B's spread --------------


def test_a_regression_beyond_the_threshold_and_the_spread_regresses(
    tmp_path, shas, capsys
):
    # B: criteria 0.9, spread 0.02 -> the bar is max(0.05, 0.02) = 0.05.
    bad = card(shas["after"], NIGHT_1, criteria_rate=0.5)
    code, out = run_watch(tmp_path, shas, record(shas), [bad], capsys)
    assert (code, out["decision"]) == (1, "regressed")
    night = out["nights"][0]
    assert night["decision"] == "regressed"
    details = [
        r["detail"] for r in night["cards"][0]["reasons"] if r["outcome"] == "regressed"
    ]
    assert any("criteria passed went from 0.90 to 0.50" in d for d in details)
    # the overall reasons carry the numbers too, for the revert pull request
    assert any("0.90 to 0.50" in r["detail"] for r in out["reasons"])


@pytest.mark.parametrize(
    ("criteria", "spread", "regressed"),
    [
        (0.87, 0.02, False),  # 0.03 worse: within the threshold
        (0.84, 0.02, True),  # 0.06 worse: beyond max(0.05, 0.02)
        (0.5, 0.5, False),  # 0.4 worse: within B's own spread
        (0.5, 0.02, True),  # 0.4 worse: beyond both
    ],
)
def test_a_drop_within_the_threshold_or_the_spread_is_no_regression(
    tmp_path, shas, capsys, criteria, spread, regressed
):
    rec = record(shas)
    rec["baseline"]["totals"]["criteria_rate"] = {
        "median": 0.9,
        "spread": spread,
        "runs": 3,
    }
    one = card(shas["after"], NIGHT_1, criteria_rate=criteria)
    code, out = run_watch(tmp_path, shas, rec, [one], capsys)
    assert (out["nights"][0]["decision"] == "regressed") is regressed
    assert code == (1 if regressed else 2)  # one night: never 0


def test_a_relative_metric_regresses_on_growth_beyond_threshold_and_spread(
    tmp_path, shas, capsys
):
    # tokens: B's median 100000, spread 20000 -> the bar is max(0.15, 0.2) = 0.2;
    # the total is the specifications' reported sum, as decide.py reads it
    over = with_tokens(card(shas["after"], NIGHT_1), 130000)
    _, out = run_watch(tmp_path, shas, record(shas), [over], capsys)
    assert out["nights"][0]["decision"] == "regressed"
    within = with_tokens(card(shas["after"], NIGHT_1), 115000)
    _, out = run_watch(tmp_path, shas, record(shas), [within], capsys)
    assert out["nights"][0]["decision"] == "ok"


def with_tokens(found, total):
    """*found* with its specifications reporting *total* tokens in all."""
    first = found["specs"][0]["tokens"]
    first["reported"] += total - sum(s["tokens"]["reported"] for s in found["specs"])
    found["totals"]["tokens_reported"] = total
    return found


def test_a_specification_that_stopped_completing_is_a_regression(
    tmp_path, shas, capsys
):
    rec = record(shas)
    rec["baseline"]["specs"] = {"01-cli-wordcount": baseline_spec()}
    one = card(shas["after"], NIGHT_1)
    one["specs"][0]["status"] = "timeout"
    code, out = run_watch(tmp_path, shas, rec, [one], capsys)
    assert code == 1
    reasons = out["nights"][0]["cards"][0]["reasons"]
    assert any(
        r["code"] == "completion" and "timeout now" in r["detail"] for r in reasons
    )


# --- what does not count ----------------------------------------------------------


def test_a_fake_card_does_not_count(tmp_path, shas, capsys):
    cards = two_ok_nights(shas) + [card(shas["after"], "20261118T021700Z", llm="fake")]
    code, out = run_watch(tmp_path, shas, record(shas), cards, capsys)
    assert code == 0  # the two real nights still close the cycle
    assert len(out["nights"]) == 2
    assert any("fake-model card" in e["reason"] for e in out["excluded"])


def test_a_card_from_before_the_merge_does_not_count(tmp_path, shas, capsys):
    code, out = run_watch(
        tmp_path, shas, record(shas), [card(shas["merged"], BEFORE_MERGE)], capsys
    )
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert any("not after the merge" in e["reason"] for e in out["excluded"])


def test_a_card_on_a_commit_that_does_not_contain_the_merge_does_not_count(
    tmp_path, shas, capsys
):
    # a later real-model run on a commit main does not hold: git decides
    code, out = run_watch(
        tmp_path, shas, record(shas), [card(shas["other"], NIGHT_1)], capsys
    )
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert any(
        "does not contain the merged sha" in e["reason"] for e in out["excluded"]
    )


def test_a_card_whose_commit_the_repository_does_not_hold_does_not_count(
    tmp_path, shas, capsys
):
    code, out = run_watch(
        tmp_path, shas, record(shas), [card("d" * 40, NIGHT_1)], capsys
    )
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert any("is not in the repository" in e["reason"] for e in out["excluded"])


def test_a_card_with_another_graders_sha_does_not_count(tmp_path, shas, capsys):
    one = card(shas["after"], NIGHT_1)
    one["graders"] = {"sha": "e" * 40, "trees": {"bench": "f" * 40}, "sha256": "a" * 64}
    code, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert any("another commit's graders" in e["reason"] for e in out["excluded"])


def test_a_card_with_the_cycle_graders_counts(tmp_path, shas, capsys):
    one = card(shas["after"], NIGHT_1)
    one["graders"] = {
        "sha": shas["root"],
        "trees": {"bench": "f" * 40},
        "sha256": "a" * 64,
    }
    _, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert out["excluded"] == []
    assert out["nights"][0]["decision"] == "ok"


def test_a_card_naming_another_producer_does_not_count(tmp_path, shas, capsys):
    one = card(shas["after"], NIGHT_1)
    one["produced_by"] = "candidate"
    code, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert any("not by the platform" in e["reason"] for e in out["excluded"])


def test_a_card_off_the_schema_does_not_count(tmp_path, shas, capsys):
    one = card(shas["after"], NIGHT_1)
    del one["totals"]
    code, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert any("off the schema" in e["reason"] for e in out["excluded"])


def test_with_no_valid_card_the_watch_is_unmeasured_never_ok(tmp_path, shas, capsys):
    code, out = run_watch(tmp_path, shas, record(shas), [], capsys)
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert out["reasons"][0]["code"] == "watching"


# --- a counted card that measures nothing is an unmeasured night, never ok --------


def test_a_void_pair_makes_the_night_unmeasured(tmp_path, shas, capsys):
    rec = record(shas)
    rec["models"] = {**SERVED, "QA": ["another-model"]}
    code, out = run_watch(tmp_path, shas, rec, [card(shas["after"], NIGHT_1)], capsys)
    assert code == 2
    night = out["nights"][0]
    assert night["decision"] == "unmeasured"
    assert night["cards"][0]["reasons"][0]["code"] == "void_pair"
    assert "QA" in night["cards"][0]["reasons"][0]["detail"]


def test_a_card_without_a_producer_makes_the_night_unmeasured(tmp_path, shas, capsys):
    one = card(shas["after"], NIGHT_1)
    for spec in one["specs"]:
        spec["models"] = {}
    code, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert code == 2
    night = out["nights"][0]
    assert night["decision"] == "unmeasured"
    assert night["cards"][0]["reasons"][0]["code"] == "no_producer"


def test_a_run_the_budget_cut_leaves_the_totals_unmeasured(tmp_path, shas, capsys):
    code, out = run_watch(
        tmp_path, shas, record(shas), [cut_run(card(shas["after"], NIGHT_1))], capsys
    )
    assert code == 2
    night = out["nights"][0]
    assert night["decision"] == "unmeasured"
    assert any(
        r["code"] == "not_complete" and "budget cut" in r["detail"]
        for r in night["cards"][0]["reasons"]
    )


def test_an_unmeasured_night_does_not_close_the_cycle(tmp_path, shas, capsys):
    void = card(shas["after"], NIGHT_2)
    for spec in void["specs"]:
        spec["models"] = {}
    code, out = run_watch(
        tmp_path, shas, record(shas), [card(shas["after"], NIGHT_1), void], capsys
    )
    assert (code, out["decision"], out["nights_ok"]) == (2, "unmeasured", 1)


def test_a_spec_the_card_does_not_measure_leaves_its_metric_unmeasured(
    tmp_path, shas, capsys
):
    rec = record(shas)
    rec["prompt_targeted"] = True  # the prompt's weight becomes a judged metric
    rec["baseline"]["totals"]["prompt_tokens_mean"] = {
        "median": 2000.0,
        "spread": 100.0,
        "runs": 3,
    }
    one = card(shas["after"], NIGHT_1)
    one["totals"]["prompt"]["prompts"] = 0  # no prompt measured on the night
    code, out = run_watch(tmp_path, shas, rec, [one], capsys)
    assert code == 2
    reasons = out["nights"][0]["cards"][0]["reasons"]
    assert any(r["code"] == "not_measured" for r in reasons)


# --- the observations --------------------------------------------------------------


def test_every_file_read_is_hashed_by_the_script(tmp_path, shas, capsys):
    cards = two_ok_nights(shas)
    code, out = run_watch(tmp_path, shas, record(shas), cards, capsys)
    assert code == 0
    assert len(out["inputs"]) == 1 + 2  # the record and the two cards
    assert all(len(i["sha256"]) == 64 for i in out["inputs"])


def test_the_output_names_the_cycle_and_the_night_numbers(tmp_path, shas, capsys):
    rec = record(shas)
    bad = card(shas["after"], NIGHT_1, criteria_rate=0.5)
    _, out = run_watch(tmp_path, shas, rec, [bad], capsys)
    assert out["cycle_id"] == rec["cycle_id"]
    assert out["merged_sha"] == shas["merged"]
    metric = next(
        m
        for m in out["nights"][0]["cards"][0]["metrics"]
        if m["metric"] == "criteria_rate"
    )
    assert metric["baseline"]["median"] == 0.9 and metric["value"] == 0.5
    assert metric["regressed"] is True


def test_the_command_line_exits_by_the_decision(tmp_path, shas):
    import subprocess
    import sys

    from watch_helpers import REPO

    record_path = tmp_path / "cycle.json"
    record_path.write_text(json.dumps(record(shas)), encoding="utf-8")
    card_path = tmp_path / f"{NIGHT_1}.json"
    card_path.write_text(json.dumps(card(shas["after"], NIGHT_1)), encoding="utf-8")
    done = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "rsi" / "watch.py"),
            "--cycle",
            str(record_path),
            "--repo",
            str(shas["repo"]),
            "--cards",
            str(card_path),
            "--out",
            str(tmp_path / "watch.json"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 2, done.stderr  # one night: the watch continues
    assert json.loads(done.stdout)["decision"] == "unmeasured"
    assert json.loads((tmp_path / "watch.json").read_text())["nights_ok"] == 1


# --- reachable from main, tokens as decide reads them, a closed window ------------


def test_a_branch_commit_descended_from_the_merge_does_not_count(
    tmp_path, shas, capsys
):
    # it contains the merged sha, but never reached main: a branch-only run
    cards = [card(shas["branch"], NIGHT_1), card(shas["branch"], NIGHT_2)]
    code, out = run_watch(tmp_path, shas, record(shas), cards, capsys)
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert all("not reachable from origin/main" in e["reason"] for e in out["excluded"])


def test_a_merged_sha_main_does_not_hold_is_unmeasured(tmp_path, shas, capsys):
    rec = record(shas, merged_sha=shas["branch"])
    code, out = run_watch(tmp_path, shas, rec, [], capsys)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "merged_sha_unknown"


def test_the_main_tip_that_is_not_there_counts_nothing(tmp_path, shas, capsys):
    record_path = tmp_path / "cycle.json"
    record_path.write_text(json.dumps(record(shas)), encoding="utf-8")
    from watch_helpers import watch, write_card

    paths = [str(write_card(tmp_path / "cards", c)) for c in two_ok_nights(shas)]
    code = watch.main(
        ["--cycle", str(record_path), "--repo", str(shas["repo"])]
        + ["--main-ref", "refs/remotes/origin/nowhere", "--cards", *paths]
    )
    out = json.loads(capsys.readouterr().out)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "merged_sha_unknown"


@pytest.mark.parametrize("unreported", ["one_turn", "every_turn"])
def test_tokens_a_turn_did_not_report_are_not_measured(
    tmp_path, shas, capsys, unreported
):
    # decide.py's rule: a turn without usage, or no turn with usage, leaves
    # the tokens unmeasured, whatever the card's totals say
    one = card(shas["after"], NIGHT_1)
    if unreported == "one_turn":
        one["specs"][0]["tokens"]["turns_unreported"] = 1
    else:
        for spec in one["specs"]:
            spec["tokens"] = {"reported": 0, "turns_reported": 0, "turns_unreported": 0}
    code, out = run_watch(tmp_path, shas, record(shas), [one], capsys)
    assert code == 2
    night = out["nights"][0]
    assert night["decision"] == "unmeasured"
    assert {
        (m["scope"], m["metric"])
        for m in night["cards"][0]["metrics"]
        if m["value"] is None
    } == {("total", "tokens_reported")}


def test_a_card_naming_its_commit_by_a_symbolic_name_does_not_count(
    tmp_path, shas, capsys
):
    # "HEAD" or "origin/main" always contains the merge; only a sha is checked
    cards = [card("HEAD", NIGHT_1), card("origin/main", NIGHT_2)]
    code, out = run_watch(tmp_path, shas, record(shas), cards, capsys)
    assert (code, out["decision"], out["nights"]) == (2, "unmeasured", [])
    assert all("not a full 40-character sha" in e["reason"] for e in out["excluded"])


def test_a_regression_after_two_ok_nights_does_not_reopen_the_watch(
    tmp_path, shas, capsys
):
    late = card(shas["after"], "20261118T021700Z", criteria_rate=0.5)
    code, out = run_watch(
        tmp_path, shas, record(shas), [*two_ok_nights(shas), late], capsys
    )
    assert (code, out["decision"], out["nights_ok"]) == (0, "ok", 2)
    assert [n["night"] for n in out["nights"]] == ["20261116", "20261117"]
    assert any("after the watch closed" in e["reason"] for e in out["excluded"])
