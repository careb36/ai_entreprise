"""Typed relations between tasks across sprints (issue #533).

The store and its events (``services/tasks/relations.py``): one row per pair,
the reverse names from one table, the refusal across companies, a proposed
relation that never reaches dispatch, a confirmed same-sprint ``blocked_by``
that does, the events on both chains; carry-over's confirmed ``relates_to``;
the measure; and the API's roles (``routers/task_relations.py``).
"""

from __future__ import annotations

import secrets
from uuid import uuid4

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    OutboxEvent,
    Sprint,
    TaskEvent,
    TaskRelation,
    User,
)
from services.loop import carry_over, sprint_control
from services.tasks import dependencies, relations, task_events, task_projection
from sqlalchemy.exc import IntegrityError
from sqlmodel import select


def _company(session):
    company = Company(company_name=f"Rel {uuid4().hex[:6]}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, status="active", name="S"):
    sprint = Sprint(company_id=company.id, sprint_name=name, status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session, sprint, title, status="todo", depends_on=None):
    item = BacklogItem(
        sprint_id=sprint.id, title=title, status=status, depends_on=depends_on
    )
    session.add(item)
    session.commit()
    return item


def _relation_events(session, task_id):
    return [
        e
        for e in task_events.for_task(session, task_id)
        if e.event_type == relations.EVENT_KIND
    ]


def _queued(session, sprint):
    return {
        int(row.aggregate_id)
        for row in session.exec(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "backlog_item")
        ).all()
        if row.payload["message"]["sprint_id"] == sprint.id
    }


# ── The store ────────────────────────────────────────────────────────────────


