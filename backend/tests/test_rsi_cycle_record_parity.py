"""The backend refuses exactly the cycle records the script refuses
(``services/contracts/rsi_cycle_record.py`` beside ``scripts/rsi/cycle_record.py``;
``docs/design/rsi-loop.md``, PR 10 of section 8).

The backend imports the records ``scripts/rsi/cycle.py`` writes
(``services/management/rsi_cycle_import.py``) and checks them with the
contracts copy of the rules, given fitness's guarded metrics. This test runs
both validators over the same records, sound and broken, and fails on the
first record they answer differently: when the script's schema moves, the
contracts module must follow.
"""

from __future__ import annotations

import copy

import pytest
from services.contracts import rsi_cycle_record
from services.evidence import fitness
from watch_helpers import baseline_totals, cycle_record, summary

SHA = "a" * 40


def _sound() -> dict:
    return {
        "schema_version": 1,
        "cycle_id": "cycle-1-folder-lint",
        "merged_sha": SHA,
        "merged_at": "2026-11-15T12:00:00Z",
        "graders_sha": SHA,
        "models": {"Developer": ["fake-model"]},
        "baseline_sha": SHA,
        "finding_id": "F5",
        "specification": "folder-lint",
        "pull_request": 630,
        "prompt_targeted": False,
        "k": 3,
        "baseline": {
            "totals": {**baseline_totals(), "prompt_tokens_mean": summary(2000.0, 1.0)},
            "specs": {"01-cli-wordcount": baseline_totals()},
        },
    }


def _broken() -> list:
    cases: list = [None, [], "record", {}]
    changes = [
        ("schema_version", 2),
        ("schema_version", None),
        ("cycle_id", ""),
        ("cycle_id", "a/b"),
        ("cycle_id", "cycle..1"),
        ("cycle_id", "cycle."),
        ("cycle_id", "cycle.lock"),
        ("cycle_id", "c\n"),
        ("cycle_id", 7),
        ("merged_sha", "B" * 40),
        ("merged_sha", SHA + "\n"),
        ("graders_sha", None),
        ("merged_at", "yesterday"),
        ("merged_at", "2026-11-15T12:00:00"),
        ("models", {}),
        ("models", {"Developer": []}),
        ("models", {"D": [""]}),
        ("baseline_sha", "short"),
        ("finding_id", 5),
        ("specification", None),
        ("pull_request", 0),
        ("pull_request", True),
        ("k", "3"),
        ("prompt_targeted", "yes"),
        ("baseline", None),
        ("baseline", {}),
        ("baseline", {"totals": {}, "extra": 1}),
        ("baseline", {"totals": baseline_totals(), "specs": []}),
    ]
    for key, value in changes:
        rec = _sound()
        rec[key] = value
        cases.append(rec)
    for metric in fitness.GUARDED:
        rec = _sound()
        del rec["baseline"]["totals"][metric]
        cases.append(rec)
    for bad in (
        {"median": float("nan"), "spread": 0},
        {"median": 1, "spread": -1},
        {"median": True, "spread": 0},
        {"median": 1, "spread": 0, "runs": 0},
        {"median": 1, "spread": 0, "extra": 1},
        [1, 2],
    ):
        rec = _sound()
        rec["baseline"]["totals"]["criteria_rate"] = bad
        cases.append(rec)
    rec = _sound()
    rec["baseline"]["specs"]["01-cli-wordcount"]["unknown_metric"] = summary(1, 0)
    cases.append(rec)
    return cases


@pytest.mark.parametrize("record", [_sound(), *_broken()])
def test_the_backend_and_the_script_agree(record):
    script = cycle_record.validate(copy.deepcopy(record))
    backend = rsi_cycle_record.validate(copy.deepcopy(record), fitness.GUARDED)
    assert backend == script


def test_the_corpus_holds_one_sound_record_and_only_broken_others():
    assert cycle_record.validate(_sound()) == []
    assert all(cycle_record.validate(record) for record in _broken())


def test_both_name_the_same_metrics_and_version():
    assert tuple(cycle_record.REQUIRED_METRICS) == tuple(fitness.GUARDED)
    assert cycle_record.OPTIONAL_METRICS == rsi_cycle_record.OPTIONAL_METRICS
    assert cycle_record.SCHEMA_VERSION == rsi_cycle_record.SCHEMA_VERSION
