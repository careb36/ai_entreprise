"""The revert pull request the watch opens (``scripts/rsi/revert.py``, REQ-19),
and the nightly jobs that run it (``.github/workflows/bench.yml``).

Refusals first: only a regression beyond the threshold and B's spread opens
the revert; a night within the spread, an unmeasured watch and an invalid
record open nothing. The opener never merges, never runs twice for one cycle,
leaves a conflict to a person, and its body names the cycle with the numbers.
The git and gh commands run through an injected runner: no network in the
tests, and the token never reaches a command line, the body or the output.
"""

import json

import pytest
import yaml
from watch_helpers import (
    NIGHT_1,
    NIGHT_2,
    FakeRunner,
    card,
    make_repo,
    record,
    revert,
    run_revert,
    two_ok_nights,
)


@pytest.fixture
def shas(tmp_path):
    return make_repo(tmp_path)


def _regressed_night(shas):
    return card(shas["after"], NIGHT_1, criteria_rate=0.5)


# --- when the revert opens, and when it does not ----------------------------------


def test_a_regression_beyond_threshold_and_spread_opens_the_revert(
    tmp_path, shas, capsys
):
    runner = FakeRunner(
        {"rev-list --parents": (0, f"{shas['merged']} {'a' * 40} {'b' * 40}")}
    )
    code = run_revert(tmp_path, shas, record(shas), [_regressed_night(shas)], runner)
    capsys.readouterr()
    assert code == 0
    assert runner.ran("checkout", "-b", "rsi/revert-cycle-1-folder-lint")
    assert runner.ran("revert", "--no-edit", "-m", "1", shas["merged"])
    assert runner.ran("push", "-u", "origin", "rsi/revert-cycle-1-folder-lint")
    create = runner.call_with("pr", "create")
    assert create is not None
    assert "--base" in create and "main" in create
    assert create[create.index("--title") + 1] == revert.title(record(shas))
    assert "cycle-1-folder-lint" in create[create.index("--title") + 1]


def test_a_regression_within_the_spread_opens_nothing(tmp_path, shas, capsys):
    rec = record(shas)
    rec["baseline"]["totals"]["criteria_rate"] = {
        "median": 0.9,
        "spread": 0.5,
        "runs": 3,
    }
    nights = [
        card(shas["after"], NIGHT_1, criteria_rate=0.5),
        card(shas["after"], NIGHT_2, criteria_rate=0.5),
    ]
    runner = FakeRunner()
    code = run_revert(tmp_path, shas, rec, nights, runner)
    capsys.readouterr()
    assert code == 0  # both nights within B's spread: the cycle closes
    assert runner.calls == []  # neither git nor gh ran


def test_two_watched_nights_without_a_regression_open_nothing(tmp_path, shas, capsys):
    runner = FakeRunner()
    code = run_revert(tmp_path, shas, record(shas), two_ok_nights(shas), runner)
    capsys.readouterr()
    assert code == 0 and runner.calls == []


def test_an_unmeasured_watch_opens_nothing_and_exits_2(tmp_path, shas, capsys):
    runner = FakeRunner()
    code = run_revert(
        tmp_path, shas, record(shas), [card(shas["after"], NIGHT_1)], runner
    )
    capsys.readouterr()
    assert code == 2 and runner.calls == []  # one night: the watch continues


def test_an_invalid_record_opens_nothing(tmp_path, shas, capsys):
    rec = record(shas)
    rec["merged_sha"] = "nope"
    runner = FakeRunner()
    code = run_revert(tmp_path, shas, rec, [], runner)
    capsys.readouterr()
    assert code == 2 and runner.calls == []


def test_an_open_revert_of_the_cycle_is_not_opened_twice(tmp_path, shas, capsys):
    runner = FakeRunner({"pr list": (0, json.dumps([{"number": 7, "url": "u/7"}]))})
    code = run_revert(tmp_path, shas, record(shas), [_regressed_night(shas)], runner)
    capsys.readouterr()
    assert code == 0
    assert runner.ran("pr", "list")
    assert not runner.ran("checkout")
    assert not runner.ran("pr", "create")


def test_a_revert_that_conflicts_is_left_to_a_person(tmp_path, shas, capsys):
    runner = FakeRunner({"revert --no-edit": (1, "CONFLICT (content)")})
    code = run_revert(tmp_path, shas, record(shas), [_regressed_night(shas)], runner)
    capsys.readouterr()
    assert code == 2
    assert runner.ran("revert", "--abort")
    assert not runner.ran("push")
    assert not runner.ran("pr", "create")


