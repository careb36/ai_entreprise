"""A sprint runs only when its company's guarantees hold or an Owner
acknowledged that they do not (issue #766).

With tests off (``DELIVERY_TESTS_ENABLED``) no code delivery can be verified;
with the host sandbox agent code runs unisolated. Every endpoint that starts
work refuses with 409 and names what is missing, before changing anything;
the task worker pauses the sprint for a task queued by any other path; an
Owner records the acknowledgement (audited, with a reason and an end), and
only an Owner; a withdrawn or expired one no longer counts; ``/health``
says ``degraded`` with the reason.
"""

import secrets
from datetime import timedelta

import audit_signing
import pytest
from config import settings
from fastapi.testclient import TestClient
from models import AuditLog, BacklogItem, Company, CompanyAcknowledgement, Sprint
from services.loop import preconditions
from services.platform import acknowledgements, healthcheck
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401

pytestmark = pytest.mark.preconditions


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


@pytest.fixture
def weak(monkeypatch):
    """The defaults this deployment ships with: tests off, host sandbox."""
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")


def _company(session, sandbox=None):
    company = Company(
        company_name=f"Pre {secrets.token_hex(3)}", delivery_sandbox=sandbox
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _sprint(session, company, status):
    sprint = Sprint(company_id=company.id, sprint_name="s", status=status)
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title="t",
        description="d",
        status="todo",
        assignee="Developer",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return sprint, item


def _ack(session, company, kind, days=30):
    return acknowledgements.record(
        session,
        company.id,
        kind,
        "we know, sprint 3 is a trial",
        by="owner@x",
        user_id=None,
        days=days,
    )


def _start_payload(company_id):
    return {
        "company_id": company_id,
        "sprint_name": "S",
        "start_date": "2026-11-20",
        "scope_size": "S",
        "backlog_items": [{"title": "Task 1", "description": "One"}],
    }


def test_the_configuration_says_which_acknowledgements_it_needs():
    assert acknowledgements.required(False, "host") == ["tests_off", "host_sandbox"]
    assert acknowledgements.required(True, "container") == []
    assert acknowledgements.required(True, "HOST") == ["host_sandbox"]
    assert acknowledgements.required(False, "bwrap") == ["tests_off"]


def test_tests_off_needs_acknowledgement(sqlite_session, test_client, weak):
    company = _company(sqlite_session)
    sprints_before = len(sqlite_session.exec(select(Sprint)).all())

    refused = test_client.post("/v1/start_sprint", json=_start_payload(company.id))

    assert refused.status_code == 409
    missing = refused.json()["detail"]["missing_acknowledgements"]
    assert [m["kind"] for m in missing] == ["tests_off", "host_sandbox"]
    assert "no code delivery can be verified" in missing[0]["means"]
    # Refused before anything changed.
    assert len(sqlite_session.exec(select(Sprint)).all()) == sprints_before

    _ack(sqlite_session, company, "tests_off")
    still = test_client.post("/v1/start_sprint", json=_start_payload(company.id))
    assert still.status_code == 409
    assert [m["kind"] for m in still.json()["detail"]["missing_acknowledgements"]] == [
        "host_sandbox"
    ]

    _ack(sqlite_session, company, "host_sandbox")
    assert (
        test_client.post(
            "/v1/start_sprint", json=_start_payload(company.id)
        ).status_code
        == 200
    )


def test_a_company_with_isolation_and_tests_needs_nothing(
    sqlite_session, test_client, monkeypatch
):
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    company = _company(sqlite_session, sandbox="container")

    assert preconditions.unmet(sqlite_session, company.id) == []
    assert (
        test_client.post(
            "/v1/start_sprint", json=_start_payload(company.id)
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("status", "path"),
    [("planned", "start"), ("paused", "resume"), ("active", "dispatch")],
)
def test_every_endpoint_that_starts_work_refuses(
    sqlite_session, test_client, weak, status, path
):
    company = _company(sqlite_session)
    sprint, _item = _sprint(sqlite_session, company, status)

    answer = test_client.post(f"/v1/sprints/{sprint.id}/{path}")

    assert answer.status_code == 409
    assert answer.json()["detail"]["missing_acknowledgements"]
    sqlite_session.refresh(sprint)
    assert sprint.status == status

    _ack(sqlite_session, company, "tests_off")
    _ack(sqlite_session, company, "host_sandbox")
    assert test_client.post(f"/v1/sprints/{sprint.id}/{path}").status_code == 200


def test_the_worker_pauses_a_sprint_queued_by_another_path(sqlite_session, weak):
    company = _company(sqlite_session)
    sprint, item = _sprint(sqlite_session, company, "active")
    engine = sqlite_session.get_bind()

    assert preconditions.hold_task(engine, item.id) is True

    sqlite_session.expire_all()
    sprint = sqlite_session.get(Sprint, sprint.id)
    assert sprint.status == "paused"
    assert sprint.pause_reason == "Missing acknowledgement: tests_off, host_sandbox"
    assert sqlite_session.get(BacklogItem, item.id).status == "todo"
    # A second task finds the sprint already paused: held, no second pause.
    assert preconditions.hold_task(engine, item.id) is True

    _ack(sqlite_session, company, "tests_off")
    _ack(sqlite_session, company, "host_sandbox")
    assert preconditions.hold_task(engine, item.id) is False


def test_a_withdrawn_or_expired_acknowledgement_no_longer_counts(sqlite_session, weak):
    company = _company(sqlite_session, sandbox="container")
    row = _ack(sqlite_session, company, "tests_off")
    assert preconditions.unmet(sqlite_session, company.id) == []

    acknowledgements.withdraw(
        sqlite_session, company.id, row.id, by="owner@x", user_id=None
    )
    assert [m["kind"] for m in preconditions.unmet(sqlite_session, company.id)] == [
        "tests_off"
    ]
    # The row stays, with who withdrew it.
    kept = sqlite_session.get(CompanyAcknowledgement, row.id)
    assert kept.withdrawn_by == "owner@x" and kept.withdrawn_at is not None

    later = _ack(sqlite_session, company, "tests_off", days=1)
    assert acknowledgements.active(sqlite_session, company.id)
    after_it_ends = later.expires_at + timedelta(seconds=1)
    assert acknowledgements.active(sqlite_session, company.id, now=after_it_ends) == {}


def test_only_an_owner_records_one_with_a_reason_and_an_end(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company = _company(sqlite_session)
    url = f"/v1/companies/{company.id}/acknowledgements"
    viewer = _member(sqlite_session, company.id, "Viewer")
    manager = _member(sqlite_session, company.id, "Manager")
    owner = _member(sqlite_session, company.id, "Owner")
    body = {"kind": "tests_off", "reason": "a trial sprint on our own repo"}

    assert test_client.get(url, headers=viewer).status_code == 200
    assert test_client.post(url, json=body, headers=viewer).status_code == 403
    assert test_client.post(url, json=body, headers=manager).status_code == 403
    for bad in (
        {**body, "kind": "everything"},
        {**body, "reason": "ok"},
        {**body, "days": 0},
        {**body, "days": acknowledgements.MAX_DAYS + 1},
    ):
        assert test_client.post(url, json=bad, headers=owner).status_code == 422

    recorded = test_client.post(url, json=body, headers=owner)
    assert recorded.status_code == 200
    state = recorded.json()
    assert [a["kind"] for a in state["in_force"]] == ["tests_off"]
    assert state["in_force"][0]["reason"] == body["reason"]
    ack_id = state["in_force"][0]["id"]

    audit = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == acknowledgements.RECORDED)
    ).all()
    assert [a.company_id for a in audit] == [company.id]

    assert test_client.delete(f"{url}/{ack_id}", headers=manager).status_code == 403
    withdrawn = test_client.delete(f"{url}/{ack_id}", headers=owner).json()
    assert withdrawn["in_force"] == []
    assert withdrawn["history"][0]["withdrawn_at"] is not None
    assert sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == acknowledgements.WITHDRAWN)
    ).all()
    assert test_client.delete(f"{url}/999999", headers=owner).status_code == 404


def test_health_is_degraded_with_the_reason(monkeypatch):
    from main import app

    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    answer = TestClient(app).get("/health")
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "degraded"
    assert body["verification"]["tests"] == "off"
    assert len(body["verification"]["reasons"]) == 2

    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "container")
    body = TestClient(app).get("/health").json()
    assert body["status"] == "healthy"
    assert body["verification"]["reasons"] == []


def test_the_worst_severity_wins():
    assert healthcheck.worst("healthy", "degraded") == "degraded"
    assert healthcheck.worst("down", "degraded") == "down"
    assert healthcheck.verification(True, "container")["status"] == "healthy"
