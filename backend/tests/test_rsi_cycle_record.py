"""The cycle record's schema and validator (``scripts/rsi/cycle_record.py``).

The record does not exist until PR 8 (``scripts/rsi/cycle.py``) writes it, so
its schema is the contract PR 8 conforms to: refusals first — a record that
does not validate is refused by the watch, which answers ``unmeasured``
(never ``ok``) and names every problem.
"""

import json

import pytest
from watch_helpers import (
    baseline_totals,
    cycle_record,
    make_repo,
    record,
    run_watch,
    summary,
    watch,
)


@pytest.fixture
def shas(tmp_path):
    return make_repo(tmp_path)


def test_a_sound_record_validates(shas):
    assert cycle_record.validate(record(shas)) == []


def test_a_record_with_a_spec_baseline_and_every_optional_field_validates(shas):
    rec = record(
        shas,
        prompt_targeted=True,
        k=3,
        finding_id="F5",
        specification="folder-lint",
    )
    rec["baseline"]["totals"]["prompt_tokens_mean"] = summary(2000.0, 100.0)
    rec["baseline"]["specs"] = {"01-cli-wordcount": baseline_totals()}
    assert cycle_record.validate(rec) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", None),
        ("cycle_id", ""),
        ("cycle_id", "a branch/unsafe id"),
        ("cycle_id", 7),
        ("merged_sha", "abc123"),
        ("merged_sha", "B" * 40),
        ("graders_sha", "not a sha"),
        ("merged_at", "yesterday"),
        ("merged_at", "2026-11-15T12:00:00"),  # naive: no timezone named
        ("models", {}),
        ("models", {"Developer": []}),
        ("models", {"Developer": "fake-model"}),
        ("models", None),
        ("pull_request", 0),
        ("k", "3"),
        ("prompt_targeted", "yes"),
        ("baseline_sha", "short"),
        ("finding_id", 4),
    ],
    ids=str,
)
def test_a_bad_field_is_refused_and_named(shas, field, value):
    rec = record(shas)
    if value is None and field == "schema_version":
        del rec[field]
    else:
        rec[field] = value
    problems = cycle_record.validate(rec)
    assert problems and field in " ".join(problems)


def test_a_record_that_is_not_an_object_is_refused():
    assert cycle_record.validate(["not", "a", "record"]) == [
        "the record is not an object"
    ]
    assert cycle_record.validate(None)


@pytest.mark.parametrize(
    "totals",
    [
        {},  # every guarded metric missing
        {k: v for k, v in baseline_totals().items() if k != "criteria_rate"},
        {**baseline_totals(), "criteria_rate": {"median": 0.9}},  # no spread
        {**baseline_totals(), "criteria_rate": {"median": "0.9", "spread": 0.0}},
        {**baseline_totals(), "criteria_rate": {"median": 0.9, "spread": -0.01}},
        {
            **baseline_totals(),
            "criteria_rate": {"median": 0.9, "spread": 0.0, "runs": 0},
        },
        {
            **baseline_totals(),
            "criteria_rate": {"median": 0.9, "spread": 0.0, "extra": 1},
        },
        {**baseline_totals(), "tokens_spent": summary(1, 0)},  # unknown metric
    ],
)
def test_a_baseline_that_cannot_hold_the_comparison_is_refused(shas, totals):
    rec = record(shas)
    rec["baseline"]["totals"] = totals
    assert cycle_record.validate(rec)


def test_a_bad_spec_baseline_is_refused(shas):
    rec = record(shas)
    rec["baseline"]["specs"] = {
        "01-cli-wordcount": {"criteria_rate": summary(1.0, 0.0)}
    }
    assert cycle_record.validate(rec)
    rec["baseline"]["specs"] = "not an object"
    assert cycle_record.validate(rec)


def test_missing_baseline_or_totals_is_refused(shas):
    rec = record(shas)
    del rec["baseline"]["totals"]
    assert cycle_record.validate(rec)
    del rec["baseline"]
    assert cycle_record.validate(rec)


def test_the_prompt_metric_is_optional_but_checked(shas):
    rec = record(shas)
    rec["baseline"]["totals"]["prompt_tokens_mean"] = {"median": 2000.0}
    assert cycle_record.validate(rec)


# --- the watch refuses a record that does not validate -------------------------


def test_the_watch_refuses_an_invalid_record_with_exit_2(tmp_path, shas, capsys):
    rec = record(shas)
    rec["merged_sha"] = "nope"
    code, out = run_watch(tmp_path, shas, rec, [], capsys)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert [r["code"] for r in out["reasons"]] == ["invalid_record"]
    assert any("merged_sha" in r["detail"] for r in out["reasons"])


def test_the_watch_refuses_a_record_that_is_not_json(tmp_path, shas, capsys):
    record_path = tmp_path / "cycle.json"
    record_path.write_text("{not json", encoding="utf-8")
    code = watch.main(["--cycle", str(record_path), "--repo", str(shas["repo"])])
    out = json.loads(capsys.readouterr().out)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "invalid_record"
    entry = next(i for i in out["inputs"] if i["path"] == str(record_path))
    assert entry["error"].startswith("not JSON")


def test_the_watch_refuses_a_record_it_cannot_read(tmp_path, shas, capsys):
    code = watch.main(
        ["--cycle", str(tmp_path / "absent.json"), "--repo", str(shas["repo"])]
    )
    out = json.loads(capsys.readouterr().out)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "invalid_record"


def test_the_watch_refuses_a_merged_sha_the_repository_does_not_hold(
    tmp_path, shas, capsys
):
    rec = record(shas)
    rec["merged_sha"] = "d" * 40  # validates as a sha, but git does not know it
    code, out = run_watch(tmp_path, shas, rec, [], capsys)
    assert (code, out["decision"]) == (2, "unmeasured")
    assert out["reasons"][0]["code"] == "merged_sha_unknown"


def test_the_schema_version_is_the_one_the_validator_names():
    assert cycle_record.SCHEMA_VERSION == 1
    assert any(
        "schema_version" in p for p in cycle_record.validate({"schema_version": 2})
    )