def test_a_branch_that_cannot_be_created_is_left_to_a_person(tmp_path, shas, capsys):
    runner = FakeRunner({"checkout -b": (128, "fatal: a branch named 'x' exists")})
    code = run_revert(tmp_path, shas, record(shas), [_regressed_night(shas)], runner)
    capsys.readouterr()
    assert code == 2
    assert not runner.ran("revert")
    assert not runner.ran("pr", "create")


def test_a_merge_commit_reverts_its_first_parent_and_a_plain_commit_does_not(
    tmp_path, shas
):
    # the fixture's merged sha is a real merge commit
    assert revert._merge_flag(
        FakeRunner({"rev-list": (0, f"{shas['merged']} {'a' * 40} {'b' * 40}")}),
        shas["repo"],
        shas["merged"],
    ) == ["-m", "1"]
    assert (
        revert._merge_flag(
            FakeRunner({"rev-list": (0, f"{shas['after']} {'a' * 40}")}),
            shas["repo"],
            shas["after"],
        )
        == []
    )


# --- what the pull request says ----------------------------------------------------


def test_the_body_names_the_cycle_the_nights_and_the_numbers():
    rec = record({"merged": "m" * 40, "root": "r" * 40})
    result = {
        "decision": "regressed",
        "nights_ok": 0,
        "nights_required": 2,
        "nights": [
            {
                "night": "20261116",
                "decision": "regressed",
                "cards": [
                    {
                        "commit": "c" * 40,
                        "decision": "regressed",
                        "reasons": [
                            {
                                "outcome": "regressed",
                                "code": "regression",
                                "detail": (
                                    "total: criteria passed went from 0.90 to 0.50 "
                                    "(medians over 1 runs, beyond 0.05)"
                                ),
                            }
                        ],
                    }
                ],
            }
        ],
    }
    text = revert.body(rec, result)
    assert "cycle-1-folder-lint" in text
    assert "m" * 40 in text and "#630" in text
    assert "REQ-19" in text
    assert "went from 0.90 to 0.50" in text
    assert "| 20261116 | `cccccccccccc` | regressed |" in text
    assert "A person reviews and merges" in text


def test_the_title_names_the_cycle_and_the_merged_sha():
    text = revert.title(record({"merged": "m" * 40, "root": "r" * 40}))
    assert "cycle-1-folder-lint" in text and "mmmmmmmmmmmm" in text


def test_the_token_never_reaches_a_command_the_body_or_the_output(
    tmp_path, shas, capsys, monkeypatch
):
    secret = "unit-test-" + "gh-token-" + "value"
    monkeypatch.setenv("GH_TOKEN", secret)
    runner = FakeRunner(
        {"rev-list --parents": (0, f"{shas['merged']} {'a' * 40} {'b' * 40}")}
    )
    code = run_revert(tmp_path, shas, record(shas), [_regressed_night(shas)], runner)
    out = capsys.readouterr().out
    assert code == 0
    assert secret not in out
    assert all(secret not in " ".join(call) for call in runner.calls)
    body = next(i for i in runner.inputs if i)
    assert secret not in body


# --- the nightly jobs ----------------------------------------------------------------


def _workflow() -> dict:
    from watch_helpers import REPO

    return yaml.safe_load((REPO / ".github" / "workflows" / "bench.yml").read_text())


def test_the_watch_job_reads_and_the_revert_job_is_the_only_writer():
    jobs = _workflow()["jobs"]
    watch_job, revert_job = jobs["watch"], jobs["revert"]
    assert watch_job["needs"] == "bench-real"
    assert watch_job["permissions"] == {"contents": "read", "actions": "read"}
    assert revert_job["permissions"] == {
        "contents": "write",  # the revert branch, and nothing else
        "pull-requests": "write",
    }
    assert "needs.watch.outputs.regressed" in revert_job["if"]
    assert "always()" in revert_job["if"]


def test_the_watch_job_runs_the_watch_over_the_cycle_records():
    watch_job = _workflow()["jobs"]["watch"]
    assert watch_job["permissions"]["contents"] == "read"
    run = "\n".join(str(s.get("run", "")) for s in watch_job["steps"])
    assert "rsi/cycles/" in run
    assert "scripts/rsi/watch.py" in run
    assert "fetch-depth: 0" in json.dumps(watch_job) or any(
        (s.get("with") or {}).get("fetch-depth") == 0 for s in watch_job["steps"]
    )


def test_nothing_in_the_workflow_merges_the_revert():
    from watch_helpers import REPO

    text = (REPO / ".github" / "workflows" / "bench.yml").read_text()
    # The revert job opens a pull request; merging it is a person's (rule: no
    # automation merges into main, docs/design/rsi-loop.md §10, decision 1).
    assert "pr merge" not in text
    assert "pulls/merge" not in text
    assert "merge_method" not in text
