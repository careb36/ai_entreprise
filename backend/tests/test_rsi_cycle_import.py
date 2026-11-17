"""Importing a cycle runner's record (``services/management/rsi_cycle_import.py``;
``docs/design/rsi-loop.md``, PR 10 of section 8; REQ-26, REQ-27), refusals
first:

- a record that does not validate by the script's rules is refused, every
  problem named and audited;
- a kept file the backend can read whose sha256 differs from the record's
  claim is refused: the backend computes the hash, never accepts it;
- a kept file outside the root, or not there, keeps its hash as a claim only;
- a record whose merged sha is not the backend's cycle's C is refused;
- only a signed-in Owner imports: a Manager, an API token, the Manager agent.

Then a sound record: a new imported cycle, or the backend's own cycle with its
record attached, audited.
"""

from __future__ import annotations

import copy
import hashlib

import pytest
from models.rsi_cycle import RsiCycle
from rsi_cycle_helpers import (
    B,
    C,
    audits,
    hosting,
    kept,
    rsi_on,  # noqa: F401 - a fixture
)
from services.management import rsi_cycle, rsi_cycle_import
from services.platform import api_tokens
from sod_helpers import _person, auth_on  # noqa: F401 - an autouse fixture
from sqlmodel import select

pytestmark = pytest.mark.usefixtures("rsi_on")

SCORE = "bench/scorecards/real/B-1.json"
CONTENT = b'{"criteria_rate": 0.9}\n'


def _summary(median, spread):
    return {"median": median, "spread": spread, "runs": 3}


def record(**over) -> dict:
    found = {
        "schema_version": 1,
        "cycle_id": "cycle-1-folder-lint",
        "merged_sha": C,
        "merged_at": "2026-11-16T14:39:43Z",
        "graders_sha": B,
        "baseline_sha": B,
        "models": {"Developer": ["model-a"]},
        "finding_id": "F5",
        "pull_request": 630,
        "baseline": {
            "totals": {
                "criteria_rate": _summary(0.9, 0.02),
                "first_attempt_rate": _summary(0.8, 0.04),
                "blocked_share": _summary(0.1, 0.02),
                "rework_per_task": _summary(0.5, 0.1),
                "tokens_reported": _summary(100000, 20000),
                "wall_clock_seconds": _summary(1000.0, 100.0),
            }
        },
        "kept_files": [{"path": SCORE, "sha256": hashlib.sha256(CONTENT).hexdigest()}],
    }
    found.update(over)
    return found


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "repo"
    (folder / "bench/scorecards/real").mkdir(parents=True)
    (folder / SCORE).write_bytes(CONTENT)
    return folder


def _import(session, company, rec, person, root=None):
    return rsi_cycle_import.import_record(
        session, company.id, rec, person=person, files_root=root
    )


def _refused(session, company, rec, person, root=None) -> str:
    with pytest.raises(rsi_cycle.CycleRefused) as caught:
        _import(session, company, rec, person, root)
    assert caught.value.code == "bad_record"
    return caught.value.detail