def test_one_row_per_pair_whichever_way_round(sqlite_session):
    company = _company(sqlite_session)
    first, second = _sprint(sqlite_session, company), _sprint(sqlite_session, company)
    a, b = _task(sqlite_session, first, "A"), _task(sqlite_session, second, "B")
    relations.propose(sqlite_session, a, b, "relates_to", by="ana", role="person")
    sqlite_session.commit()

    for name, x, y in (("duplicate", b, a), ("relates_to", a, b), ("blocking", a, b)):
        with pytest.raises(relations.RelationError) as refused:
            relations.propose(sqlite_session, x, y, name, by="ana", role="person")
        assert refused.value.status == 409
    with pytest.raises(relations.RelationError) as itself:
        relations.propose(sqlite_session, a, a, "relates_to", by="ana", role="person")
    assert itself.value.status == 422
    with pytest.raises(relations.RelationError, match="Unknown relation"):
        relations.propose(sqlite_session, a, b, "parent_of", by="ana", role="person")

    # The database holds the rule too, not only the service.
    low, high = sorted((a.id, b.id))
    sqlite_session.add(
        TaskRelation(
            company_id=company.id,
            task_id=b.id,
            related_task_id=a.id,
            pair_low=low,
            pair_high=high,
            kind="duplicate",
            proposed_by="x",
            proposed_role="person",
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()
    sqlite_session.rollback()


def test_the_reverse_name_is_read_from_one_table(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    a, b, c = (_task(sqlite_session, sprint, t) for t in "ABC")
    blocked = relations.propose(
        sqlite_session, a, b, "blocked_by", by="ana", role="person"
    )
    # ``blocking`` is stored as ``blocked_by`` with the tasks swapped.
    blocking = relations.propose(
        sqlite_session, a, c, "blocking", by="ana", role="person"
    )
    sqlite_session.commit()

    assert (blocked.task_id, blocked.related_task_id, blocked.kind) == (
        a.id,
        b.id,
        "blocked_by",
    )
    assert (blocking.task_id, blocking.related_task_id, blocking.kind) == (
        c.id,
        a.id,
        "blocked_by",
    )
    assert relations.name_for(blocked, a.id) == "blocked_by"
    assert relations.name_for(blocked, b.id) == "blocking"
    assert relations.name_for(blocking, a.id) == "blocking"
    assert relations.REVERSE == {
        "blocked_by": "blocking",
        "duplicate": "duplicate",
        "relates_to": "relates_to",
    }
    view = relations.as_dict(sqlite_session, blocked, b.id)
    assert view["relation"] == "blocking" and view["other_task"]["id"] == a.id


def test_tasks_of_two_companies_are_never_related(sqlite_session):
    ours, theirs = _company(sqlite_session), _company(sqlite_session)
    a = _task(sqlite_session, _sprint(sqlite_session, ours), "A")
    b = _task(sqlite_session, _sprint(sqlite_session, theirs), "B")
    with pytest.raises(relations.RelationError) as refused:
        relations.propose(sqlite_session, a, b, "duplicate", by="ana", role="person")
    assert refused.value.status == 404
    assert relations.for_task(sqlite_session, a.id) == []
    assert _relation_events(sqlite_session, b.id) == []


def test_a_proposed_relation_never_affects_dispatch(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(sqlite_session, sprint, "Store")
    endpoint = _task(sqlite_session, sprint, "Endpoint")
    # An agent's claim that the endpoint waits for the store: proposed only.
    proposed = relations.propose(
        sqlite_session, endpoint, store, "blocked_by", by="Developer", role="Developer"
    )
    sqlite_session.commit()

    assert proposed.state == "proposed" and proposed.added_dependency is False
    assert sqlite_session.get(BacklogItem, endpoint.id).depends_on is None
    assert dependencies.is_ready(sqlite_session, endpoint) is True
    assert sprint_control.dispatch_todo(sqlite_session, sprint) == 2
    sqlite_session.commit()
    assert _queued(sqlite_session, sprint) == {store.id, endpoint.id}
    assert dependencies.settle(sqlite_session, sprint.id) == {
        "blocked": [],
        "released": [],
    }
    # The agent's events are claims, and say nothing of the task's state.
    [event] = _relation_events(sqlite_session, endpoint.id)
    assert event.actor == "Developer" and event.payload["claim"] is True
    record = task_projection.records_for(sqlite_session, [endpoint])[endpoint.id]
    assert record.block_reason() == "" and record.rework_request_count() == 0
    assert record.is_settled("todo") is False


def test_a_confirmed_blocked_by_in_one_sprint_drives_dispatch(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(sqlite_session, sprint, "Store")
    endpoint = _task(sqlite_session, sprint, "Endpoint")
    relation = relations.propose(
        sqlite_session, endpoint, store, "blocked_by", by="Developer", role="Developer"
    )
    relations.confirm(sqlite_session, relation, "lead@example.com")
    sqlite_session.commit()

    assert relation.added_dependency is True
    assert sqlite_session.get(BacklogItem, endpoint.id).depends_on == [store.id]
    assert dependencies.is_ready(sqlite_session, endpoint) is False
    # Removing it takes the dependency it added out of dispatch again.
    assert relations.between(sqlite_session, store.id, endpoint.id) is relation
    relations.remove(sqlite_session, relation, "lead@example.com", "Not needed")
    sqlite_session.commit()
    assert sqlite_session.get(BacklogItem, endpoint.id).depends_on is None
    assert dependencies.is_ready(sqlite_session, endpoint) is True


def test_a_dependency_circle_is_refused(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    store = _task(sqlite_session, sprint, "Store")
    endpoint = _task(sqlite_session, sprint, "Endpoint", depends_on=[store.id])
    with pytest.raises(relations.RelationError, match="circle") as refused:
        relations.propose(
            sqlite_session,
            store,
            endpoint,
            "blocked_by",
            by="ana",
            role="person",
            confirmed_by="ana",
        )
    assert refused.value.status == 409
    sqlite_session.rollback()


def test_other_kinds_and_other_sprints_are_information_only(sqlite_session):
    company = _company(sqlite_session)
    first, second = _sprint(sqlite_session, company), _sprint(sqlite_session, company)
    a, b = _task(sqlite_session, first, "A"), _task(sqlite_session, first, "B")
    c = _task(sqlite_session, second, "C")
    kept = [
        relations.propose(
            sqlite_session,
            a,
            b,
            "duplicate",
            by="ana",
            role="person",
            confirmed_by="ana",
        ),
        relations.propose(
            sqlite_session,
            a,
            c,
            "blocked_by",
            by="ana",
            role="person",
            confirmed_by="ana",
        ),
    ]
    sqlite_session.commit()
    assert [r.added_dependency for r in kept] == [False, False]
    assert sqlite_session.get(BacklogItem, a.id).depends_on is None


def test_every_change_is_an_event_on_both_chains(sqlite_session):
    company = _company(sqlite_session)
    first, second = _sprint(sqlite_session, company), _sprint(sqlite_session, company)
    a, b = _task(sqlite_session, first, "A"), _task(sqlite_session, second, "B")
    relation = relations.propose(sqlite_session, a, b, "duplicate", by="QA", role="QA")
    relation_id = relation.id
    relations.confirm(sqlite_session, relation, "lead@example.com")
    relations.remove(sqlite_session, relation, "lead@example.com", "Different scope")
    proposed = relations.propose(
        sqlite_session, b, a, "relates_to", by="ana", role="person"
    )
    relations.reject(sqlite_session, proposed, "lead@example.com")
    sqlite_session.commit()

    for task_id, other, name in ((a.id, b.id, "duplicate"), (b.id, a.id, "duplicate")):
        events = _relation_events(sqlite_session, task_id)
        assert [e.payload["action"] for e in events] == [
            "proposed",
            "confirmed",
            "removed",
            "proposed",
            "rejected",
        ]
        assert events[0].payload["relation_id"] == relation_id
        assert events[0].payload["relation"] == name
        assert all(e.payload["other_task"] == other for e in events)
        assert [e.actor for e in events] == [
            "QA",
            "person",
            "person",
            "person",
            "person",
        ]
        assert events[2].payload["reason"] == "Different scope"
        assert task_events.verify_chain(sqlite_session, task_id)["status"] == "intact"
    assert relations.for_task(sqlite_session, a.id) == []
    # A confirmed relation cannot be rejected, only removed.
    again = relations.propose(
        sqlite_session, a, b, "relates_to", by="ana", role="person", confirmed_by="ana"
    )
    with pytest.raises(relations.RelationError) as refused:
        relations.reject(sqlite_session, again, "ana")
    assert refused.value.status == 409
    with pytest.raises(relations.RelationError):
        relations.confirm(sqlite_session, again, "ana")
    sqlite_session.rollback()


def test_carry_over_writes_a_confirmed_relates_to(sqlite_session):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company, status="completed", name="First")
    item = _task(sqlite_session, sprint, "Store", status="blocked")
    task_events.record(
        sqlite_session,
        item,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    sqlite_session.commit()
    nxt = _sprint(sqlite_session, company, name="Second")

    [clone] = carry_over.carry_over(
        sqlite_session, company.id, [item.id], nxt, "ana", "Manager"
    )
    sqlite_session.commit()

    [relation] = relations.for_task(sqlite_session, item.id)
    assert (relation.task_id, relation.related_task_id) == (clone.id, item.id)
    assert (relation.kind, relation.state) == ("relates_to", "confirmed")
    assert relation.confirmed_by == relations.RULE_CARRY_OVER
    assert relation.proposed_role == "system"
    for task_id in (item.id, clone.id):
        [event] = _relation_events(sqlite_session, task_id)
        assert event.actor == "system" and event.payload["action"] == "added"
        assert event.payload["claim"] is False
        assert task_events.verify_chain(sqlite_session, task_id)["status"] == "intact"


def test_duplicates_delivered_counts_confirmed_pairs_both_done(sqlite_session):
    company = _company(sqlite_session)
    first, second = _sprint(sqlite_session, company), _sprint(sqlite_session, company)
    a = _task(sqlite_session, first, "Login page", status="done")
    b = _task(sqlite_session, second, "Sign-in page", status="done")
    c = _task(sqlite_session, first, "Logout", status="done")
    d = _task(sqlite_session, first, "Sign-out", status="todo")
    e = _task(sqlite_session, first, "Profile", status="done")
    f = _task(sqlite_session, second, "Account", status="done")
    relations.propose(
        sqlite_session, a, b, "duplicate", by="ana", role="person", confirmed_by="ana"
    )
    relations.propose(
        sqlite_session, c, d, "duplicate", by="ana", role="person", confirmed_by="ana"
    )
    relations.propose(sqlite_session, e, f, "duplicate", by="QA", role="QA")
    sqlite_session.commit()

    metrics = relations.sprint_metrics(sqlite_session, first.id)
    assert metrics["duplicates_delivered"] == 1
    assert (metrics["total"], metrics["confirmed"], metrics["proposed"]) == (3, 2, 1)
    assert (
        relations.sprint_metrics(sqlite_session, second.id)["duplicates_delivered"] == 1
    )


# ── The API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company, other = _company(sqlite_session), _company(sqlite_session)
    first, second = _sprint(sqlite_session, company), _sprint(sqlite_session, company)
    a, b = _task(sqlite_session, first, "A"), _task(sqlite_session, second, "B")
    foreign = _task(sqlite_session, _sprint(sqlite_session, other), "Foreign")

    def member(company_id, role):
        user = User(
            email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
            full_name=role,
            password_hash="x",
        )
        sqlite_session.add(user)
        sqlite_session.commit()
        sqlite_session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        sqlite_session.commit()
        token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
        return {"Authorization": f"Bearer {token}"}

    return {
        "sprint": first,
        "a": a,
        "b": b,
        "foreign": foreign,
        "viewer": member(company.id, "Viewer"),
        "manager": member(company.id, "Manager"),
        "outsider": member(other.id, "Manager"),
    }


def test_a_viewer_reads_a_manager_proposes_and_confirms(
    test_client, sqlite_session, api
):
    a, b = api["a"], api["b"]
    url = f"/v1/tasks/{a.id}/relations"
    body = {"related_task_id": b.id, "relation": "duplicate"}

    assert test_client.post(url, json=body).status_code == 401
    assert test_client.post(url, json=body, headers=api["viewer"]).status_code == 403
    assert test_client.post(url, json=body, headers=api["outsider"]).status_code == 403
    foreign = {"related_task_id": api["foreign"].id, "relation": "duplicate"}
    assert (
        test_client.post(url, json=foreign, headers=api["manager"]).status_code == 404
    )
    bad = {"related_task_id": b.id, "relation": "parent_of"}
    assert test_client.post(url, json=bad, headers=api["manager"]).status_code == 422

    created = test_client.post(url, json=body, headers=api["manager"])
    assert created.status_code == 201, created.text
    relation = created.json()
    assert relation["state"] == "proposed" and relation["relation"] == "duplicate"
    assert relation["proposed_role"] == "person"
    assert test_client.post(url, json=body, headers=api["manager"]).status_code == 409

    listed = test_client.get(f"/v1/tasks/{b.id}/relations", headers=api["viewer"])
    assert listed.status_code == 200
    [seen] = listed.json()["relations"]
    assert seen["other_task"]["id"] == a.id and seen["other_task"]["title"] == "A"
    assert test_client.get(url, headers=api["outsider"]).status_code == 403

    confirm = f"/v1/task_relations/{relation['id']}/confirm"
    assert test_client.post(confirm, headers=api["viewer"]).status_code == 403
    assert test_client.post(confirm, headers=api["outsider"]).status_code == 403
    confirmed = test_client.post(confirm, headers=api["manager"])
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "confirmed"
    assert confirmed.json()["confirmed_by"].startswith("manager-")
    assert test_client.post(confirm, headers=api["manager"]).status_code == 409
    reject = f"/v1/task_relations/{relation['id']}/reject"
    assert test_client.post(reject, json={}, headers=api["manager"]).status_code == 409

    metrics = test_client.get(
        f"/v1/sprints/{api['sprint'].id}/relations", headers=api["viewer"]
    )
    assert metrics.status_code == 200
    assert metrics.json()["metrics"]["confirmed"] == 1

    remove = f"/v1/task_relations/{relation['id']}/remove"
    assert test_client.post(remove, json={}, headers=api["viewer"]).status_code == 403
    removed = test_client.post(
        remove, json={"reason": "Not the same"}, headers=api["manager"]
    )
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert test_client.post(remove, json={}, headers=api["manager"]).status_code == 404

    sqlite_session.expire_all()
    actions = [
        row.action
        for row in sqlite_session.exec(
            select(AuditLog).where(AuditLog.interaction_id == f"task-{a.id}")
        ).all()
    ]
    assert actions == [
        "task_relation_added",
        "task_relation_confirmed",
        "task_relation_removed",
    ]
    events = sqlite_session.exec(
        select(TaskEvent).where(
            TaskEvent.task_id == b.id, TaskEvent.event_type == "relation"
        )
    ).all()
    assert [e.payload["action"] for e in events] == ["proposed", "confirmed", "removed"]


def test_a_manager_rejects_a_proposal_and_writes_one_confirmed(test_client, api):
    a, b = api["a"], api["b"]
    url = f"/v1/tasks/{a.id}/relations"
    proposed = test_client.post(
        url,
        json={"related_task_id": b.id, "relation": "relates_to"},
        headers=api["manager"],
    ).json()
    rejected = test_client.post(
        f"/v1/task_relations/{proposed['id']}/reject",
        json={"reason": "Unrelated"},
        headers=api["manager"],
    )
    assert rejected.status_code == 200 and rejected.json()["removed"] is True

    direct = test_client.post(
        url,
        json={"related_task_id": b.id, "relation": "blocking", "confirm": True},
        headers=api["manager"],
    )
    assert direct.status_code == 201, direct.text
    assert direct.json()["state"] == "confirmed"
    assert direct.json()["relation"] == "blocking"
    assert direct.json()["stored_kind"] == "blocked_by"
    assert direct.json()["drives_dispatch"] is False  # two sprints: information


def test_unknown_task_and_relation_are_404(test_client, api):
    headers = api["manager"]
    assert (
        test_client.get("/v1/tasks/999999/relations", headers=headers).status_code
        == 404
    )
    assert (
        test_client.post(
            "/v1/task_relations/999999/confirm", headers=headers
        ).status_code
        == 404
    )
    assert (
        test_client.post(
            f"/v1/tasks/{api['a'].id}/relations",
            json={"related_task_id": 999999, "relation": "duplicate"},
            headers=headers,
        ).status_code
        == 404
    )
