"""Renaming a company, and deleting it with everything it holds
(``routers/company_admin.py``, ``services/platform/company_removal.py``).

An Owner renames; only a signed-in Owner deletes, typing the name back. The
delete removes the company's rows and the rows that hang from them (a
sprint's tasks), leaves another company untouched, records itself in the
deployment's audit row, and is held back while a sprint runs.
"""

import secrets

import audit_signing
import pytest
from models import (
    Agent,
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    Sprint,
    User,
)
from services.platform import api_tokens
from sqlmodel import select
from test_git_workflow import _member, enforcement  # noqa: F401


@pytest.fixture(autouse=True)
def _audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_hex(16)
    monkeypatch.setattr(audit_signing, "_configured_key", lambda: key)


def _company(session, name="Acme"):
    company = Company(company_name=f"{name} {secrets.token_hex(2)}")
    session.add(company)
    session.commit()
    session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="S1", status="completed")
    session.add(sprint)
    session.add(Agent(company_id=company.id, name="Dana", role="DevOps"))
    session.commit()
    session.add(BacklogItem(sprint_id=sprint.id, title="T"))
    session.commit()
    return company, sprint


def test_an_owner_renames_and_a_manager_cannot(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company, _ = _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    manager = _member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}"
    assert (
        test_client.patch(url, json={"company_name": "X"}, headers=manager).status_code
        == 403
    )
    assert (
        test_client.patch(url, json={"company_name": "  "}, headers=owner).status_code
        == 422
    )
    reply = test_client.patch(
        url, json={"company_name": "  La  Fabrique "}, headers=owner
    )
    assert reply.status_code == 200 and reply.json()["company_name"] == "La Fabrique"
    [row] = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "company_renamed")
    ).all()
    assert row.company_id == company.id


def test_the_owner_deletes_everything_the_company_holds_and_nothing_else(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company, sprint = _company(sqlite_session)
    _, other_sprint = _company(sqlite_session, "Other")
    company_id, sprint_id, other_sprint_id = company.id, sprint.id, other_sprint.id
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}"

    preview = test_client.get(f"{url}/deletion", headers=owner).json()
    assert preview["blockers"] == []
    assert preview["rows"]["backlog_items"] == 1 and preview["rows"]["agents"] == 1

    wrong = test_client.request(
        "DELETE", url, json={"confirm_name": "nope"}, headers=owner
    )
    assert wrong.status_code == 422
    reply = test_client.request(
        "DELETE", url, json={"confirm_name": company.company_name}, headers=owner
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["rows"]["sprints"] == 1

    sqlite_session.expunge_all()
    assert sqlite_session.get(Company, company_id) is None
    assert sqlite_session.get(Sprint, sprint_id) is None
    assert not sqlite_session.exec(
        select(CompanyMember).where(CompanyMember.company_id == company_id)
    ).all()
    # The other company keeps all of its rows.
    assert sqlite_session.get(Sprint, other_sprint_id) is not None
    assert sqlite_session.exec(
        select(BacklogItem).where(BacklogItem.sprint_id == other_sprint_id)
    ).one()
    # The deployment's own record of what went.
    [row] = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "company_deleted")
    ).all()
    assert row.company_id is None


def test_a_running_sprint_holds_the_delete_back(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company, sprint = _company(sqlite_session)
    sprint.status = "active"
    sqlite_session.add(sprint)
    sqlite_session.commit()
    owner = _member(sqlite_session, company.id, "Owner")
    url = f"/v1/companies/{company.id}"
    assert test_client.get(f"{url}/deletion", headers=owner).json()["blockers"] == [
        "sprint_running"
    ]
    reply = test_client.request(
        "DELETE", url, json={"confirm_name": company.company_name}, headers=owner
    )
    assert reply.status_code == 409
    assert sqlite_session.get(Company, company.id) is not None


def test_a_manager_or_an_api_token_cannot_delete(
    sqlite_session,
    test_client,
    enforcement,  # noqa: F811
):
    company, _ = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    user = User(
        email=f"o-{secrets.token_hex(3)}@example.com", full_name="O", password_hash="x"
    )
    sqlite_session.add(user)
    sqlite_session.commit()
    sqlite_session.add(
        CompanyMember(company_id=company.id, user_id=user.id, human_role="Owner")
    )
    sqlite_session.commit()
    _, raw = api_tokens.create_token(
        sqlite_session, company.id, "ci", role="Manager", created_by_user_id=user.id
    )
    body = {"confirm_name": company.company_name}
    url = f"/v1/companies/{company.id}"
    for headers in (manager, {"Authorization": f"Bearer {raw}"}):
        assert (
            test_client.request("DELETE", url, json=body, headers=headers).status_code
            == 403
        )
    assert sqlite_session.get(Company, company.id) is not None
