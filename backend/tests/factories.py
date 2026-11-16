"""
Test data, written straight to the database.

A test that needs a company, its members, a sprint, tasks, runs or
verifications builds them here, with plain model inserts, instead of through
``services.loop`` or ``services.management`` (``CompanyService``, the sprint
routes) or a helper borrowed from another test file. A test that sets up its
data through the top contexts is tied to them in the test map
(``docs/architecture/contexts.json``), so a change below them selects it for
nothing; this module imports only the models, ``auth_tokens`` and ``config``.

Each function adds its row, commits, refreshes it and returns it; keyword
arguments are the model's fields and override the defaults below, which are
the models' own unless named. ``scripts/affected_tests.py`` reads a change to
this file as a change to every test that imports it.

    company = factories.company(session)
    headers = factories.member_headers(session, company, "Manager")
    sprint = factories.sprint(session, company)
    item = factories.task(session, sprint, status="in_review")
    run = factories.run(session, company, task=item)
    factories.verification(session, run, verdict="verified")
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import auth_tokens
import pytest
from config import settings
from models import BacklogItem, Company, CompanyMember, Sprint, User
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session


def _save(session: Session, row: Any) -> Any:
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _id(row_or_id: Any) -> int | None:
    return getattr(row_or_id, "id", row_or_id)


# --- people ------------------------------------------------------------------


@pytest.fixture
def enforcement(monkeypatch):
    """Authentication on, with a signing key made for the test."""
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def company(session: Session, name: str | None = None, **fields: Any) -> Company:
    """A company with a unique name; autonomy 2 and the Git workflow on, as
    the model sets them."""
    return _save(
        session,
        Company(company_name=name or f"Company {secrets.token_hex(3)}", **fields),
    )


def user(session: Session, role: str = "Viewer", **fields: Any) -> User:
    """A user named after *role*, with a unique address and no usable password."""
    fields.setdefault("email", f"{role.lower()}-{secrets.token_hex(6)}@example.com")
    fields.setdefault("full_name", role)
    fields.setdefault("password_hash", "unused")
    return _save(session, User(**fields))


def member(
    session: Session, company_or_id: Any, role: str = "Viewer", **fields: Any
) -> User:
    """A user who holds *role* in the company (a row or an id); with ``None``,
    a user who belongs to no company."""
    person = user(session, role, **fields)
    if company_or_id is not None:
        _save(
            session,
            CompanyMember(
                company_id=_id(company_or_id), user_id=person.id, human_role=role
            ),
        )
    return person


def headers(person: User) -> dict[str, str]:
    """The bearer header of *person*'s access token (under ``enforcement``,
    the key of the test)."""
    token = auth_tokens.create_access_token(user_id=person.id, email=person.email)
    return {"Authorization": f"Bearer {token}"}


def member_headers(
    session: Session, company_or_id: Any, role: str = "Viewer"
) -> dict[str, str]:
    """``member`` and ``headers`` in one: the header of a new member."""
    return headers(member(session, company_or_id, role))


# --- work --------------------------------------------------------------------


def sprint(session: Session, company_or_id: Any, **fields: Any) -> Sprint:
    """An active sprint of the company (a row or an id)."""
    fields.setdefault("sprint_name", "Sprint")
    return _save(session, Sprint(company_id=_id(company_or_id), **fields))


def task(session: Session, sprint_or_id: Any, **fields: Any) -> BacklogItem:
    """A task (a backlog item) of the sprint, in ``todo`` unless told."""
    fields.setdefault("title", "Task")
    return _save(session, BacklogItem(sprint_id=_id(sprint_or_id), **fields))


def run(
    session: Session,
    company_or_id: Any,
    *,
    task: BacklogItem | None = None,
    artifact_text: str = "",
    **fields: Any,
) -> AgentRun:
    """A run of the company. With *task*, the task worker's run of that task
    (``external_ref`` ``task-<id>`` and the ``subject`` the worker records:
    sprint, task and title); the artifact's digest and size are those of
    *artifact_text*."""
    data = artifact_text.encode("utf-8")
    if task is not None:
        fields.setdefault("external_ref", f"task-{task.id}")
        fields.setdefault(
            "subject",
            {
                "sprint_id": task.sprint_id,
                "backlog_item_id": task.id,
                "title": task.title,
            },
        )
    fields.setdefault("external_ref", f"run-{secrets.token_hex(4)}")
    fields.setdefault("producer", TASK_WORKER_PRODUCER)
    fields.setdefault("artifact_digest", hashlib.sha256(data).hexdigest())
    fields.setdefault("artifact_size", len(data))
    return _save(
        session,
        AgentRun(
            company_id=_id(company_or_id),
            artifact_text=artifact_text or None,
            **fields,
        ),
    )


def verification(session: Session, agent_run: AgentRun, **fields: Any) -> Verification:
    """The Verifier's record of *agent_run*, ``verified`` unless told."""
    fields.setdefault("verdict", "verified")
    return _save(
        session,
        Verification(
            company_id=agent_run.company_id, agent_run_id=agent_run.id, **fields
        ),
    )
