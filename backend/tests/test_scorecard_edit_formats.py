"""The /bench page's edit-format section (schema v7, issue #689):
``GET /v1/bench/scorecards`` and ``services/models/bench_scorecards.py``.

EARS criteria held here:

- WHEN a v7 card holds ``edit_formats``, its entry SHALL carry one row per
  model and format with its outcome, criteria, rework and the deliveries
  observed, well formed, malformed, off the format and refused, as written.
- WHERE a row is ``unusable``, its outcome SHALL stay ``unusable`` with the
  card's reason and SHALL NOT read as a pass, whatever its criteria.
- WHERE a row is ``unmeasured``, its outcome and criteria SHALL be ``measured:
  false`` with the card's reason, never a pass or a 0; WHERE no delivery was
  observed, the well-formed share SHALL be ``measured: false``, never 0.
- WHEN a card names the format its run asked for or its specs carry their
  deliveries' observations, the entry SHALL carry them; a card with none of
  the fields SHALL carry ``edit_formats: null``.
- IF the fields sit on a card older than v7, THEN it SHALL be ``invalid``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from config import settings
from test_scorecard_history import _card, _write


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BENCH_REPOSITORY_DIR", str(tmp_path))
    return tmp_path


def _cards_dir(root: Path) -> Path:
    return root / "bench" / "scorecards"


def _counts(observed=3, well_formed=2, missing=1) -> dict:
    return {
        "deliveries": observed + missing,
        "observed": observed,
        "missing": missing,
        "well_formed": well_formed,
        "whole_files": 2,
        "edit_blocks": 3,
        "malformed_edit_blocks": 1,
        "off_format": 0,
        "rejected_edits": 1 if well_formed < observed else 0,
        "tool_edits": 0,
        "tool_edit_errors": 0,
    }


def _row(fmt: str, outcome: str, reason=None, passed=2, **counts) -> dict:
    seen = _counts(**counts)
    return {
        "model": "fake-model",
        "format": fmt,
        "card": "20261114T201804Z",
        "served_models": ["fake-model"],
        "outcome": outcome,
        "reason": reason,
        "criteria_passed": passed,
        "criteria_total": 2,
        "rework_requests": 1,
        "tokens_reported": 1200,
        **seen,
        "well_formed_rate": round(seen["well_formed"] / seen["observed"], 4)
        if seen["observed"]
        else None,
    }


def _v7(rows=None, run_format=None, summary=None, version=7) -> dict:
    card = _card(version=version)
    if run_format is not None:
        card["edit_format"] = run_format
    if summary is not None:
        card["specs"][0]["edit_format"] = {**summary, "requested": {"whole": 3}}
    if rows is not None:
        card["edit_formats"] = {
            "formats": sorted({r["format"] for r in rows}),
            "models": ["fake-model"],
            "rows": rows,
        }
    return card


def _entry(test_client) -> dict:
    return test_client.get("/v1/bench/scorecards").json()["cards"][0]


def test_each_row_carries_its_outcome_and_deliveries(repo, test_client):
    rows = [_row("whole", "pass"), _row("search_replace", "fail", passed=1)]
    _write(_cards_dir(repo), "20261114T201804Z.json", _v7(rows, "whole", _counts()))

    entry = _entry(test_client)

    assert entry["status"] == "valid", entry["problems"]
    formats = entry["edit_formats"]
    assert formats["requested"] == "whole"
    assert formats["deliveries"]["observed"] == 3
    assert formats["deliveries"]["well_formed_rate"] == {
        "measured": True,
        "value": 0.6667,
    }
    whole, search = formats["rows"]
    assert whole["outcome"] == {"measured": True, "value": "pass"}
    assert whole["criteria"] == {"measured": True, "value": {"passed": 2, "total": 2}}
    assert (whole["observed"], whole["well_formed"], whole["rejected_edits"]) == (
        3,
        2,
        1,
    )
    assert search["outcome"] == {"measured": True, "value": "fail"}


def test_an_unusable_format_is_never_a_pass(repo, test_client):
    why = "no delivery well formed: 3 malformed edit block(s)"
    rows = [_row("search_replace", "unusable", why, well_formed=0)]
    _write(_cards_dir(repo), "20261114T201804Z.json", _v7(rows))

    row = _entry(test_client)["edit_formats"]["rows"][0]

    # Every criterion passed, and the format still reads unusable.
    assert row["criteria"]["value"] == {"passed": 2, "total": 2}
    assert row["outcome"] == {"measured": True, "value": "unusable"}
    assert row["reason"] == why
    assert row["well_formed_rate"] == {"measured": True, "value": 0.0}


def test_an_unmeasured_row_and_no_delivery_observed_are_never_zero(repo, test_client):
    rows = [
        _row("whole", "unmeasured", "the token budget cut the run", passed=0),
        _row("auto", "unmeasured", "no delivery observed", observed=0, well_formed=0),
    ]
    _write(_cards_dir(repo), "20261114T201804Z.json", _v7(rows))

    cut, unseen = _entry(test_client)["edit_formats"]["rows"]

    assert cut["outcome"] == {
        "measured": False,
        "reason": "format_unmeasured",
        "detail": "the token budget cut the run",
    }
    assert cut["criteria"] == {"measured": False, "reason": "format_unmeasured"}
    assert unseen["well_formed_rate"] == {
        "measured": False,
        "reason": "no_delivery_observed",
    }


def test_a_run_format_alone_and_cards_without_the_fields(repo, test_client):
    cards = _cards_dir(repo)
    _write(cards, "20261114T201804Z.json", _v7(run_format="search_replace"))
    _write(cards, "20261108T140751Z.json", _card("20261108T140751Z"))

    body = test_client.get("/v1/bench/scorecards").json()
    entries = {c["file"]: c for c in body["cards"]}

    assert entries["20261114T201804Z.json"]["edit_formats"] == {
        "requested": "search_replace",
        "deliveries": None,
        "rows": [],
    }
    assert entries["20261108T140751Z.json"]["edit_formats"] is None


def test_the_fields_on_an_older_card_are_invalid(repo, test_client):
    card = _v7([_row("whole", "pass")], "whole", version=6)
    _write(_cards_dir(repo), "20261114T201804Z.json", card)

    entry = _entry(test_client)

    assert entry["status"] == "invalid"
    assert entry["problems"] == [
        "edit_format needs schema v7",
        "edit_formats needs schema v7",
    ]
