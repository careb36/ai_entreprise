"""A sprint's progress from its task events (issue #531): ``progress.build``
gives the same bytes from the same events, keeps the claimed line (``delivered``
events) apart from the verified line (``verified`` events with the verdict
``verified``) and counts tasks by their latest verdict; a sprint's close
stores the series as a snapshot whose sha256 the backend computes and seals
in the audit chain, and a reopened sprint keeps its first snapshot;
``GET /v1/sprints/{id}/progress`` and ``GET /v1/specifications/{id}/progress``
are Viewer reads; the gap is a number procedures are measured against."""

from __future__ import annotations

import asyncio
import random
import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import pytest
import worker_stages
from auth_tokens import create_access_token
from config import settings
from models import (
    AgentRun,
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    Procedure,
    Specification,
    Sprint,
    SprintProgressSnapshot,
    User,
    Verification,
)
from services.learning import procedures
from services.loop.worker import close as worker_close
from services.loop.worker import procedures_round
from services.tasks import progress, progress_snapshot, task_events
from test_review_gate import _FakeNATS


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _company(session) -> Company:
    company = Company(company_name=f"Prog {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, status="active", spec_id=None) -> Sprint:
    sprint = Sprint(
        company_id=company.id,
        sprint_name="P",
        status=status,
        specification_id=spec_id,
    )
    session.add(sprint)
    session.commit()
    return sprint


def _item(session, sprint, title="T", covers=None) -> BacklogItem:
    item = BacklogItem(sprint_id=sprint.id, title=title, covers=covers)
    session.add(item)
    session.commit()
    return item


def _deliver(session, item, verdict=None, status="in_review"):
    task_events.record(
        session,
        item,
        "delivered",
        "Developer",
        author="Dev",
        text="# Artifact: a.py\nprint(1)",
        status=status,
    )
    if verdict is not None:
        task_events.record(
            session,
            item,
            "verified",
            "system",
            text=f"Delivery {verdict}",
            payload={"verification": verdict},
        )
    session.commit()


def _event(eid, task, kind, at, status=None, verdict=None):
    return {
        "id": eid,
        "task_id": task,
        "kind": kind,
        "created_at": at,
        "task_status": status,
        "payload": {"verification": verdict} if verdict else {},
    }


DAY1 = "2026-11-10T09:00:00"
DAY2 = "2026-11-11T09:00:00"
EVENTS = [
    _event(1, 1, "claimed", DAY1, "in_progress"),
    _event(2, 1, "delivered", DAY1, "in_review"),
    _event(3, 1, "verified", DAY1, verdict="verified"),
    _event(4, 2, "claimed", DAY1, "in_progress"),
    _event(5, 2, "delivered", DAY2, "in_review"),
    _event(6, 2, "verified", DAY2, verdict="refuted"),
    _event(7, 3, "delivered", DAY2, "in_review"),
    _event(8, 1, "reviewed", DAY2, "done"),
]


# ── The pure function ────────────────────────────────────────────────────────


def test_building_twice_from_the_same_events_gives_the_same_bytes():
    first = progress.canonical_bytes(progress.build(EVENTS, [1, 2, 3, 4]))
    again = progress.canonical_bytes(progress.build(list(EVENTS), [4, 3, 2, 1]))
    shuffled = list(EVENTS)
    random.Random(531).shuffle(shuffled)
    replayed = progress.canonical_bytes(progress.build(shuffled, [2, 4, 1, 3]))
    assert first == again == replayed


def test_the_claimed_and_verified_lines_stay_apart():
    series = progress.build(EVENTS, [1, 2, 3, 4], sprint_id=9)
    final = series["final"]
    # Three tasks delivered (the claim); one verified (the observation).
    assert (final["claimed"], final["verified"], final["gap"]) == (3, 1, 2)
    assert final["verification"] == {
        "verified": 1,
        "refuted": 1,
        "unverifiable": 0,
        "none": 2,
    }
    assert final["status"] == {
        "todo": 1,
        "in_progress": 0,
        "in_review": 2,
        "blocked": 0,
        "done": 1,
    }
    assert series["tasks"] == 4 and series["sprint_id"] == 9


def test_one_point_per_day_and_one_per_event():
    by_day = progress.build(EVENTS, [1, 2, 3])["points"]
    assert [p["at"] for p in by_day] == ["2026-11-10", "2026-11-11"]
    assert [(p["claimed"], p["verified"]) for p in by_day] == [(1, 1), (3, 1)]
    assert [p["events"] for p in by_day] == [4, 8]
    by_event = progress.build(EVENTS, [1, 2, 3], granularity="event")["points"]
    assert [p["event_id"] for p in by_event] == list(range(1, 9))
    with pytest.raises(ValueError):
        progress.build(EVENTS, granularity="hour")


def test_the_latest_verdict_counts_and_the_verified_line_can_drop():
    events = [
        _event(1, 1, "delivered", DAY1),
        _event(2, 1, "verified", DAY1, verdict="verified"),
        _event(3, 1, "delivered", DAY2),
        _event(4, 1, "verified", DAY2, verdict="refuted"),
        _event(5, 2, "delivered", DAY1),
        _event(6, 2, "verified", DAY1, verdict="unverifiable"),
        _event(7, 2, "verified", DAY2, verdict="verified"),
        # A verdict that is not the Verifier's leaves the task as it was.
        _event(8, 2, "verified", DAY2, verdict="looks fine"),
    ]
    points = progress.build(events)["points"]
    assert [p["verified"] for p in points] == [1, 1]
    final = progress.build(events)["final"]
    assert final["verification"]["refuted"] == 1
    assert final["verification"]["verified"] == 1
    assert (final["claimed"], final["gap"]) == (2, 1)


def test_coverage_per_requirement():
    series = progress.build(EVENTS, [1, 2, 3, 4])
    covers = {1: ["REQ-1.1"], 2: ["REQ-2"], 3: ["REQ-2.3", "REQ-3.1"], 4: ["X-9"]}
    result = progress.coverage(
        ["REQ-1", "REQ-2", "REQ-3", "REQ-4"], covers, series["by_task"]
    )
    rows = {r["requirement"]: r for r in result["requirements"]}
    assert rows["REQ-1"]["state"] == "verified" and rows["REQ-1"]["tasks"] == [1]
    assert rows["REQ-2"]["state"] == "claimed"
    assert (rows["REQ-2"]["claimed"], rows["REQ-2"]["verified"]) == (2, 0)
    assert rows["REQ-3"]["state"] == "claimed"
    assert rows["REQ-4"]["state"] == "uncovered" and rows["REQ-4"]["tasks"] == []
    assert result["summary"]["uncovered"] == 1
    assert result["summary"]["verified"] == 1


# ── From the database ────────────────────────────────────────────────────────


def test_the_live_series_reads_the_events_written_by_the_task_record(db):
    company = _company(db)
    sprint = _sprint(db, company)
    a, b, _c = _item(db, sprint), _item(db, sprint), _item(db, sprint)
    _deliver(db, a, "verified")
    _deliver(db, b, "refuted")

    series = progress_snapshot.live(db, sprint.id)
    assert (series["final"]["claimed"], series["final"]["verified"]) == (2, 1)
    assert series["final"]["verification"]["none"] == 1
    # The rows and their as_dict copies give the same bytes.
    rows = progress_snapshot.events_of(db, [sprint.id])[sprint.id]
    as_dicts = [task_events.as_dict(e) for e in rows]
    ids = [a.id, b.id, _c.id]
    assert progress.canonical_bytes(
        progress.build(rows, ids, sprint_id=sprint.id)
    ) == progress.canonical_bytes(progress.build(as_dicts, ids, sprint_id=sprint.id))


def test_an_older_verified_event_takes_the_verdict_of_its_run(db):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer="task_worker",
        artifact_text="x",
        artifact_digest="0" * 64,
        artifact_size=1,
    )
    db.add(run)
    db.commit()
    db.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            observed_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
        )
    )
    db.commit()
    _deliver(db, item)
    # Written before #531: no verdict in the payload, only the run.
    task_events.record(db, item, "verified", "system", text="Delivered", run_id=run.id)
    db.commit()
    # A later verification of the same run does not rewrite the past.
    db.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="refuted",
            observed_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
        )
    )
    db.commit()

    assert progress_snapshot.live(db, sprint.id)["final"]["verified"] == 1


