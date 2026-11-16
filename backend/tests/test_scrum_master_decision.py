"""The ScrumMaster's decision, recorded, read back and reaching the loop.

The triage is a message the conversation keeps, a person decision is
addressed to nobody, nothing is recorded without a usable answer and other
messages are not a triage; the brief travels with the next attempt, a task
left for a person is not carried on the company's own; the prompt shows only
the rework since the last send-back, a decision answers one escalation only,
and a send-back during or after the triage makes the answer stale.

``services/learning/scrum_master.py``: the ScrumMaster answers an escalation
in a fixed format; the decision is recorded as a message to the assignee
(another attempt, with a brief) or to nobody in particular (a person decides),
and read back from the conversation. The shared rows are in
``scrum_master_helpers``.
"""

import pytest
import scrum_master_helpers
from models import AgentMessage, Sprint
from scrum_master_helpers import RETRY_ANSWER, TRIAGE, _blocked, _decide
from services.learning import scrum_master
from services.loop import carry_over
from sqlmodel import select

# The shared fixtures.
company = scrum_master_helpers.company


def test_the_triage_is_a_message_the_conversation_keeps(
    sqlite_session, company, monkeypatch
):
    company, sprint = company
    item = _blocked(sqlite_session, sprint)
    asked = []

    def _ask(model, system_prompt, prompt, company_id=None):
        asked.append((model, prompt))
        return RETRY_ANSWER

    monkeypatch.setattr(scrum_master, "ask_triage", _ask)

    message = scrum_master.triage(sqlite_session, company.id, sprint.id, item, TRIAGE)
    sqlite_session.commit()

    assert asked[0][0] and "## What blocked it" in asked[0][1]
    recorded = sqlite_session.exec(
        select(AgentMessage).where(AgentMessage.backlog_item_id == item.id)
    ).one()
    assert recorded.id == message.id
    assert (
        recorded.from_role,
        recorded.to_role,
        recorded.kind,
        recorded.sprint_id,
    ) == (
        "ScrumMaster",
        "QA",
        "decision",
        sprint.id,
    )
    assert recorded.body == (
        "Triage decision: another attempt in the next sprint.\n"
        "Why: the attempts were cut short, not wrong.\n"
        "Brief for the next attempt: Write the full strategy document (at least 180 "
        "words) with sections for unit, integration and end-to-end tests, as a path= block."
    )
    assert scrum_master.decision_for(sqlite_session, item.id) == {
        "decision": "retry",
        "why": "the attempts were cut short, not wrong.",
        "brief": "Write the full strategy document (at least 180 words) with sections for unit, integration and end-to-end tests, as a path= block.",
    }


def test_a_person_decision_is_addressed_to_nobody_and_read_back(
    sqlite_session, company, monkeypatch
):
    company, sprint = company
    item = _blocked(sqlite_session, sprint)
    monkeypatch.setattr(
        scrum_master,
        "ask_triage",
        lambda *a: "DECISION: person\nWHY: it needs a budget decision.\nEND",
    )

    message = scrum_master.triage(sqlite_session, company.id, sprint.id, item, TRIAGE)
    sqlite_session.commit()

    assert message.to_role is None
    assert message.body == (
        "Triage decision: leave it for a person.\nWhy: it needs a budget decision."
    )
    assert scrum_master.decision_for(sqlite_session, item.id) == {
        "decision": "person",
        "why": "it needs a budget decision.",
        "brief": "",
    }


@pytest.mark.parametrize(
    "ask",
    [
        lambda *a: (_ for _ in ()).throw(scrum_master.ScrumMasterError("proxy 503")),
        lambda *a: "I would retry it.",
    ],
    ids=["failed-call", "unusable-answer"],
)
def test_without_a_usable_answer_nothing_is_recorded(
    sqlite_session, company, monkeypatch, ask
):
    company, sprint = company
    item = _blocked(sqlite_session, sprint)
    monkeypatch.setattr(scrum_master, "ask_triage", ask)

    assert (
        scrum_master.triage(sqlite_session, company.id, sprint.id, item, TRIAGE) is None
    )
    sqlite_session.commit()

    assert (
        sqlite_session.exec(
            select(AgentMessage).where(AgentMessage.backlog_item_id == item.id)
        ).all()
        == []
    )
    assert scrum_master.decision_for(sqlite_session, item.id) is None


