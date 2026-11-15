"""The intake queue (issue #532): requests in, a person's decision out.

The vocabulary and the measures (``services/contracts/intake_rules.py``), the
moves and their audit rows (``services/planning/intake_queue.py``), the
routes and their roles (``routers/intake.py``: a Viewer reads and sends, a
Manager decides), the ways a request arrives (a ticket, the client's brief,
an API token, a chat command), the wake of a snoozed request and the Support
agent's suggestion (``services/management/intake_triage.py``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    IntakeItem,
    OutboxEvent,
    Specification,
    Sprint,
    TaskEvent,
    User,
)
from services.contracts import intake_rules as intake
from services.planning import intake_queue
from sqlmodel import select

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture()
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session, name="Intake"):
    company = Company(company_name=f"{name} {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _member(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}, user


def _sprint(session, company_id, status="active", team=None):
    sprint = Sprint(
        company_id=company_id, sprint_name="Sprint 1", status=status, team=team
    )
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    return sprint


def _item(session, company_id, title="Export the board as CSV", source="brief"):
    item = intake_queue.receive(
        session, company_id, source, title, "So we can share it.", submitted_by="ana"
    )
    session.commit()
    session.refresh(item)
    return item


def queue_of(session):
    """A company with a Manager and a Viewer, and their headers."""
    company = _company(session)
    manager, manager_user = _member(session, company.id, "Manager")
    viewer, _ = _member(session, company.id, "Viewer")
    return {
        "company": company,
        "manager": manager,
        "manager_email": manager_user.email,
        "viewer": viewer,
    }


@pytest.fixture()
def queue(sqlite_session, enforced):
    return queue_of(sqlite_session)


def _audit(session, item_id):
    return [
        row
        for row in session.exec(select(AuditLog).order_by(AuditLog.id)).all()
        if (row.details or {}).get("intake_item_id") == item_id
    ]


# ── The vocabulary ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        ("pending", "accepted", True),
        ("pending", "declined", True),
        ("pending", "duplicate", True),
        ("pending", "snoozed", True),
        ("pending", "pending", False),
        ("snoozed", "pending", True),
        ("snoozed", "accepted", True),
        ("snoozed", "snoozed", False),
        ("declined", "pending", True),
        ("declined", "accepted", False),
        ("duplicate", "pending", True),
        ("accepted", "pending", False),
        ("accepted", "declined", False),
    ],
)
def test_the_moves_a_person_may_make(current, target, allowed):
    assert intake.can_move(current, target) is allowed


def test_the_measures_compare_two_fields_and_count_what_was_recorded():
    t0 = datetime(2026, 11, 1, 9, 0)
    rows = [
        # accepted with its outcome, agent agreed: 1 hour.
        {
            "status": "accepted",
            "outcome": {"kind": "task", "id": 1},
            "created_at": t0,
            "decided_at": t0 + timedelta(hours=1),
            "triaged": True,
            "suggested_status": "accepted",
            "suggested_category": "feature",
            "decided_status": "accepted",
            "decided_category": "feature",
        },
        # declined with a reason, agent said accept: 3 hours.
        {
            "status": "declined",
            "decision_reason": "Out of scope",
            "created_at": t0,
            "decided_at": t0 + timedelta(hours=3),
            "triaged": True,
            "suggested_status": "accepted",
            "suggested_category": "bug",
            "decided_status": "declined",
            "decided_category": "feature",
        },
        # duplicate without a link (not recorded); agent unusable: 2 hours.
        {
            "status": "duplicate",
            "created_at": t0,
            "decided_at": t0 + timedelta(hours=2),
            "triaged": True,
            "suggested_status": None,
            "suggested_category": None,
            "decided_status": "duplicate",
            "decided_category": None,
        },
        # still pending, never triaged.
        {"status": "pending", "created_at": t0, "triaged": False},
    ]
    numbers = intake.measures(rows)
    assert numbers["received"] == 4
    assert numbers["recorded"] == 2
    assert numbers["recorded_share"] == 0.5
    assert numbers["median_seconds_to_decision"] == 7200
    assert numbers["agreement"] == {
        "feature": {"decided": 2, "agreed": 1, "unusable": 0},
        "uncategorized": {"decided": 1, "agreed": 0, "unusable": 1},
    }
    assert numbers["category_agreement"] == {"compared": 2, "agreed": 1}
    assert numbers["by_status"]["pending"] == 1
    assert intake.measures([])["recorded_share"] is None


# ── Roles ────────────────────────────────────────────────────────────────────


def test_a_viewer_reads_and_sends_but_only_a_manager_decides(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    sent = test_client.post(
        f"/v1/companies/{company.id}/intake",
        json={"title": "Dark mode", "body": "Please."},
        headers=queue["viewer"],
    )
    assert sent.status_code == 201
    item = sent.json()
    # The source is the backend's: a person signed in sends a brief.
    assert item["source"] == "brief" and item["status"] == "pending"
    listed = test_client.get(
        f"/v1/companies/{company.id}/intake", headers=queue["viewer"]
    ).json()
    assert [i["id"] for i in listed["items"]] == [item["id"]]
    assert listed["pending"] == 1
    for action, body in (
        ("decline", {"reason": "no"}),
        ("snooze", {"until": (_utcnow() + timedelta(days=2)).isoformat()}),
        ("duplicate", {"of_task_id": 1}),
        ("accept", {"target": "task", "sprint_id": 1}),
        ("reopen", None),
    ):
        refused = test_client.post(
            f"/v1/intake/{item['id']}/{action}", json=body, headers=queue["viewer"]
        )
        assert refused.status_code == 403, action
    # A stranger reads nothing.
    stranger, _ = _member(sqlite_session, None, "Viewer")
    assert (
        test_client.get(
            f"/v1/companies/{company.id}/intake", headers=stranger
        ).status_code
        == 403
    )
    assert (
        test_client.get(f"/v1/intake/{item['id']}", headers=stranger).status_code == 403
    )


# ── Accept ───────────────────────────────────────────────────────────────────


def test_accepting_as_a_task_creates_it_and_links_both_ways(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    sprint = _sprint(sqlite_session, company.id)
    item = _item(sqlite_session, company.id)
    accepted = test_client.post(
        f"/v1/intake/{item.id}/accept",
        json={
            "target": "task",
            "sprint_id": sprint.id,
            "assignee": "developer",
            "priority": "high",
            "category": "feature",
        },
        headers=queue["manager"],
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted"
    task_id = body["outcome"]["id"]
    assert body["outcome"] == {"kind": "task", "id": task_id}
    assert body["decision"]["by"] == queue["manager_email"]
    assert body["decision"]["category"] == "feature"
    task = sqlite_session.get(BacklogItem, task_id)
    assert task.sprint_id == sprint.id and task.title == item.title
    assert task.assignee == "Developer" and task.priority == "high"
    # The task's thread names the request it came from.
    events = sqlite_session.exec(
        select(TaskEvent).where(TaskEvent.task_id == task_id)
    ).all()
    assert any(e.payload.get("intake_item_id") == item.id for e in events)
    assert f"intake request #{item.id}" in task.comments[-1]["text"]
    # In an active sprint it is queued now.
    assert sqlite_session.exec(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == str(task_id))
    ).first()
    # The history: received, then accepted by the Manager.
    history = body["history"]
    assert [h["action"] for h in history] == ["intake_received", "intake_accepted"]
    assert history[1]["actor"] == f"human:{queue['manager_email']}"
    assert (history[1]["from"], history[1]["to"]) == ("pending", "accepted")
    # Accepted is final.
    again = test_client.post(
        f"/v1/intake/{item.id}/accept",
        json={"target": "task", "sprint_id": sprint.id},
        headers=queue["manager"],
    )
    assert again.status_code == 409
    assert (
        test_client.post(
            f"/v1/intake/{item.id}/reopen", headers=queue["manager"]
        ).status_code
        == 409
    )


def test_a_task_joins_only_an_open_sprint_of_the_company(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    item = _item(sqlite_session, company.id)
    planned = _sprint(sqlite_session, company.id, status="planned")
    closed = _sprint(sqlite_session, company.id, status="completed")
    elsewhere = _sprint(sqlite_session, _company(sqlite_session, "Other").id)
    off_team = _sprint(sqlite_session, company.id, team=["QA"])
    manager = queue["manager"]

    def accept(sprint_id, **extra):
        return test_client.post(
            f"/v1/intake/{item.id}/accept",
            json={"target": "task", "sprint_id": sprint_id, **extra},
            headers=manager,
        )

    assert accept(None).status_code == 422
    assert accept(closed.id).status_code == 409
    assert accept(elsewhere.id).status_code == 404
    assert accept(off_team.id, assignee="Developer").status_code == 409
    assert accept(planned.id, assignee="Somebody").status_code == 422
    assert accept(planned.id, priority="urgent").status_code == 422
    # Nothing was written by the refusals.
    assert sqlite_session.get(IntakeItem, item.id).status == "pending"
    ok = accept(planned.id)
    assert ok.status_code == 200
    task_id = ok.json()["outcome"]["id"]
    # In a planned sprint it waits for the sprint to start.
    assert (
        sqlite_session.exec(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == str(task_id))
        ).first()
        is None
    )


def test_accepting_into_a_specification_opens_a_revision_with_the_request(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    approved = Specification(
        company_id=company.id,
        title="Task board",
        status="approved",
        requirements=[{"id": "REQ-1", "title": "List", "criteria": []}],
    )
    sqlite_session.add(approved)
    sqlite_session.commit()
    first = _item(sqlite_session, company.id, "Export as CSV")
    second = _item(sqlite_session, company.id, "Export as PDF")
    out = test_client.post(
        f"/v1/intake/{first.id}/accept",
        json={"target": "specification", "specification_id": approved.id},
        headers=queue["manager"],
    ).json()
    revision = sqlite_session.get(Specification, out["outcome"]["id"])
    assert out["outcome"]["kind"] == "specification"
    assert revision.status == "draft" and revision.supersedes_id == approved.id
    assert revision.version == approved.version + 1
    assert revision.requirements == approved.requirements
    assert revision.questions[0]["question"].startswith(
        f"Intake request #{first.id}: Export as CSV"
    )
    # A second request lands in the same open revision.
    out2 = test_client.post(
        f"/v1/intake/{second.id}/accept",
        json={"target": "specification", "specification_id": approved.id},
        headers=queue["manager"],
    ).json()
    assert out2["outcome"]["id"] == revision.id
    sqlite_session.refresh(revision)
    assert len(revision.questions) == 2
    # The approved version itself is never edited.
    sqlite_session.refresh(approved)
    assert approved.status == "approved" and not approved.questions
    third = _item(sqlite_session, company.id)
    assert (
        test_client.post(
            f"/v1/intake/{third.id}/accept",
            json={"target": "specification"},
            headers=queue["manager"],
        ).status_code
        == 422
    )


# ── Decline, duplicate, snooze ───────────────────────────────────────────────


def test_declining_needs_a_reason_and_can_be_reopened(
    test_client, sqlite_session, queue
):
    item = _item(sqlite_session, queue["company"].id)
    manager = queue["manager"]
    assert (
        test_client.post(
            f"/v1/intake/{item.id}/decline", json={"reason": ""}, headers=manager
        ).status_code
        == 422
    )
    declined = test_client.post(
        f"/v1/intake/{item.id}/decline",
        json={"reason": "Not what this product does", "category": "feature"},
        headers=manager,
    ).json()
    assert declined["status"] == "declined"
    assert declined["decision"]["reason"] == "Not what this product does"
    assert (
        test_client.post(
            f"/v1/intake/{item.id}/decline",
            json={"reason": "x", "category": "nonsense"},
            headers=manager,
        ).status_code
        == 409
    )
    reopened = test_client.post(f"/v1/intake/{item.id}/reopen", headers=manager).json()
    assert reopened["status"] == "pending"
    # The decision stays recorded until the next one; the history keeps both.
    assert [h["action"] for h in reopened["history"]] == [
        "intake_received",
        "intake_declined",
        "intake_pending",
    ]
    assert (
        test_client.post(
            f"/v1/intake/{item.id}/decline",
            json={"reason": "x", "category": "nonsense"},
            headers=manager,
        ).status_code
        == 422
    )


def test_a_duplicate_names_one_request_or_one_task_of_the_company(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    manager = queue["manager"]
    original = _item(sqlite_session, company.id, "Export as CSV")
    repeat = _item(sqlite_session, company.id, "CSV export please")
    sprint = _sprint(sqlite_session, company.id)
    task = BacklogItem(sprint_id=sprint.id, title="CSV export")
    sqlite_session.add(task)
    sqlite_session.commit()
    other = _item(sqlite_session, _company(sqlite_session, "Other").id)

    def mark(item_id, body):
        return test_client.post(
            f"/v1/intake/{item_id}/duplicate", json=body, headers=manager
        )

    assert mark(repeat.id, {}).status_code == 422
    assert (
        mark(repeat.id, {"of_item_id": original.id, "of_task_id": task.id}).status_code
        == 422
    )
    assert mark(repeat.id, {"of_item_id": repeat.id}).status_code == 422
    assert mark(repeat.id, {"of_item_id": other.id}).status_code == 404
    done = mark(repeat.id, {"of_item_id": original.id}).json()
    assert done["status"] == "duplicate"
    assert done["duplicate_of"] == {"kind": "intake", "id": original.id}
    # The original cannot now duplicate its own duplicate.
    assert mark(original.id, {"of_item_id": repeat.id}).status_code == 422
    third = _item(sqlite_session, company.id, "CSV again")
    by_task = mark(third.id, {"of_task_id": task.id}).json()
    assert by_task["duplicate_of"] == {"kind": "task", "id": task.id}


def test_a_snoozed_request_comes_back_when_its_date_passes(
    test_client, sqlite_session, queue
):
    item = _item(sqlite_session, queue["company"].id)
    manager = queue["manager"]
    past = (_utcnow() - timedelta(hours=1)).isoformat()
    assert (
        test_client.post(
            f"/v1/intake/{item.id}/snooze", json={"until": past}, headers=manager
        ).status_code
        == 422
    )
    until = _utcnow() + timedelta(days=3)
    snoozed = test_client.post(
        f"/v1/intake/{item.id}/snooze",
        json={"until": until.isoformat(), "reason": "After the release"},
        headers=manager,
    ).json()
    assert snoozed["status"] == "snoozed" and snoozed["snoozed_until"]
    # Not yet due.
    assert intake_queue.wake_due(sqlite_session, until - timedelta(minutes=1)) == 0
    assert sqlite_session.get(IntakeItem, item.id).status == "snoozed"
    # Due: back to pending, as the system's move.
    assert intake_queue.wake_due(sqlite_session, until + timedelta(minutes=1)) == 1
    woken = sqlite_session.get(IntakeItem, item.id)
    sqlite_session.refresh(woken)
    assert woken.status == "pending" and woken.snoozed_until is None
    rows = _audit(sqlite_session, item.id)
    assert [r.action for r in rows] == [
        "intake_received",
        "intake_snoozed",
        "intake_woken",
    ]
    assert rows[-1].agent_id == intake_queue.SYSTEM_ACTOR
    assert rows[-1].details["from"] == "snoozed" and rows[-1].details["to"] == "pending"
    # Every row is in the company's chain.
    assert all(r.chain_seq is not None for r in rows)
