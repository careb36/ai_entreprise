"""The project wiki through the API (issue #445, ``routers/wiki.py``): the
roles that read, edit and decide, the history and the decisions, and a
rejected edit. The shared rows are in ``project_wiki_helpers``.
"""

from __future__ import annotations

import secrets
from uuid import uuid4

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    Company,
    CompanyMember,
    User,
)
from project_wiki_helpers import _project
from services.contracts import wiki
from services.learning import wiki_store
from sqlmodel import select

# ── The API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company, _sprint, _item, run = _project(sqlite_session)
    other = Company(company_name=f"Other {uuid4().hex[:6]}")
    sqlite_session.add(other)
    sqlite_session.commit()

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
        "company": company,
        "run": run,
        "viewer": member(company.id, "Viewer"),
        "manager": member(company.id, "Manager"),
        "outsider": member(other.id, "Manager"),
    }


def test_roles_edit_history_and_decisions(test_client, sqlite_session, api):
    cid, run = api["company"].id, api["run"]
    url = f"/v1/companies/{cid}/wiki/auth"
    body = {
        "kind": "concept",
        "title": "Authentication",
        "body": f"Tokens last 15 minutes [run:{run.id}].\n\n<script>alert(1)</script>\n",
    }
    assert test_client.put(url, json=body).status_code == 401
    assert test_client.put(url, json=body, headers=api["viewer"]).status_code == 403
    assert test_client.put(url, json=body, headers=api["outsider"]).status_code == 403
    written = test_client.put(url, json=body, headers=api["manager"])
    assert written.status_code == 200, written.text
    page = written.json()
    assert page["status"] == "current" and page["person_locked"] is True
    assert page["blocks"][0]["sources"][0] == {
        "kind": "run",
        "ref": str(run.id),
        "known": True,
        "verdict": "verified",
        "task_id": run.subject["backlog_item_id"],
    }
    # The body is stored as written; the UI renders it without raw HTML.
    assert "<script>" in page["body"]
    assert page["links"] == [{"kind": "run", "target": str(run.id), "sha256": None}]

    bad = test_client.put(
        url, json=body | {"body": "Invented [run:99999999]."}, headers=api["manager"]
    )
    assert bad.status_code == 422 and bad.json()["detail"] == {
        "unknown": ["[run:99999999]"]
    }
    assert (
        test_client.put(
            url, json=body | {"kind": "rumour"}, headers=api["manager"]
        ).status_code
        == 422
    )

    listed = test_client.get(f"/v1/companies/{cid}/wiki", headers=api["viewer"])
    assert listed.status_code == 200 and listed.json()["pages"][0]["slug"] == "auth"
    assert "body" not in listed.json()["pages"][0]
    assert (
        test_client.get(
            f"/v1/companies/{cid}/wiki", headers=api["outsider"]
        ).status_code
        == 403
    )

    _page, proposal = wiki_store.save(
        sqlite_session,
        cid,
        slug="auth",
        kind="concept",
        title="Authentication",
        body=f"Tokens last 30 minutes [run:{run.id}].\n",
        author_kind=wiki.AGENT,
        author="TechWriter",
    )
    sqlite_session.commit()
    shown = test_client.get(url, headers=api["viewer"]).json()
    assert (
        shown["proposed_version"]["id"] == proposal.id and "15 minutes" in shown["body"]
    )
    accept = f"/v1/wiki_versions/{proposal.id}/accept"
    assert test_client.post(accept, headers=api["viewer"]).status_code == 403
    accepted = test_client.post(accept, headers=api["manager"])
    assert accepted.status_code == 200 and "30 minutes" in accepted.json()["body"]
    assert test_client.post(accept, headers=api["manager"]).status_code == 409
    history = test_client.get(f"{url}/versions", headers=api["viewer"]).json()[
        "versions"
    ]
    assert [(v["number"], v["author_kind"], v["status"]) for v in history] == [
        (2, "agent", "current"),
        (1, "person", "superseded"),
    ]
    assert (
        test_client.get(
            f"/v1/companies/{cid}/wiki/nope", headers=api["viewer"]
        ).status_code
        == 404
    )

    settings_url = f"/v1/companies/{cid}/wiki-settings"
    assert (
        test_client.put(
            settings_url, json={"write_to_repo": True}, headers=api["viewer"]
        ).status_code
        == 403
    )
    assert test_client.put(
        settings_url, json={"write_to_repo": True}, headers=api["manager"]
    ).json() == {"write_to_repo": True}
    linted = test_client.post(f"/v1/companies/{cid}/wiki-lint", headers=api["manager"])
    assert linted.status_code == 200 and linted.json() == {"stale": {}}
    sqlite_session.expire_all()
    actions = [
        r.action
        for r in sqlite_session.exec(
            select(AuditLog).where(AuditLog.interaction_id == f"company:{cid}:wiki")
        ).all()
    ]
    assert actions == [
        "wiki_page_edited",
        "wiki_version_accepted",
        "wiki_settings_changed",
        "wiki_linted",
    ]


def test_reject_through_the_api(test_client, sqlite_session, api):
    cid, run = api["company"].id, api["run"]
    wiki_store.save(
        sqlite_session,
        cid,
        slug="glossary",
        kind="glossary",
        title="Glossary",
        body="Mine.\n",
        author_kind=wiki.PERSON,
        author="lead@example.com",
    )
    _p, proposal = wiki_store.save(
        sqlite_session,
        cid,
        slug="glossary",
        kind="glossary",
        title="Glossary",
        body=f"Theirs [run:{run.id}].\n",
        author_kind=wiki.AGENT,
        author="TechWriter",
    )
    sqlite_session.commit()
    rejected = test_client.post(
        f"/v1/wiki_versions/{proposal.id}/reject",
        json={"reason": "no"},
        headers=api["manager"],
    )
    assert rejected.status_code == 200 and rejected.json()["proposed_version"] is None
    assert (
        test_client.post(
            "/v1/wiki_versions/999999/reject", json={}, headers=api["manager"]
        ).status_code
        == 404
    )
