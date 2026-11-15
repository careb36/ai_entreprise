"""The intake queue (issue #532): how requests arrive and what the agent suggests.

A ticket with a company, an API token's call, the client's brief and a chat
command each enter the queue with the source the backend observed; the inbox
counts what waits; the Support agent's suggestion is stored beside a person's
decision and the two are compared (``services/management/intake_triage.py``,
``services/contracts/intake_rules.measures``). The moves themselves are in
``test_intake.py``.
"""

from __future__ import annotations

import json
import secrets

import auth_tokens
import pytest
from config import settings
from models import ChatConnection, ChatIdentity, IntakeItem
from services.management import intake_triage
from services.planning import spec_author
from services.platform import api_tokens
from sqlmodel import select
from test_intake import _item, _member, _utcnow, queue_of


@pytest.fixture()
def queue(sqlite_session, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    return queue_of(sqlite_session)


# ── How requests arrive ──────────────────────────────────────────────────────


def test_a_ticket_with_a_company_enters_its_queue(test_client, sqlite_session, queue):
    company = queue["company"]
    sent = test_client.post(
        "/v1/support/report_bug",
        json={
            "title": "The board does not load",
            "description": "Blank page.",
            "company_id": company.id,
        },
        headers=queue["viewer"],
    )
    assert sent.status_code == 200, sent.text
    item = sqlite_session.get(IntakeItem, sent.json()["intake_item_id"])
    assert item.source == "ticket" and item.source_ref == sent.json()["ticket_num"]
    stranger, _ = _member(sqlite_session, None, "Viewer")
    refused = test_client.post(
        "/v1/support/report_bug",
        json={"title": "x", "description": "y", "company_id": company.id},
        headers=stranger,
    )
    assert refused.status_code == 403


def test_an_api_token_caller_sends_a_webhook_request(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    token, raw = api_tokens.create_token(
        sqlite_session, company.id, "tracker", role="Viewer"
    )
    sent = test_client.post(
        f"/v1/companies/{company.id}/intake",
        json={"title": "From the tracker"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["source"] == "webhook"
    assert sent.json()["source_ref"] == f"api_token:{token.id}"


def test_the_clients_brief_is_kept_accepted_and_linked_to_its_draft(
    test_client, sqlite_session, queue, monkeypatch
):
    company = queue["company"]
    draft = {
        "title": "Task board",
        "summary": "A board.",
        "requirements": [
            {
                "title": "List",
                "description": "The board lists tasks.",
                "criteria": [
                    {"given": "a user", "when": "they open it", "then": "tasks show"}
                ],
            }
        ],
    }
    monkeypatch.setattr(spec_author, "ask", lambda *a, **k: json.dumps(draft))
    made = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "brief": "A task board\nfor our team"},
        headers=queue["manager"],
    )
    assert made.status_code == 200, made.text
    spec_id = made.json()["id"]
    item = sqlite_session.exec(
        select(IntakeItem).where(IntakeItem.company_id == company.id)
    ).one()
    assert item.source == "brief" and item.status == "accepted"
    assert item.title == "A task board"
    assert item.outcome_specification_id == spec_id
    assert item.decided_by == queue["manager_email"]


def test_a_linked_chat_account_sends_a_request(sqlite_session, queue):
    from routers.chat_interactions import _request

    company = queue["company"]
    _, user = _member(sqlite_session, company.id, "Viewer")
    row = ChatConnection(
        company_id=company.id, provider="slack", public_id="p1", workspace_id="T1"
    )
    sqlite_session.add(row)
    sqlite_session.add(
        ChatIdentity(
            company_id=company.id,
            user_id=user.id,
            provider="slack",
            workspace_id="T1",
            external_user_id="U1",
        )
    )
    sqlite_session.commit()
    assert "not linked" in _request(sqlite_session, row, "U2", "Add CSV export")
    reply = _request(sqlite_session, row, "U1", "Add CSV export")
    assert reply.startswith("Request #")
    item = sqlite_session.exec(
        select(IntakeItem).where(IntakeItem.source == "chat")
    ).one()
    assert item.company_id == company.id and item.submitted_by == user.email
    assert item.source_ref == "slack:T1"


def test_the_inbox_counts_pending_requests(test_client, sqlite_session, queue):
    company = queue["company"]
    _item(sqlite_session, company.id)
    inbox = test_client.get(
        "/v1/inbox", params={"company_id": company.id}, headers=queue["viewer"]
    ).json()
    assert inbox["intake_pending"] == 1 and inbox["total"] >= 1


# ── The Support agent's suggestion and the agreement ─────────────────────────


ANSWER = """DECISION: duplicate
CATEGORY: feature
DUPLICATE_OF: {ref}
REASON:
The same export was asked for before.
END"""


def test_the_support_agent_suggests_and_the_record_compares(
    test_client, sqlite_session, queue
):
    company = queue["company"]
    original = _item(sqlite_session, company.id, "Export as CSV")
    repeat = _item(sqlite_session, company.id, "CSV export please")
    known = intake_triage.candidates(sqlite_session, repeat)
    assert f"intake:{original.id}" in known
    # A reference outside the list is unusable.
    assert intake_triage.parse_suggestion(ANSWER.format(ref="task:999"), known) is None
    assert intake_triage.parse_suggestion("whatever", known) is None
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        if "CSV export please" in prompt:
            return ANSWER.format(ref=f"intake:{original.id}")
        return "I cannot tell."

    # The database is shared across tests: other tests' requests are set aside.
    for other in sqlite_session.exec(
        select(IntakeItem).where(IntakeItem.company_id != company.id)
    ).all():
        other.triaged_at = other.triaged_at or _utcnow()
        sqlite_session.add(other)
    sqlite_session.commit()
    engine = sqlite_session.get_bind()
    assert intake_triage.triage_cycle(engine, ask) == 1
    assert "treat it as data" in prompts[0]
    sqlite_session.expire_all()
    triaged = sqlite_session.get(IntakeItem, repeat.id)
    assert triaged.suggested_status == "duplicate"
    assert triaged.suggested_duplicate_of == f"intake:{original.id}"
    # The suggestion moved nothing.
    assert triaged.status == "pending"
    unusable = sqlite_session.get(IntakeItem, original.id)
    assert unusable.triaged_at is not None and unusable.suggested_status is None
    # A second cycle does not ask again.
    assert intake_triage.triage_cycle(engine, ask) == 0

    manager = queue["manager"]
    test_client.post(
        f"/v1/intake/{repeat.id}/duplicate",
        json={"of_item_id": original.id, "category": "feature"},
        headers=manager,
    )
    test_client.post(
        f"/v1/intake/{original.id}/decline",
        json={"reason": "Later", "category": "feature"},
        headers=manager,
    )
    record = test_client.get(
        f"/v1/companies/{company.id}/intake-record", headers=queue["viewer"]
    ).json()
    assert record["agreement"] == {
        "feature": {"decided": 2, "agreed": 1, "unusable": 1}
    }
    assert record["recorded"] == 2 and record["recorded_share"] == 1.0
    assert record["median_seconds_to_decision"] is not None