def test_other_scrum_master_messages_are_not_a_triage(sqlite_session, company):
    company, sprint = company
    item = _blocked(sqlite_session, sprint)
    sqlite_session.add(
        AgentMessage(
            company_id=company.id,
            sprint_id=sprint.id,
            backlog_item_id=item.id,
            from_name="ScrumMaster",
            from_role="ScrumMaster",
            to_role="QA",
            kind="decision",
            body='"Create testing strategy document" gets another attempt in sprint "Sprint 1".',
        )
    )
    sqlite_session.commit()

    assert scrum_master.decision_for(sqlite_session, item.id) is None


def test_the_brief_travels_with_the_next_attempt(sqlite_session, company, monkeypatch):
    company, sprint = company
    item = _blocked(sqlite_session, sprint)
    _decide(monkeypatch, sqlite_session, company, sprint, item, RETRY_ANSWER)
    next_sprint = Sprint(company_id=company.id, sprint_name="Sprint 1", status="active")
    sqlite_session.add(next_sprint)
    sqlite_session.commit()

    (clone,) = carry_over.carry_over_on_the_companys_own(
        sqlite_session, company.id, sprint.id, next_sprint, room=4
    )
    sqlite_session.commit()

    note = clone.comments[0]["text"]
    assert note.startswith(
        f'Carried over from task {item.id} ("Sprint 0"), which was blocked: {TRIAGE}'
    )
    assert "\nBrief for the next attempt: Write the full strategy document" in note
    prompt_section = carry_over.previous_attempt_context(clone)
    assert "## Previous attempt" in prompt_section
    assert (
        "Brief for the next attempt: Write the full strategy document" in prompt_section
    )
    told = sqlite_session.exec(
        select(AgentMessage).where(AgentMessage.backlog_item_id == clone.id)
    ).one()
    assert "Brief for the next attempt:" in told.body


def test_a_task_left_for_a_person_is_not_carried_on_the_companys_own(
    sqlite_session, company, monkeypatch
):
    company, sprint = company
    kept = _blocked(sqlite_session, sprint, title="Needs a budget")
    _decide(
        monkeypatch,
        sqlite_session,
        company,
        sprint,
        kept,
        "DECISION: person\nWHY: budget.\nEND",
    )
    retried = _blocked(sqlite_session, sprint, title="Just too short")
    _decide(monkeypatch, sqlite_session, company, sprint, retried, RETRY_ANSWER)
    undecided = _blocked(sqlite_session, sprint, title="No answer")
    next_sprint = Sprint(company_id=company.id, sprint_name="Sprint 1", status="active")
    sqlite_session.add(next_sprint)
    sqlite_session.commit()

    clones = carry_over.carry_over_on_the_companys_own(
        sqlite_session, company.id, sprint.id, next_sprint, room=4
    )
    sqlite_session.commit()

    assert [c.title for c in clones] == ["Just too short", "No answer"]
    assert carry_over.left_for_a_person(sqlite_session, kept)
    assert not carry_over.left_for_a_person(sqlite_session, undecided)
    # A person may still carry it over by hand: the triage is advice.
    (by_hand,) = carry_over.carry_over(
        sqlite_session, company.id, [kept.id], next_sprint, "ana", "Manager"
    )
    assert by_hand.title == "Needs a budget"


def test_the_prompt_shows_only_the_rework_since_the_last_send_back(
    sqlite_session, company
):
    _company, sprint = company
    item = _blocked(
        sqlite_session,
        sprint,
        comments=[
            {"author": "QA Quinn", "text": "NEEDS WORK: the old attempt's problem"},
            {"author": "Quality gate", "text": TRIAGE},
            {"author": "user", "text": "Sent back to work by a person."},
            {"author": "Quality gate", "text": "NEEDS WORK: Too short (30 words)."},
        ],
    )

    prompt = scrum_master.triage_prompt(item, TRIAGE)

    assert "- Quality gate: NEEDS WORK: Too short (30 words)." in prompt
    assert "the old attempt's problem" not in prompt