def _close(db, sprint):
    sprint.status = "completed"
    db.add(sprint)
    db.commit()


def test_a_close_seals_the_snapshot_in_the_audit_chain(db):
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    _deliver(db, item, "verified", status="done")
    _close(db, sprint)

    snapshot = progress_snapshot.seal_at_close(
        db.get_bind(), sprint.id, {"total_tasks": 1, "done_tasks": 1, "note": "x"}
    )
    assert snapshot is not None and snapshot.close_number == 1
    assert snapshot.retrospective == {"total_tasks": 1, "done_tasks": 1}
    # The server computed the digest from the stored content.
    body = progress_snapshot.snapshot_body(
        company.id, sprint.id, 1, snapshot.series, snapshot.retrospective
    )
    assert snapshot.digest == progress_snapshot.digest_of(body)
    assert snapshot.digest.startswith("sha256:")
    row = db.get(AuditLog, snapshot.audit_log_id)
    assert row.action == "sprint_progress_sealed"
    assert row.interaction_id == f"sprint:{sprint.id}:progress"
    assert row.result["digest"] == snapshot.digest and row.result["gap"] == 0
    assert progress_snapshot.verify(db, snapshot) == {
        "digest": "match",
        "audit": "match",
        "chain": "intact",
        "intact": True,
    }

    # Altered after the close: the digest no longer matches.
    stored = db.get(SprintProgressSnapshot, snapshot.id)
    stored.series = {**stored.series, "tasks": 99}
    db.add(stored)
    db.commit()
    checked = progress_snapshot.verify(db, stored)
    assert checked["digest"] == "mismatch" and checked["intact"] is False


