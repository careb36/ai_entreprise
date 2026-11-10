"""Editing, deleting and archiving a sprint (``routers/sprint_crud.py``).

A Manager edits a sprint's name, goal and duration; an Owner deletes a halted
sprint with nothing recorded about its work, or archives it when there is
evidence, and unarchives it. Evidence is never lost: an archived sprint's
runs, verifications, events and audit rows stay readable by id.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import auth_tokens
from config import settings
from models import (
    AgentMessage,
    AgentRun,
    AuditLog,
    BacklogItem,
    OutboxEvent,
    Procedure,
    RunSecret,
    Sprint,
    SprintIntegration,
    TaskEvent,
    Verification,
)
from models.company import Company, CompanyMember, User
from models.manager import ManagerDecision
from services import manager_policy, sprint_archive, sprint_control
from services.carry_over import CarryOverError, carry_over
from services.platform import api_tokens
from services.tasks import task_events as te
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def audit_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


@pytest.fixture()
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session: Session, company_id: int | None, role: str) -> dict[str, str]:
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
        full_name=role,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def _sprint(session: Session, status: str = "planned", tasks: int = 2) -> Sprint:
    company = Company(company_name=f"Crud {uuid4().hex[:6]}", autonomy_level=2)
    session.add(company)
    session.commit()
    sprint = Sprint(
        company_id=company.id,
        sprint_name="Sprint 0",
        scope_size="S",
        status=status,
        goal="Ship it",
    )
    session.add(sprint)
    session.commit()
    for n in range(tasks):
        session.add(
            BacklogItem(
                sprint_id=sprint.id,
                title=f"t{n}",
                status="todo",
                assignee="Developer",
            )
        )
    session.commit()
    session.refresh(sprint)
    return sprint


def _items(session: Session, sprint_id: int) -> list[BacklogItem]:
    return list(
        session.exec(select(BacklogItem).where(BacklogItem.sprint_id == sprint_id))
    )


def _run_with_verification(session: Session, item: BacklogItem, company_id: int):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{item.id}",
        producer="le-fabrique-task-worker",
        artifact_digest="sha256:" + "0" * 64,
        artifact_size=1,
        subject={"sprint_id": item.sprint_id},
    )
    session.add(run)
    session.commit()
    verification = Verification(
        company_id=company_id, agent_run_id=run.id, verdict="verified"
    )
    session.add(verification)
    session.commit()
    te.record(session, item, "claimed", te.SYSTEM, status="in_progress")
    session.commit()
    return run, verification


def _audit_actions(session: Session, company_id: int) -> list[str]:
    return [
        row.action
        for row in session.exec(
            select(AuditLog)
            .where(AuditLog.company_id == company_id)
            .order_by(AuditLog.id)
        )
    ]


# ── Update ──────────────────────────────────────────────────────────────────


def test_a_manager_edits_name_goal_and_duration_and_it_is_audited(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    manager = _member(sqlite_session, sprint.company_id, "Manager")
    resp = test_client.patch(
        f"/v1/sprints/{sprint.id}",
        json={"sprint_name": "  Foundation  ", "goal": "", "duration_days": 10},
        headers=manager,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["sprint_name"], body["goal"], body["duration_days"]) == (
        "Foundation",
        None,
        10,
    )
    assert body["changed"]["sprint_name"] == {
        "before": "Sprint 0",
        "after": "Foundation",
    }
    assert body["changed"]["goal"] == {"before": "Ship it", "after": None}
    assert body["status"] == "planned"
    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == sprint.company_id,
            AuditLog.action == "sprint_updated",
        )
    ).one()
    assert row.result["changes"]["duration_days"] == {"before": None, "after": 10}
    assert row.agent_id.startswith("human:manager-")
    # The same values again change nothing and write no audit row.
    again = test_client.patch(
        f"/v1/sprints/{sprint.id}", json={"sprint_name": "Foundation"}, headers=manager
    )
    assert again.json()["changed"] == {}
    assert _audit_actions(sqlite_session, sprint.company_id) == ["sprint_updated"]


def test_update_is_refused_to_viewers_outsiders_and_without_a_token(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    viewer = _member(sqlite_session, sprint.company_id, "Viewer")
    outsider = _member(sqlite_session, None, "Owner")
    url = f"/v1/sprints/{sprint.id}"
    assert test_client.patch(url, json={"goal": "x"}, headers=viewer).status_code == 403
    assert (
        test_client.patch(url, json={"goal": "x"}, headers=outsider).status_code == 403
    )
    assert test_client.patch(url, json={"goal": "x"}).status_code == 401
    assert (
        test_client.patch("/v1/sprints/999999", json={}, headers=viewer).status_code
        == 404
    )


@pytest.mark.parametrize(
    "body",
    [
        {"sprint_name": ""},
        {"sprint_name": "   "},
        {"sprint_name": None},
        {"sprint_name": "x" * 256},
        {"goal": "g" * 2001},
        {"duration_days": 0},
        {"duration_days": 91},
        {"status": "active"},
        {"company_id": 1},
        {"backlog_items": []},
    ],
)
def test_invalid_values_and_fields_that_are_not_editable_are_422(
    sqlite_session, test_client, auth_on, body
):
    sprint = _sprint(sqlite_session)
    manager = _member(sqlite_session, sprint.company_id, "Manager")
    resp = test_client.patch(f"/v1/sprints/{sprint.id}", json=body, headers=manager)
    assert resp.status_code == 422
    sqlite_session.refresh(sprint)
    assert (sprint.sprint_name, sprint.status) == ("Sprint 0", "planned")


def test_an_archived_sprint_cannot_be_edited(sqlite_session, test_client, auth_on):
    sprint = _sprint(sqlite_session, status="cancelled")
    item = _items(sqlite_session, sprint.id)[0]
    _run_with_verification(sqlite_session, item, sprint.company_id)
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    assert test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner).json()[
        "archived"
    ]
    resp = test_client.patch(
        f"/v1/sprints/{sprint.id}", json={"goal": "new"}, headers=owner
    )
    assert resp.status_code == 409 and "archived" in resp.json()["detail"]


# ── Delete without evidence ─────────────────────────────────────────────────


def test_an_owner_deletes_an_empty_planned_sprint_with_its_tasks(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    items = _items(sqlite_session, sprint.id)
    # A dependency note is bookkeeping, a queued assignment is transport and
    # a run secret is configuration for a run that never happened.
    te.record(sqlite_session, items[1], "note", te.SYSTEM, text="Waits for task 1")
    sqlite_session.add(
        OutboxEvent(
            aggregate_type="backlog_item",
            aggregate_id=str(items[0].id),
            event_type="nats_publish",
            payload={"subject": "task.assign"},
        )
    )
    sqlite_session.add(
        RunSecret(
            company_id=sprint.company_id,
            task_id=items[0].id,
            name="TOKEN",
            encrypted_value="x",
        )
    )
    sqlite_session.commit()
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    sid, company_id, ids = sprint.id, sprint.company_id, [i.id for i in items]

    resp = test_client.delete(f"/v1/sprints/{sid}", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True and "archived" not in body
    assert body["removed"] == {
        "run_secrets": 1,
        "outbox_events": 1,
        "task_events": 1,
        "backlog_items": 2,
    }
    sqlite_session.expire_all()
    assert sqlite_session.get(Sprint, sid) is None
    assert all(sqlite_session.get(BacklogItem, i) is None for i in ids)
    assert not sqlite_session.exec(
        select(TaskEvent).where(TaskEvent.sprint_id == sid)
    ).all()
    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == "sprint_deleted"
        )
    ).one()
    assert row.result["sprint_name"] == "Sprint 0" and row.chain_hash
    assert test_client.get(f"/v1/sprints/{sid}/board", headers=owner).status_code == 404


def test_a_sprint_stopped_before_any_work_is_deleted(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    stopped = test_client.post(f"/v1/sprints/{sprint.id}/stop", headers=owner)
    assert stopped.json()["status"] == "cancelled"
    resp = test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner)
    assert resp.status_code == 200 and resp.json()["deleted"] is True


def test_a_sprint_edited_but_never_run_is_still_deleted(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    test_client.patch(f"/v1/sprints/{sprint.id}", json={"goal": "g"}, headers=owner)
    resp = test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner)
    assert resp.json()["deleted"] is True
    # The administration rows stay in the chain.
    assert _audit_actions(sqlite_session, sprint.company_id) == [
        "sprint_updated",
        "sprint_deleted",
    ]


# ── Archive with evidence ───────────────────────────────────────────────────


def test_a_cancelled_sprint_with_work_is_archived_and_its_evidence_stays(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session, status="cancelled")
    item = _items(sqlite_session, sprint.id)[0]
    run, verification = _run_with_verification(sqlite_session, item, sprint.company_id)
    owner = _member(sqlite_session, sprint.company_id, "Owner")

    resp = test_client.request(
        "DELETE",
        f"/v1/sprints/{sprint.id}",
        json={"reason": "abandoned"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["archived"] is True and "deleted" not in body
    assert body["archived_at"] and body["archived_by"].startswith("owner-")
    assert set(body["evidence"]) >= {"agent_runs", "task_events"}

    sqlite_session.expire_all()
    stored = sqlite_session.get(Sprint, sprint.id)
    assert stored is not None and stored.archive_reason == "abandoned"
    assert sqlite_session.get(BacklogItem, item.id) is not None
    assert sqlite_session.get(AgentRun, run.id) is not None
    assert sqlite_session.get(Verification, verification.id) is not None
    assert sqlite_session.exec(
        select(TaskEvent).where(TaskEvent.task_id == item.id)
    ).all()
    # Readable by id.
    board = test_client.get(f"/v1/sprints/{sprint.id}/board", headers=owner)
    assert board.status_code == 200
    assert board.json()["sprint"]["archived_at"] == body["archived_at"]
    assert (
        test_client.get(
            f"/v1/agent_runs/{run.id}?company_id={sprint.company_id}", headers=owner
        ).status_code
        == 200
    )
    assert "sprint_archived" in _audit_actions(sqlite_session, sprint.company_id)
    again = test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner)
    assert again.status_code == 409


@pytest.mark.parametrize(
    "kind",
    ["message", "integration", "procedure", "decision", "audit", "person_note"],
)
def test_every_kind_of_evidence_archives_instead_of_deleting(sqlite_session, kind):
    sprint = _sprint(sqlite_session, status="completed")
    item = _items(sqlite_session, sprint.id)[0]
    cid = sprint.company_id
    rows = {
        "message": lambda: AgentMessage(
            company_id=cid,
            sprint_id=sprint.id,
            from_name="QA",
            from_role="QA",
            kind="handoff",
            body="b",
        ),
        "integration": lambda: SprintIntegration(
            company_id=cid,
            sprint_id=sprint.id,
            trigger="manual",
            requested_by="ana",
            status="tests_passed",
            branch="fabrique/sprint-1",
            base="main",
        ),
        "procedure": lambda: Procedure(
            company_id=cid,
            role="Developer",
            title="t",
            body="b",
            proposed_by="ScrumMaster",
            source_sprint_id=sprint.id,
        ),
        "decision": lambda: ManagerDecision(
            company_id=cid,
            item_kind="task",
            item_id=item.id,
            item_state="s",
            category="accept_delivery",
        ),
        "audit": lambda: AuditLog(
            company_id=cid,
            interaction_id=f"task-{item.id}",
            action="agent_turn",
            details={"sprint_id": sprint.id},
        ),
    }
    if kind == "person_note":
        te.record(sqlite_session, item, "note", te.PERSON, text="look at this")
    else:
        sqlite_session.add(rows[kind]())
    sqlite_session.commit()
    assert sprint_archive.evidence_of(sqlite_session, sprint)
    result = sprint_archive.remove(sqlite_session, sprint, "ana")
    assert result["archived"] is True
    assert sqlite_session.get(BacklogItem, item.id) is not None


def test_an_audit_row_that_names_a_task_in_its_details_is_evidence(sqlite_session):
    sprint = _sprint(sqlite_session, status="cancelled")
    item = _items(sqlite_session, sprint.id)[0]
    sqlite_session.add(
        AuditLog(
            company_id=sprint.company_id,
            action="agent_turn",
            details={"backlog_item_id": item.id, "sprint_id": None},
        )
    )
    sqlite_session.commit()
    assert sprint_archive.evidence_of(sqlite_session, sprint) == ["audit_log"]


def test_another_companys_rows_are_not_this_sprints_evidence(sqlite_session):
    sprint = _sprint(sqlite_session, status="planned")
    other = _sprint(sqlite_session, status="planned")
    item = _items(sqlite_session, sprint.id)[0]
    sqlite_session.add(
        AuditLog(
            company_id=other.company_id,
            interaction_id=f"task-{item.id}",
            action="agent_turn",
        )
    )
    sqlite_session.commit()
    assert sprint_archive.evidence_of(sqlite_session, sprint) == []


# ── What may be deleted, and by whom ────────────────────────────────────────


@pytest.mark.parametrize("status", ["active", "needs_split", "paused"])
def test_a_running_sprint_must_be_stopped_first(
    sqlite_session, test_client, auth_on, status
):
    sprint = _sprint(sqlite_session, status=status)
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    resp = test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner)
    assert resp.status_code == 409
    assert "Stop the sprint first" in resp.json()["detail"]
    assert sqlite_session.get(Sprint, sprint.id) is not None


def test_a_manager_a_viewer_an_outsider_and_an_api_token_cannot_delete(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session)
    url = f"/v1/sprints/{sprint.id}"
    for role in ("Manager", "Viewer"):
        headers = _member(sqlite_session, sprint.company_id, role)
        assert test_client.delete(url, headers=headers).status_code == 403
        assert test_client.post(f"{url}/unarchive", headers=headers).status_code == 403
    outsider = _member(sqlite_session, None, "Owner")
    assert test_client.delete(url, headers=outsider).status_code == 403
    _token, raw = api_tokens.create_token(
        sqlite_session, sprint.company_id, "ci", role="Manager"
    )
    token = {"Authorization": f"Bearer {raw}"}
    assert test_client.delete(url, headers=token).status_code == 403
    # The token is still a Manager: it may edit.
    assert (
        test_client.patch(url, json={"goal": "by token"}, headers=token).status_code
        == 200
    )
    assert test_client.delete(url).status_code == 401
    assert sqlite_session.get(Sprint, sprint.id) is not None


def test_the_manager_agent_can_never_be_handed_delete_sprint():
    assert "delete_sprint" in manager_policy.ALWAYS_ESCALATE
    with pytest.raises(manager_policy.PolicyError, match="always decided"):
        manager_policy.validate({"rules": ["delete_sprint"]})
    assert not manager_policy.evaluate(
        "delete_sprint",
        "accept",
        {},
        manager_policy.Policy(rules=list(manager_policy.RULES)),
    )["allowed"]


# ── Archived sprints leave the lists, the inbox and carry-over ──────────────


def _blocked_for_a_person(session: Session, sprint: Sprint) -> BacklogItem:
    item = _items(session, sprint.id)[0]
    te.record(
        session,
        item,
        "blocked",
        te.SYSTEM,
        status="blocked",
        text="Blocked: manual triage required.",
    )
    session.commit()
    return item


def test_an_archived_sprint_is_hidden_from_lists_inbox_and_carry_over(
    sqlite_session, test_client, auth_on
):
    sprint = _sprint(sqlite_session, status="cancelled")
    item = _blocked_for_a_person(sqlite_session, sprint)
    owner = _member(sqlite_session, sprint.company_id, "Owner")
    cid = sprint.company_id
    inbox = test_client.get(f"/v1/inbox?company_id={cid}", headers=owner).json()
    assert [t["id"] for t in inbox["blocked_tasks"]] == [item.id]

    archived = test_client.delete(f"/v1/sprints/{sprint.id}", headers=owner).json()
    assert archived["archived"] is True and archived["evidence"] == ["task_events"]

    listed = test_client.get(f"/v1/sprints?company_id={cid}", headers=owner).json()
    assert listed == []
    everything = test_client.get(
        f"/v1/sprints?company_id={cid}&include_archived=true", headers=owner
    ).json()
    assert [s["id"] for s in everything] == [sprint.id]
    assert everything[0]["archived_at"] == archived["archived_at"]
    inbox = test_client.get(f"/v1/inbox?company_id={cid}", headers=owner).json()
    assert inbox["blocked_tasks"] == [] and inbox["blocked_total"] == 0
    from services.manager_inbox import waiting_tasks

    assert waiting_tasks(sqlite_session, cid) == []
    new = Sprint(company_id=cid, sprint_name="next", status="active")
    sqlite_session.add(new)
    sqlite_session.commit()
    with pytest.raises(CarryOverError) as refused:
        carry_over(sqlite_session, cid, [item.id], new, "ana", "Manager")
    assert refused.value.status == 409 and "archived" in refused.value.detail

    # Unarchive brings it back where it was.
    back = test_client.post(f"/v1/sprints/{sprint.id}/unarchive", headers=owner)
    assert back.status_code == 200 and back.json()["status"] == "cancelled"
    listed = test_client.get(f"/v1/sprints?company_id={cid}", headers=owner).json()
    assert [s["id"] for s in listed] == [new.id, sprint.id]
    assert listed[1]["archived_at"] is None
    inbox = test_client.get(f"/v1/inbox?company_id={cid}", headers=owner).json()
    assert [t["id"] for t in inbox["blocked_tasks"]] == [item.id]
    assert (
        test_client.post(
            f"/v1/sprints/{sprint.id}/unarchive", headers=owner
        ).status_code
        == 409
    )
    actions = _audit_actions(sqlite_session, cid)
    assert actions[-2:] == ["sprint_archived", "sprint_unarchived"]


def test_an_archived_sprints_integration_leaves_the_inbox(sqlite_session):
    from services import integration

    sprint = _sprint(sqlite_session, status="completed")
    sqlite_session.add(
        SprintIntegration(
            company_id=sprint.company_id,
            sprint_id=sprint.id,
            trigger="manual",
            requested_by="ana",
            status="conflict",
            branch=f"fabrique/sprint-{sprint.id}",
            base="main",
        )
    )
    sqlite_session.commit()
    assert integration.waiting_for_a_person(sqlite_session, sprint.company_id)
    assert sprint_archive.remove(sqlite_session, sprint, "ana")["archived"]
    assert integration.waiting_for_a_person(sqlite_session, sprint.company_id) == []


def test_the_removable_statuses_are_the_halted_ones_the_worker_leaves_alone():
    for status in sprint_archive.REMOVABLE_STATUSES:
        assert sprint_control.is_halted(status) or status == "completed"
