"""A company's routines (``services/loop/routines.py``): a Manager writes
them, each change and run is audited, a due routine opens a small sprint with
its task, a routine whose previous run is open skips, and one occurrence runs
once however many replicas see it."""

from datetime import datetime

import pytest
import test_agent_access
from models import BacklogItem, OutboxEvent, Routine, Sprint
from models.audit import AuditLog
from services.loop import routines
from sqlmodel import select

company = test_agent_access.company
enforcement = test_agent_access.enforcement

DAILY = {"every": "day", "at": "09:00", "timezone": "UTC"}


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _create(test_client, company, headers, **body):
    body = {
        "title": "Dependency audit",
        "assignee": "Developer",
        "schedule": DAILY,
        **body,
    }
    return test_client.post(
        f"/v1/companies/{company['id']}/routines", json=body, headers=headers
    )


def _actions(session, routine_id):
    return [
        row.action
        for row in session.exec(
            select(AuditLog)
            .where(AuditLog.interaction_id == f"routine:{routine_id}")
            .order_by(AuditLog.id)
        ).all()
    ]


def test_a_manager_writes_a_routine_and_a_viewer_reads_it(
    test_client, sqlite_session, company
):
    assert _create(test_client, company, company["viewer"]).status_code == 403
    assert _create(test_client, company, company["outsider"]).status_code == 403

    made = _create(test_client, company, company["manager"], description="pip-audit")

    assert made.status_code == 201, made.text
    body = made.json()
    assert body["schedule"] == DAILY and body["next_run_at"].endswith("09:00:00")
    listed = test_client.get(
        f"/v1/companies/{company['id']}/routines", headers=company["viewer"]
    ).json()["routines"]
    assert [r["id"] for r in listed] == [body["id"]]
    assert _actions(sqlite_session, body["id"]) == ["routine_created"]


@pytest.mark.parametrize(
    "body",
    [
        {"assignee": "Astronaut"},
        {"schedule": {"every": "hour", "at": "09:00"}},
        {"schedule": {"every": "week", "at": "09:00"}},
        {"title": ""},
        {"company_id": 999},
    ],
)
def test_a_routine_the_backend_refuses(test_client, company, body):
    assert _create(test_client, company, company["manager"], **body).status_code == 422


def test_a_change_is_audited_and_disabling_clears_the_next_run(
    test_client, sqlite_session, company
):
    rid = _create(test_client, company, company["manager"]).json()["id"]
    url = f"/v1/routines/{rid}"
    assert (
        test_client.patch(
            url, json={"enabled": False}, headers=company["viewer"]
        ).status_code
        == 403
    )

    off = test_client.patch(url, json={"enabled": False}, headers=company["manager"])

    assert off.status_code == 200 and off.json()["next_run_at"] is None
    assert _actions(sqlite_session, rid) == ["routine_created", "routine_changed"]
    assert test_client.delete(url, headers=company["manager"]).status_code == 200
    assert sqlite_session.get(Routine, rid) is None
    assert _actions(sqlite_session, rid)[-1] == "routine_deleted"


def _routine(session, company_id, next_run_at):
    routine = Routine(
        company_id=company_id,
        title="Weekly report",
        description="Summarise the week",
        assignee="Developer",
        schedule=DAILY,
        next_run_at=next_run_at,
    )
    session.add(routine)
    session.commit()
    return routine


def test_a_due_routine_opens_a_sprint_with_its_task_once(sqlite_session, company):
    routine = _routine(sqlite_session, company["id"], datetime(2026, 11, 22, 9, 0))
    engine = sqlite_session.get_bind()
    now = datetime(2026, 11, 22, 9, 0, 30)

    [fired] = routines.fire_due(engine, now=now)
    assert routines.fire_due(engine, now=now) == []  # claimed: not again

    sqlite_session.expire_all()
    sprint = sqlite_session.get(Sprint, fired["sprint_id"])
    assert sprint.sprint_name == "Routine: Weekly report — 2026-11-22"
    [task] = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == sprint.id)
    ).all()
    assert (task.title, task.assignee, task.status) == (
        "Weekly report",
        "Developer",
        "todo",
    )
    queued = sqlite_session.exec(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == str(task.id))
    ).all()
    assert len(queued) == 1 and queued[0].payload["subject"] == "task.assign"
    stored = sqlite_session.get(Routine, routine.id)
    assert stored.next_run_at == datetime(2026, 11, 23, 9, 0)
    assert stored.last_sprint_id == sprint.id
    assert _actions(sqlite_session, routine.id) == ["routine_ran"]


def test_missed_occurrences_collapse_into_one_run(sqlite_session, company):
    routine = _routine(sqlite_session, company["id"], datetime(2026, 11, 18, 9, 0))
    engine = sqlite_session.get_bind()

    fired = routines.fire_due(engine, now=datetime(2026, 11, 22, 12, 0))

    assert len(fired) == 1
    sqlite_session.expire_all()
    assert sqlite_session.get(Routine, routine.id).next_run_at == datetime(
        2026, 11, 23, 9, 0
    )


def test_while_the_previous_run_is_open_the_occurrence_is_skipped(
    sqlite_session, company
):
    routine = _routine(sqlite_session, company["id"], datetime(2026, 11, 22, 9, 0))
    engine = sqlite_session.get_bind()
    [first] = routines.fire_due(engine, now=datetime(2026, 11, 22, 9, 1))

    [second] = routines.fire_due(engine, now=datetime(2026, 11, 23, 9, 1))

    assert second["open_sprint_id"] == first["sprint_id"]
    assert "still active" in second["skipped"]
    sqlite_session.expire_all()
    sprint = sqlite_session.get(Sprint, first["sprint_id"])
    sprint.status = "completed"
    sqlite_session.add(sprint)
    sqlite_session.commit()
    [third] = routines.fire_due(engine, now=datetime(2026, 11, 24, 9, 1))
    assert third["sprint_id"] != first["sprint_id"]
    assert _actions(sqlite_session, routine.id) == [
        "routine_ran",
        "routine_skipped",
        "routine_ran",
    ]


def test_a_disabled_routine_never_fires(sqlite_session, company):
    routine = _routine(sqlite_session, company["id"], datetime(2026, 11, 22, 9, 0))
    routine.enabled = False
    sqlite_session.add(routine)
    sqlite_session.commit()

    assert (
        routines.fire_due(sqlite_session.get_bind(), now=datetime(2026, 11, 22, 10, 0))
        == []
    )


def test_a_manager_runs_a_routine_now(test_client, sqlite_session, company):
    rid = _create(test_client, company, company["manager"]).json()["id"]
    url = f"/v1/routines/{rid}/run"
    assert test_client.post(url, headers=company["viewer"]).status_code == 403
    assert test_client.post(url, headers=company["outsider"]).status_code == 403
    assert (
        test_client.post(
            "/v1/routines/999999/run", headers=company["manager"]
        ).status_code
        == 404
    )

    ran = test_client.post(url, headers=company["manager"])

    assert ran.status_code == 200 and "sprint_id" in ran.json()
    again = test_client.post(url, headers=company["manager"]).json()
    assert "skipped" in again
