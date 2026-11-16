"""The test data factories (``tests/factories.py``): each row is saved with
the models' defaults, a member holds the role in the company, a header signs
in as that member, and a task's run is the task worker's run of it."""

import hashlib

import factories
import pytest
from config import settings
from factories import enforcement  # noqa: F401 - the fixture, by name
from models import CompanyMember
from models.agent_run import TASK_WORKER_PRODUCER
from sqlmodel import select


def test_a_company_sprint_and_task_keep_the_models_defaults(sqlite_session):
    company = factories.company(sqlite_session)
    other = factories.company(sqlite_session, "Acme", autonomy_level=3)
    sprint = factories.sprint(sqlite_session, company)
    item = factories.task(sqlite_session, sprint.id, priority="high")

    assert company.id and company.company_name != other.company_name
    assert (company.autonomy_level, company.git_workflow) == (2, True)
    assert (other.company_name, other.autonomy_level) == ("Acme", 3)
    assert (sprint.company_id, sprint.status) == (company.id, "active")
    assert (item.sprint_id, item.status, item.priority) == (sprint.id, "todo", "high")


def test_a_member_holds_the_role_and_a_user_alone_holds_none(sqlite_session):
    company = factories.company(sqlite_session)
    manager = factories.member(sqlite_session, company, "Manager")
    alone = factories.member(sqlite_session, None, "Viewer")

    rows = sqlite_session.exec(select(CompanyMember)).all()
    assert [(r.company_id, r.user_id, r.human_role) for r in rows] == [
        (company.id, manager.id, "Manager")
    ]
    assert alone.id and alone.email != manager.email


@pytest.mark.usefixtures("enforcement")
def test_a_member_s_header_signs_in_as_that_member(sqlite_session, test_client):
    assert settings.AUTH_REQUIRED is True
    company = factories.company(sqlite_session)
    viewer = factories.member_headers(sqlite_session, company.id, "Viewer")

    assert test_client.get("/v1/auth/me").status_code == 401
    me = test_client.get("/v1/auth/me", headers=viewer)
    assert me.status_code == 200, me.text


def test_a_task_s_run_and_its_verification(sqlite_session):
    company = factories.company(sqlite_session)
    item = factories.task(sqlite_session, factories.sprint(sqlite_session, company))
    run = factories.run(sqlite_session, company, task=item, artifact_text="done")
    alone = factories.run(sqlite_session, company.id, producer="cli")
    check = factories.verification(sqlite_session, run)

    assert (run.external_ref, run.producer) == (f"task-{item.id}", TASK_WORKER_PRODUCER)
    assert run.subject == {
        "sprint_id": item.sprint_id,
        "backlog_item_id": item.id,
        "title": item.title,
    }
    assert run.artifact_digest == hashlib.sha256(b"done").hexdigest()
    assert (run.artifact_size, run.artifact_text) == (4, "done")
    assert alone.external_ref != run.external_ref and alone.artifact_size == 0
    assert (check.agent_run_id, check.company_id, check.verdict) == (
        run.id,
        company.id,
        "verified",
    )