def test_no_snapshot_for_a_sprint_that_did_not_close(db):
    sprint = _sprint(db, _company(db))
    assert progress_snapshot.seal_at_close(db.get_bind(), sprint.id, {}) is None


def test_a_reopened_sprint_keeps_its_first_snapshot_and_adds_one(db, monkeypatch):
    monkeypatch.setattr(procedures_round, "_propose_procedures", lambda *a: None)
    monkeypatch.setattr(procedures_round, "_evaluate_procedures", lambda *a: None)
    monkeypatch.setattr(worker_close, "_burn_retrospective_engram", lambda *a: None)
    company = _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    _deliver(db, item, "refuted", status="done")

    asyncio.run(worker_close._finalize_sprint_if_ready(_FakeNATS(), sprint.id))
    db.expire_all()
    assert db.get(Sprint, sprint.id).status == "completed"
    [first] = progress_snapshot.snapshots_of(db, sprint.id)
    first_digest, first_series = first.digest, dict(first.series)
    assert first.series["final"]["gap"] == 1
    assert first.retrospective["total_tasks"] == 1

    # A person reopens it; the task is verified this time; it closes again.
    reopened = db.get(Sprint, sprint.id)
    reopened.status = "active"
    db.add(reopened)
    db.commit()
    _deliver(db, db.get(BacklogItem, item.id), "verified", status="done")
    asyncio.run(worker_close._finalize_sprint_if_ready(_FakeNATS(), sprint.id))
    db.expire_all()

    snapshots = progress_snapshot.snapshots_of(db, sprint.id)
    assert [s.close_number for s in snapshots] == [1, 2]
    assert snapshots[0].digest == first_digest
    assert snapshots[0].series == first_series
    assert snapshots[1].series["final"]["gap"] == 0
    assert all(progress_snapshot.verify(db, s)["intact"] for s in snapshots)


# ── The gap as a procedure's number ──────────────────────────────────────────


def test_the_gap_is_a_board_metric_from_the_first_snapshot(db):
    company = _company(db)
    sprint = _sprint(db, company)
    a, b = _item(db, sprint), _item(db, sprint)
    _deliver(db, a, "verified", status="done")
    _deliver(db, b, "unverifiable", status="done")
    assert procedures.board_metrics(db, [sprint.id])["claim_gap_rate"] == 0.5

    _close(db, sprint)
    progress_snapshot.seal_at_close(db.get_bind(), sprint.id, {})
    # Events after the close do not change the sealed number.
    task_events.record(
        db,
        db.get(BacklogItem, b.id),
        "verified",
        "system",
        text="v",
        payload={"verification": "verified"},
    )
    db.commit()
    assert procedures.board_metrics(db, [sprint.id])["claim_gap_rate"] == 0.5
    assert progress_snapshot.claim_gap(db, [sprint.id])["gap"] == 1