def test_a_decision_answers_one_escalation_only(sqlite_session, company, monkeypatch):
    """Sent back by a person and blocked again, the task is escalated again; the
    earlier triage no longer counts, so an unanswered new block keeps the default."""
    company, sprint = company
    item = _blocked(sqlite_session, sprint, title="Needs a budget")

    def _escalate():
        sqlite_session.add(
            AgentMessage(
                company_id=company.id,
                sprint_id=sprint.id,
                backlog_item_id=item.id,
                from_name="Quality gate",
                from_role="Quality gate",
                to_role="ScrumMaster",
                kind="escalation",
                body=TRIAGE,
            )
        )
        sqlite_session.commit()

    _escalate()
    _decide(monkeypatch, sqlite_session, company, sprint, item, "DECISION: person\nEND")
    assert scrum_master.decision_for(sqlite_session, item.id)["decision"] == "person"
    assert carry_over.left_for_a_person(sqlite_session, item)

    _escalate()  # blocked again after a send-back; this time no usable answer

    assert scrum_master.decision_for(sqlite_session, item.id) is None
    assert not carry_over.left_for_a_person(sqlite_session, item)

    _decide(monkeypatch, sqlite_session, company, sprint, item, RETRY_ANSWER)
    assert scrum_master.decision_for(sqlite_session, item.id)["decision"] == "retry"
    assert scrum_master.decisions_for(sqlite_session, [item.id, 999_999]) == {
        item.id: scrum_master.decision_for(sqlite_session, item.id)
    }
    assert scrum_master.decisions_for(sqlite_session, []) == {}


def test_a_send_back_during_the_triage_makes_the_answer_stale(
    sqlite_session, company, monkeypatch
):
    """On the Verifier and QA paths the task is blocked before the ScrumMaster
    answers. A person who sends it back in that window acted on the old block,
    so the answer, recorded after the send-back, does not govern the next one."""
    from datetime import UTC, datetime, timedelta

    company, sprint = company
    item = _blocked(sqlite_session, sprint, title="Sent back mid-triage")
    escalated_at = datetime.now(UTC) - timedelta(seconds=30)
    sqlite_session.add(
        AgentMessage(
            company_id=company.id,
            sprint_id=sprint.id,
            backlog_item_id=item.id,
            from_name="Verifier",
            from_role="Verifier",
            to_role="ScrumMaster",
            kind="escalation",
            body=TRIAGE,
            created_at=escalated_at,
        )
    )
    sent_back_at = escalated_at + timedelta(seconds=10)
    item.comments = [
        *item.comments,
        {
            "author": "user",
            "text": "Sent back to work by a person.",
            "created_at": str(sent_back_at.replace(tzinfo=None)),
        },
        {"author": "System", "text": "Agent error: proxy 503"},
    ]
    sqlite_session.add(item)
    sqlite_session.commit()

    # The answer lands now, after the send-back.
    _decide(monkeypatch, sqlite_session, company, sprint, item, "DECISION: person\nEND")

    assert scrum_master.decision_for(sqlite_session, item.id) is None
    assert not carry_over.left_for_a_person(sqlite_session, item)


def test_a_send_back_after_the_decision_makes_it_stale(
    sqlite_session, company, monkeypatch
):
    """A person sent the task back after the ScrumMaster answered, and the next
    attempt failed without an escalation (an agent error): the old decision is
    about another block and no longer counts."""
    from datetime import UTC, datetime, timedelta

    company, sprint = company
    item = _blocked(sqlite_session, sprint, title="Sent back since")
    _decide(monkeypatch, sqlite_session, company, sprint, item, "DECISION: person\nEND")
    assert scrum_master.decision_for(sqlite_session, item.id)["decision"] == "person"

    later = datetime.now(UTC) + timedelta(seconds=5)
    item.comments = [
        *item.comments,
        {
            "author": "user",
            "text": "Sent back to work by a person.",
            "created_at": str(later.replace(tzinfo=None)),
        },
        {"author": "System", "text": "Agent error: proxy 503"},
    ]
    sqlite_session.add(item)
    sqlite_session.commit()

    assert scrum_master.decision_for(sqlite_session, item.id) is None
    assert scrum_master.triages_for(sqlite_session, [item]) == {}
    assert not carry_over.left_for_a_person(sqlite_session, item)