# -- The refusals ------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, problem",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"cycle_id": "x..y"}, "cycle_id"),
        ({"merged_sha": "C" * 40}, "merged_sha"),
        ({"merged_at": "2026-11-16T14:39:43"}, "timezone"),
        ({"models": {}}, "models"),
        ({"baseline": {"totals": {}}}, "no baseline for"),
        ({"kept_files": [{"path": "../etc/passwd", "sha256": "0" * 64}]}, "path"),
        ({"kept_files": [{"path": SCORE, "sha256": "short"}]}, "sha256"),
        ({"kept_files": "all"}, "kept_files"),
    ],
)
def test_a_record_that_does_not_validate_is_refused(sqlite_session, change, problem):
    company = hosting(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    detail = _refused(sqlite_session, company, record(**change), owner)
    assert problem in detail
    [row] = audits(sqlite_session, company.id, "rsi_cycle_import_refused")
    assert row.agent_id == f"human:{owner.email}" and row.result["problems"]
    assert sqlite_session.exec(select(RsiCycle)).all() == []


def test_a_record_whose_file_hash_does_not_match_is_refused(sqlite_session, root):
    company = hosting(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    lying = record(kept_files=[{"path": SCORE, "sha256": "e" * 64}])
    detail = _refused(sqlite_session, company, lying, owner, root)
    assert hashlib.sha256(CONTENT).hexdigest() in detail and "e" * 64 in detail
    assert sqlite_session.exec(select(RsiCycle)).all() == []


def test_a_file_outside_the_root_keeps_its_hash_as_a_claim(
    sqlite_session, root, tmp_path
):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"other")
    (root / "link.json").symlink_to(outside)
    company = hosting(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    rec = record(
        kept_files=[
            {"path": "link.json", "sha256": "1" * 64},
            {"path": "not/there.json", "sha256": "2" * 64},
        ]
    )
    cycle = _import(sqlite_session, company, rec, owner, root)
    assert cycle.kept_files == [
        {"path": "link.json", "sha256_observed": None, "sha256_claimed": "1" * 64},
        {"path": "not/there.json", "sha256_observed": None, "sha256_claimed": "2" * 64},
    ]


def test_a_record_whose_merge_is_not_c_is_refused(sqlite_session):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    rec = record(cycle_id=cycle.cycle_key, merged_sha="e" * 40)
    assert "not the cycle's C" in _refused(sqlite_session, company, rec, owner)
    other_b = record(cycle_id=cycle.cycle_key, baseline_sha="a" * 40)
    assert "not the cycle's B" in _refused(sqlite_session, company, other_b, owner)
    sqlite_session.refresh(cycle)
    assert cycle.record is None


def test_only_a_signed_in_owner_imports(sqlite_session, test_client):
    company = hosting(sqlite_session)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    manager, manager_h = _person(sqlite_session, company.id, "Manager")
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", owner.email, [], role="Manager"
    )
    url = f"/v1/companies/{company.id}/rsi/cycles/import"
    for headers in (manager_h, {"Authorization": f"Bearer {raw}"}):
        refused = test_client.post(url, json={"record": record()}, headers=headers)
        assert refused.status_code == 403
    for who in ("Manager agent", manager):
        with pytest.raises(rsi_cycle.CycleRefused) as caught:
            _import(sqlite_session, company, record(), who)
        assert caught.value.code == "not_an_owner"
    assert sqlite_session.exec(select(RsiCycle)).all() == []


# -- What is allowed ---------------------------------------------------------------


def test_a_sound_record_is_imported_with_the_hashes_the_backend_computed(
    sqlite_session, test_client, root, monkeypatch
):
    from config import settings

    monkeypatch.setattr(settings, "RSI_FILES_ROOT", str(root))
    company = hosting(sqlite_session)
    owner, owner_h = _person(sqlite_session, company.id, "Owner")
    done = test_client.post(
        f"/v1/companies/{company.id}/rsi/cycles/import",
        json={"record": record()},
        headers=owner_h,
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["source"] == "imported" and body["status"] == "merged"
    assert body["candidate_sha"] == body["merged_sha"] == C
    assert body["merged_by"] is None  # the record names no person
    digest = hashlib.sha256(CONTENT).hexdigest()
    assert body["kept_files"] == [
        {"path": SCORE, "sha256_observed": digest, "sha256_claimed": digest}
    ]
    [row] = audits(sqlite_session, company.id, "rsi_cycle_imported")
    assert row.result["created"] is True and row.agent_id == f"human:{owner.email}"
    # An imported keep counts as a keep toward the stop.
    assert rsi_cycle.consecutive_without_keep(sqlite_session, company.id) == 0


def test_a_record_of_the_backend_s_own_cycle_is_attached(sqlite_session, root):
    company = hosting(sqlite_session)
    cycle = kept(sqlite_session, company)
    owner, _ = _person(sqlite_session, company.id, "Owner")
    rec = record(cycle_id=cycle.cycle_key)
    same = _import(sqlite_session, company, copy.deepcopy(rec), owner, root)
    assert same.id == cycle.id and same.record == rec
    assert same.status == "awaiting_merge"  # the person's merge is recorded apart
    assert same.kept_files[0]["sha256_observed"] == rec["kept_files"][0]["sha256"]
    [row] = audits(sqlite_session, company.id, "rsi_cycle_imported")
    assert row.result["created"] is False