def test_a_procedure_under_which_the_gap_grew_is_reverted(db):
    company = _company(db)
    procedure = Procedure(
        company_id=company.id,
        role="Developer",
        title="Gap",
        body="b",
        proposed_by="ScrumMaster",
        status="active",
        baseline={
            "sprints": 2,
            "tasks": 2,
            "rework_per_task": 0.0,
            "blocked_rate": 0.0,
            "claim_gap_rate": 0.0,
        },
        adopted_after_sprint_id=0,
    )
    db.add(procedure)
    db.commit()
    for _ in range(2):
        sprint = _sprint(db, company)
        _deliver(db, _item(db, sprint), "refuted", status="done")
        _close(db, sprint)

    [decided] = procedures.evaluate(db, company.id)
    assert decided.outcome["result"] == "reverted"
    assert decided.outcome["claim_gap_rate"] == 1.0
    assert "claimed but not verified 0.0" in procedures.describe_outcome(decided)


# ── The endpoints ────────────────────────────────────────────────────────────


@pytest.fixture()
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return key


def _headers(session, company_id, role):
    user = User(
        email=f"pg-{secrets.token_hex(4)}@example.com",
        full_name="u",
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def test_the_progress_endpoint_is_live_while_open_and_sealed_once_closed(
    test_client, db, signing_key
):
    company, other = _company(db), _company(db)
    sprint = _sprint(db, company)
    item = _item(db, sprint)
    _deliver(db, item, "verified", status="done")
    viewer = _headers(db, company.id, "Viewer")

    response = test_client.get(f"/v1/sprints/{sprint.id}/progress", headers=viewer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "live" and body["snapshot"] is None
    assert body["series"]["final"]["verified"] == 1
    by_event = test_client.get(
        f"/v1/sprints/{sprint.id}/progress?granularity=event", headers=viewer
    ).json()
    assert by_event["series"]["granularity"] == "event"

    _close(db, sprint)
    progress_snapshot.seal_at_close(db.get_bind(), sprint.id, {"total_tasks": 1})
    body = test_client.get(f"/v1/sprints/{sprint.id}/progress", headers=viewer).json()
    assert body["source"] == "snapshot"
    assert body["retrospective"] == {"total_tasks": 1}
    assert body["snapshot"]["close_number"] == 1
    assert body["snapshot"]["verification"]["intact"] is True
    assert len(body["snapshots"]) == 1

    outsider = _headers(db, other.id, "Viewer")
    url = f"/v1/sprints/{sprint.id}/progress"
    assert test_client.get(url, headers=outsider).status_code == 403
    assert test_client.get(url).status_code == 401
    assert (
        test_client.get("/v1/sprints/987654/progress", headers=viewer).status_code
        == 404
    )
    bad = test_client.get(f"{url}?granularity=hour", headers=viewer)
    assert bad.status_code == 422


def test_the_specification_progress_endpoint(test_client, db, signing_key):
    company, other = _company(db), _company(db)
    spec = Specification(
        company_id=company.id,
        title="S",
        status="approved",
        requirements=[
            {"id": "REQ-1", "text": "a", "criteria": [{"id": "REQ-1.1"}]},
            {"id": "REQ-2", "text": "b", "criteria": []},
        ],
    )
    db.add(spec)
    db.commit()
    first, second = (
        _sprint(db, company, spec_id=spec.id),
        _sprint(db, company, spec_id=spec.id),
    )
    _deliver(db, _item(db, first, covers=["REQ-1.1"]), "refuted", status="blocked")
    _deliver(db, _item(db, second, covers=["REQ-1.1"]), "verified", status="done")
    viewer = _headers(db, company.id, "Viewer")

    response = test_client.get(f"/v1/specifications/{spec.id}/progress", headers=viewer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sprint_ids"] == [first.id, second.id]
    rows = {r["requirement"]: r for r in body["requirements"]}
    assert rows["REQ-1"]["state"] == "verified" and len(rows["REQ-1"]["tasks"]) == 2
    assert rows["REQ-2"]["state"] == "uncovered"
    outsider = _headers(db, other.id, "Viewer")
    url = f"/v1/specifications/{spec.id}/progress"
    assert test_client.get(url, headers=outsider).status_code == 403
    assert (
        test_client.get(
            "/v1/specifications/987654/progress", headers=viewer
        ).status_code
        == 404
    )


def test_the_verified_event_carries_the_verifiers_verdict():
    """board_writes gives every ``verified`` event its run's latest
    verification as ``{"id", "verdict"}``; the progress reads the verdict
    from it, and still reads the bare verdict of an earlier event."""
    from services.tasks import progress

    assert progress.verdict_of({"verification": {"id": 7, "verdict": "refuted"}}) == (
        "refuted"
    )
    assert progress.verdict_of({"verification": "verified"}) == "verified"
    assert progress.verdict_of({"verification": {"verdict": "made up"}}) is None
    assert progress.verdict_of(None) is None
