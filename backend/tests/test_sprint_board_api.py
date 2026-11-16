"""The sprint board through the API (issue #439, ``routers/board_notes.py``): a
Manager adds a note and marks one wrong while a Viewer reads, and an unknown
sprint or note is a 404. The shared rows are in ``sprint_board_helpers``.
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
    TaskEvent,
    User,
)
from sqlmodel import select

from sprint_board_helpers import _setup

# ── The API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(sqlite_session, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    company, sprint, items = _setup(sqlite_session)
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
        "sprint": sprint,
        "items": items,
        "viewer": member(company.id, "Viewer"),
        "manager": member(company.id, "Manager"),
        "outsider": member(other.id, "Manager"),
    }


def test_a_manager_adds_and_marks_wrong_a_viewer_reads(
    test_client, sqlite_session, api
):
    sid = api["sprint"].id
    url = f"/v1/sprints/{sid}/board_notes"
    body = {"kind": "decision", "text": "Dates are ISO 8601", "files": ["src/dates.py"]}

    assert test_client.post(url, json=body, headers=api["viewer"]).status_code == 403
    assert test_client.post(url, json=body, headers=api["outsider"]).status_code == 403
    assert test_client.post(url, json=body).status_code == 401
    created = test_client.post(url, json=body, headers=api["manager"])
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["author_role"] == "person" and note["author_name"].startswith(
        "manager-"
    )
    assert note["links"]["files"] == ["src/dates.py"]
    assert note["observed"] == {"files": {"src/dates.py": None}}

    bad = test_client.post(
        url, json={"kind": "rumour", "text": "x"}, headers=api["manager"]
    )
    assert bad.status_code == 422

    listed = test_client.get(url, headers=api["viewer"])
    assert listed.status_code == 200
    assert [n["id"] for n in listed.json()["notes"]] == [note["id"]]
    assert listed.json()["metrics"]["notes"]["active"] == 1
    assert test_client.get(url, headers=api["outsider"]).status_code == 403

    mark = f"/v1/board_notes/{note['id']}/mark_wrong"
    assert test_client.post(mark, json={}, headers=api["viewer"]).status_code == 403
    marked = test_client.post(
        mark, json={"reason": "Not agreed"}, headers=api["manager"]
    )
    assert marked.status_code == 200
    assert marked.json()["status"] == "marked_wrong"
    assert marked.json()["status_reason"] == "Not agreed"
    assert marked.json()["text"] == "Dates are ISO 8601"  # the claim is untouched
    again = test_client.post(mark, json={}, headers=api["manager"])
    assert again.status_code == 409

    sqlite_session.expire_all()
    actions = {
        row.action
        for row in sqlite_session.exec(
            select(AuditLog).where(AuditLog.interaction_id == f"sprint:{sid}:board")
        ).all()
    }
    assert actions == {"board_note_added", "board_note_marked_wrong"}
    sprint_events = sqlite_session.exec(
        select(TaskEvent).where(TaskEvent.sprint_id == sid, TaskEvent.task_id.is_(None))
    ).all()
    assert [(e.event_type, e.actor, e.payload["action"]) for e in sprint_events] == [
        ("board_note", "person", "added"),
        ("board_note", "person", "marked_wrong"),
    ]


def test_unknown_sprint_and_note_are_404(test_client, api):
    assert (
        test_client.get(
            "/v1/sprints/999999/board_notes", headers=api["manager"]
        ).status_code
        == 404
    )
    assert (
        test_client.post(
            "/v1/board_notes/999999/mark_wrong", json={}, headers=api["manager"]
        ).status_code
        == 404
    )
