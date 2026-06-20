"""
Unit tests for backend/app/services/company_service.py — CompanyService.

Uses an in-memory SQLite database (via the ``db_session`` fixture from
conftest.py) and mocks external side-effects:

- Neo4j driver        — patched to a no-op to avoid network calls.
- LLM proxy client    — patched to return a deterministic fallback plan.
- Fernet encryption   — plaintext passthrough (no FERNET_SECRET_KEY set).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models import BacklogItem, Company, OutboxEvent, Sprint
from services.company_service import CompanyService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_AGENTS = [
    {"name": "Atlas", "role": "PO", "seniority": "Senior", "specialty": "product", "model": ""},
    {"name": "Nova", "role": "Architect", "seniority": "Senior", "specialty": "design", "model": ""},
    {"name": "Dev", "role": "Developer", "seniority": "Senior", "specialty": "coding", "model": ""},
    {"name": "Tester", "role": "QA", "seniority": "Senior", "specialty": "quality", "model": ""},
]

_NEO4J_PATCH = "services.company_service.get_neo4j_driver"


# ---------------------------------------------------------------------------
# init_company
# ---------------------------------------------------------------------------

class TestInitCompany:
    def test_creates_company_and_sprint(self, db_session):
        """init_company persists a Company, a Sprint, and BacklogItems."""
        with patch(_NEO4J_PATCH, return_value=MagicMock()):
            svc = CompanyService(db_session)
            result = svc.init_company(
                company_name="Acme Corp",
                agents=_MINIMAL_AGENTS,
            )

        assert "company_id" in result
        assert "sprint_id" in result
        assert result["task_count"] >= 0

        company = db_session.get(Company, result["company_id"])
        assert company is not None
        assert company.company_name == "Acme Corp"

        sprint = db_session.get(Sprint, result["sprint_id"])
        assert sprint is not None
        assert sprint.company_id == company.id
        assert sprint.status == "active"

    def test_creates_backlog_items(self, db_session):
        """init_company adds BacklogItems for each foundation task."""
        with patch(_NEO4J_PATCH, return_value=MagicMock()):
            svc = CompanyService(db_session)
            result = svc.init_company(
                company_name="Backlog Corp",
                agents=_MINIMAL_AGENTS,
            )

        sprint_id = result["sprint_id"]
        from sqlmodel import select
        items = db_session.exec(
            select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
        ).all()
        assert len(items) == result["task_count"]
        assert all(item.status == "todo" for item in items)

    def test_creates_outbox_events(self, db_session):
        """init_company creates one OutboxEvent per BacklogItem for NATS dispatch."""
        with patch(_NEO4J_PATCH, return_value=MagicMock()):
            svc = CompanyService(db_session)
            result = svc.init_company(
                company_name="Outbox Corp",
                agents=_MINIMAL_AGENTS,
            )

        sprint_id = result["sprint_id"]
        from sqlmodel import select
        # Find the BacklogItem IDs that belong to this specific sprint.
        item_ids = [
            str(bi.id) for bi in db_session.exec(
                select(BacklogItem).where(BacklogItem.sprint_id == sprint_id)
            ).all()
        ]
        # Filter OutboxEvents to those created for this sprint's items only.
        events = db_session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "backlog_item",
                OutboxEvent.aggregate_id.in_(item_ids),
            )
        ).all()
        assert len(events) == result["task_count"]
        for ev in events:
            payload = ev.payload
            assert payload["subject"] == "task.assign"
            assert "backlog_item_id" in payload["message"]

    def test_creates_credentials_when_permissions_given(self, db_session):
        """init_company stores ProjectCredentials for non-none permission entries."""
        from models import ProjectCredential
        from sqlmodel import select

        with patch(_NEO4J_PATCH, return_value=MagicMock()):
            svc = CompanyService(db_session)
            result = svc.init_company(
                company_name="Cred Corp",
                agents=_MINIMAL_AGENTS,
                permissions={"github_token": "write", "openproject_token": "none"},
            )

        creds = db_session.exec(
            select(ProjectCredential).where(
                ProjectCredential.company_id == result["company_id"]
            )
        ).all()
        # "none" access_level should be skipped; only "write" should be stored.
        assert len(creds) == 1
        assert creds[0].credential_type == "github_token"
        # The encrypted value should use the plaintext: prefix (no Fernet key).
        assert creds[0].encrypted_value.startswith("plaintext:")

    def test_default_scope_size_propagated(self, db_session):
        """default_scope_size passed to init_company appears on the Sprint."""
        with patch(_NEO4J_PATCH, return_value=MagicMock()):
            svc = CompanyService(db_session)
            result = svc.init_company(
                company_name="Scope Corp",
                agents=_MINIMAL_AGENTS,
                default_scope_size="L",
            )

        sprint = db_session.get(Sprint, result["sprint_id"])
        assert sprint.scope_size == "L"


# ---------------------------------------------------------------------------
# approve_task
# ---------------------------------------------------------------------------

class TestApproveTask:
    def _create_sprint_and_item(self, db_session) -> tuple[Sprint, BacklogItem]:
        """Insert a minimal Sprint + BacklogItem pair for approval tests."""
        company = Company(company_name="Test Co")
        db_session.add(company)
        db_session.flush()

        sprint = Sprint(
            company_id=company.id,
            sprint_name="Sprint 1",
            status="active",
            scope_size="M",
        )
        db_session.add(sprint)
        db_session.flush()

        item = BacklogItem(
            sprint_id=sprint.id,
            title="Do something",
            status="in_progress",
            assignee="Developer",
        )
        db_session.add(item)
        db_session.flush()
        return sprint, item

    def test_approve_sets_status_to_in_review(self, db_session):
        _, item = self._create_sprint_and_item(db_session)
        svc = CompanyService(db_session)

        result = svc.approve_task(
            task_id=item.id,
            approver_name="Alice",
            decision="approved",
        )

        assert result["new_status"] == "approved"
        db_session.refresh(item)
        assert item.status == "in_review"

    def test_approve_with_feedback_adds_comment(self, db_session):
        _, item = self._create_sprint_and_item(db_session)
        svc = CompanyService(db_session)

        svc.approve_task(
            task_id=item.id,
            approver_name="Alice",
            decision="approved",
            feedback="Looks great!",
        )

        db_session.refresh(item)
        comments = item.comments or []
        assert any("Looks great!" in c.get("text", "") for c in comments)

    def test_reject_resets_status_to_todo(self, db_session):
        _, item = self._create_sprint_and_item(db_session)
        svc = CompanyService(db_session)

        result = svc.approve_task(
            task_id=item.id,
            approver_name="Bob",
            decision="rejected",
            feedback="Needs rework",
        )

        assert result["item_status"] == "todo"
        db_session.refresh(item)
        assert item.status == "todo"

    def test_reject_creates_outbox_event_with_feedback(self, db_session):
        from sqlmodel import select

        _, item = self._create_sprint_and_item(db_session)
        svc = CompanyService(db_session)

        svc.approve_task(
            task_id=item.id,
            approver_name="Bob",
            decision="rejected",
            feedback="Please fix the auth logic",
        )

        events = db_session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == str(item.id),
                OutboxEvent.event_type == "nats_publish",
            )
        ).all()
        assert len(events) >= 1
        last_event = events[-1]
        payload = last_event.payload
        assert payload["subject"] == "task.assign"
        assert "hitl_feedback" in payload["message"]
        assert "Please fix the auth logic" in payload["message"]["hitl_feedback"]

    def test_reject_without_feedback_uses_default_message(self, db_session):
        from sqlmodel import select

        _, item = self._create_sprint_and_item(db_session)
        svc = CompanyService(db_session)

        svc.approve_task(task_id=item.id, approver_name="Bob", decision="rejected")

        events = db_session.exec(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == str(item.id),
            )
        ).all()
        assert len(events) >= 1
        feedback = events[-1].payload["message"]["hitl_feedback"]
        assert len(feedback) > 0  # default "No feedback provided." message

    def test_approve_task_not_found_raises(self, db_session):
        svc = CompanyService(db_session)
        with pytest.raises(ValueError, match="BacklogItem 99999 not found"):
            svc.approve_task(task_id=99999, approver_name="Alice", decision="approved")


# ---------------------------------------------------------------------------
# get_quotas
# ---------------------------------------------------------------------------

class TestGetQuotas:
    def test_returns_quota_list(self, db_session):
        from models import BusinessUnit

        company = Company(company_name="Quota Corp")
        db_session.add(company)
        db_session.flush()

        db_session.add(BusinessUnit(
            company_id=company.id,
            unit_name="Engineering",
            token_quota_limit=1_000_000,
            token_quota_used=42_000,
        ))
        db_session.add(BusinessUnit(
            company_id=company.id,
            unit_name="Marketing",
            token_quota_limit=500_000,
            token_quota_used=0,
        ))
        db_session.flush()

        svc = CompanyService(db_session)
        quotas = svc.get_quotas(company.id)

        assert len(quotas) == 2
        units = {q["unit"]: q for q in quotas}
        assert units["Engineering"]["limit"] == 1_000_000
        assert units["Engineering"]["used"] == 42_000
        assert units["Marketing"]["used"] == 0

    def test_returns_empty_list_for_unknown_company(self, db_session):
        svc = CompanyService(db_session)
        assert svc.get_quotas(99999) == []
