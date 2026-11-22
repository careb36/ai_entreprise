"""A routine's revisions and variables (``services/loop/routine_revisions.py``,
``routines.py``; from Paperclip's routine revisions and variables): each
change is a numbered revision, a run records the revision and the values it
used, and a Manager restores an earlier revision as a new one."""

from datetime import datetime

import pytest
import test_agent_access
from models import BacklogItem, Routine
from models.audit import AuditLog
from services.loop import routines
from sqlmodel import select

company = test_agent_access.company
enforcement = test_agent_access.enforcement

DAILY = {"every": "day", "at": "09:00", "timezone": "UTC"}
SCOPE = {"name": "scope", "default": "backend", "options": ["backend", "frontend"]}


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


def _create(test_client, company, **body):
    body = {
        "title": "Audit",
        "description": "Audit the {{scope}} dependencies.",
        "assignee": "Developer",
        "schedule": DAILY,
        "variables": [SCOPE],
        **body,
    }
    return test_client.post(
        f"/v1/companies/{company['id']}/routines", json=body, headers=company["manager"]
    )


def _revisions(test_client, company, rid, who="viewer"):
    return test_client.get(f"/v1/routines/{rid}/revisions", headers=company[who])


def test_each_change_is_a_numbered_revision(test_client, company):
    made = _create(test_client, company)
    assert made.status_code == 201, made.text
    rid = made.json()["id"]
    assert made.json()["revision"] == 1

    test_client.patch(
        f"/v1/routines/{rid}",
        json={"description": "Audit {{scope}} now."},
        headers=company["manager"],
    )
    test_client.patch(  # nothing changes: no revision
        f"/v1/routines/{rid}", json={"title": "Audit"}, headers=company["manager"]
    )

    body = _revisions(test_client, company, rid).json()
    assert body["revision"] == 2
    assert [r["number"] for r in body["revisions"]] == [2, 1]
    assert body["revisions"][0]["change_summary"] == "Changed description"
    assert body["revisions"][1]["snapshot"]["description"] == (
        "Audit the {{scope}} dependencies."
    )
    assert _revisions(test_client, company, rid, "outsider").status_code == 403


def test_an_undeclared_placeholder_is_refused_when_written(test_client, company):
    assert (
        _create(test_client, company, description="Audit {{region}}").status_code == 422
    )
    rid = _create(test_client, company).json()["id"]
    dropped = test_client.patch(
        f"/v1/routines/{rid}", json={"variables": []}, headers=company["manager"]
    )
    assert dropped.status_code == 422 and "scope" in dropped.json()["detail"]


def test_a_run_records_its_revision_and_values(test_client, sqlite_session, company):
    rid = _create(test_client, company).json()["id"]
    url = f"/v1/routines/{rid}/run"
    refused = test_client.post(
        url, json={"values": {"scope": "mobile"}}, headers=company["manager"]
    )
    assert refused.status_code == 422

    ran = test_client.post(
        url, json={"values": {"scope": "frontend"}}, headers=company["manager"]
    )

    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["revision"] == 1 and body["values"] == {"scope": "frontend"}
    sqlite_session.expire_all()
    [task] = sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == body["sprint_id"])
    ).all()
    assert task.description == "Audit the frontend dependencies."
    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"routine:{rid}",
            AuditLog.action == "routine_ran",
        )
    ).one()
    assert row.result["revision"] == 1 and row.result["values"] == {"scope": "frontend"}


def test_a_scheduled_run_without_a_required_value_is_skipped(sqlite_session, company):
    routine = Routine(
        company_id=company["id"],
        title="Ticket",
        description="Close {{ticket}}",
        assignee="Developer",
        schedule=DAILY,
        variables=[
            {
                "name": "ticket",
                "label": None,
                "default": None,
                "required": True,
                "options": [],
            }
        ],
        next_run_at=datetime(2026, 11, 22, 9, 0),
    )
    sqlite_session.add(routine)
    sqlite_session.commit()

    [fired] = routines.fire_due(
        sqlite_session.get_bind(), now=datetime(2026, 11, 22, 9, 1)
    )

    assert "'ticket' is required" in fired["skipped"]


def test_a_manager_restores_an_earlier_revision_as_a_new_one(
    test_client, sqlite_session, company
):
    rid = _create(test_client, company).json()["id"]
    test_client.patch(
        f"/v1/routines/{rid}",
        json={"title": "Audit v2", "enabled": False},
        headers=company["manager"],
    )
    url = f"/v1/routines/{rid}/revisions/1/restore"
    assert test_client.post(url, headers=company["viewer"]).status_code == 403

    restored = test_client.post(url, headers=company["manager"])

    assert restored.status_code == 200, restored.text
    assert restored.json()["title"] == "Audit" and restored.json()["enabled"] is True
    assert restored.json()["revision"] == 3
    newest = _revisions(test_client, company, rid).json()["revisions"][0]
    assert (
        newest["restored_from"] == 1
        and newest["change_summary"] == "Restored revision 1"
    )
    assert test_client.post(url, headers=company["manager"]).status_code == 409
    missing = test_client.post(
        f"/v1/routines/{rid}/revisions/99/restore", headers=company["manager"]
    )
    assert missing.status_code == 404


def test_the_migration_gives_an_existing_routine_its_revision_1(tmp_path, monkeypatch):
    import json

    import migrations
    from sqlalchemy import create_engine, text

    from alembic import command

    url = f"sqlite:///{tmp_path / 'r.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    config = migrations.alembic_config(url)
    command.upgrade(config, "0062")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO routines (company_id, title, description, assignee, "
                "schedule, enabled, created_at) VALUES (1, 'Old', 'Do it', "
                "'Developer', :schedule, 1, '2026-11-22 10:00:00')"
            ),
            {"schedule": json.dumps(DAILY)},
        )
    command.upgrade(config, "head")
    with engine.connect() as conn:
        routine = conn.execute(text("SELECT variables, revision FROM routines")).one()
        revision = conn.execute(
            text("SELECT number, snapshot FROM routine_revisions")
        ).one()
    assert json.loads(routine.variables) == [] and routine.revision == 1
    snapshot = json.loads(revision.snapshot)
    assert revision.number == 1
    assert snapshot["title"] == "Old" and snapshot["schedule"] == DAILY
