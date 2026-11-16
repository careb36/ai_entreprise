"""The ScrumMaster's track record and the escalation a decision names.

The triage prompt carries how second attempts went (and nothing without
them); a decision is judged by the escalation it names, which the worker
writes into it.

``services/learning/scrum_master.py``: the ScrumMaster answers an escalation
in a fixed format; the decision is recorded as a message to the assignee
(another attempt, with a brief) or to nobody in particular (a person decides),
and read back from the conversation. The shared rows are in
``scrum_master_helpers``.
"""

import scrum_master_helpers
import worker_stages
from models import AgentMessage, Sprint
from scrum_master_helpers import RETRY_ANSWER, TRIAGE, _blocked
from services.learning import scrum_master
from services.loop import carry_over
from services.tasks.board import CARRIED_FROM_PREFIX
from sqlmodel import select

# The shared fixtures.
company = scrum_master_helpers.company


def test_the_triage_prompt_carries_how_second_attempts_went(sqlite_session, company):
    company_row, sprint = company
    closed = Sprint(
        company_id=company_row.id, sprint_name="Sprint 1", status="completed"
    )
    sqlite_session.add(closed)
    sqlite_session.commit()
    sqlite_session.refresh(closed)
    note = f'{CARRIED_FROM_PREFIX} task 1 ("Sprint 0"), which was blocked: too short'
    briefed = f"{note}\n{scrum_master.BRIEF_PREFIX}Write it in full."
    for status, text in (("done", briefed), ("blocked", briefed), ("blocked", note)):
        _blocked(
            sqlite_session,
            closed,
            comments=[{"author": "System", "text": text}],
            status=status,
        )
    # Still open, so its outcome is unknown and it does not count.
    _blocked(sqlite_session, sprint, comments=[{"author": "System", "text": briefed}])
    item = _blocked(sqlite_session, sprint)

    assert scrum_master.track_record(sqlite_session, company_row.id) == {
        "total": 3,
        "done": 1,
        "briefed": 2,
        "briefed_done": 1,
    }
    prompt, _model, _system = scrum_master.prepare_triage(
        sqlite_session, company_row.id, item, TRIAGE
    )
    assert "## How second attempts went in this company" in prompt
    assert "1 of 3 tasks carried into another sprint got done." in prompt
    assert "With a brief from you: 1 of 2; without one: 0 of 1." in prompt
    assert prompt.index("How second attempts went") < prompt.index("DECISION:")


def test_without_second_attempts_the_prompt_has_no_track_record(
    sqlite_session, company
):
    company_row, sprint = company
    item = _blocked(sqlite_session, sprint)

    prompt, _model, _system = scrum_master.prepare_triage(
        sqlite_session, company_row.id, item, TRIAGE
    )

    assert "How second attempts went" not in prompt


def test_a_decision_is_judged_by_the_escalation_it_names(
    sqlite_session, company, monkeypatch
):
    """The worker records which escalation a decision answers. One that names an
    older escalation is dropped however late it was recorded; one that names the
    latest counts, until a person sends the task back after that escalation."""
    from datetime import UTC, datetime, timedelta

    company_row, sprint = company
    item = _blocked(sqlite_session, sprint, title="Answered late")
    now = datetime.now(UTC)

    def escalate(at):
        message = AgentMessage(
            company_id=company_row.id,
            sprint_id=sprint.id,
            backlog_item_id=item.id,
            from_name="Verifier",
            from_role="Verifier",
            to_role="ScrumMaster",
            kind="escalation",
            body=TRIAGE,
            created_at=at,
        )
        sqlite_session.add(message)
        sqlite_session.commit()
        sqlite_session.refresh(message)
        return message

    first = escalate(now - timedelta(seconds=60))
    second = escalate(now - timedelta(seconds=20))
    # The answer to the first escalation lands after the second was posted.
    person = {"decision": "person", "why": "needs a budget", "brief": ""}
    scrum_master.record_decision(
        sqlite_session,
        company_row.id,
        sprint.id,
        item.id,
        "QA",
        person,
        answers=first.id,
    )
    sqlite_session.commit()
    assert scrum_master.decision_for(sqlite_session, item.id) is None
    assert not carry_over.left_for_a_person(sqlite_session, item)

    retry = {"decision": "retry", "why": "cut short", "brief": "Write it in full."}
    recorded = scrum_master.record_decision(
        sqlite_session,
        company_row.id,
        sprint.id,
        item.id,
        "QA",
        retry,
        answers=second.id,
    )
    sqlite_session.commit()
    assert scrum_master.answered_escalation(recorded.body) == second.id
    assert scrum_master.parse_decision_body(recorded.body) == retry
    assert scrum_master.decision_for(sqlite_session, item.id) == retry

    # A send-back after the escalation it answers makes it stale, even though the
    # decision itself was recorded later still.
    item.comments = [
        *item.comments,
        {
            "author": "user",
            "text": "Sent back to work by a person.",
            "created_at": str((now - timedelta(seconds=10)).replace(tzinfo=None)),
        },
    ]
    sqlite_session.add(item)
    sqlite_session.commit()
    assert scrum_master.decision_for(sqlite_session, item.id) is None


def test_the_worker_names_the_escalation_in_the_decision(
    sqlite_session, company, monkeypatch
):
    from services.loop.worker import messages as worker_messages

    company_row, sprint = company
    item = _blocked(sqlite_session, sprint, title="Named by the worker")
    escalation = AgentMessage(
        company_id=company_row.id,
        sprint_id=sprint.id,
        backlog_item_id=item.id,
        from_name="Quality gate",
        from_role="Quality gate",
        to_role="ScrumMaster",
        kind="escalation",
        body=TRIAGE,
    )
    sqlite_session.add(escalation)
    sqlite_session.commit()
    sqlite_session.refresh(escalation)
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    monkeypatch.setattr(scrum_master, "ask_triage", lambda *a, **k: RETRY_ANSWER)

    worker_messages._triage_blocked_task(
        company_row.id, sprint.id, item.id, TRIAGE, escalation.id
    )

    sqlite_session.expire_all()
    decision = sqlite_session.exec(
        select(AgentMessage).where(
            AgentMessage.backlog_item_id == item.id, AgentMessage.kind == "decision"
        )
    ).one()
    assert scrum_master.answered_escalation(decision.body) == escalation.id
    assert scrum_master.decision_for(sqlite_session, item.id)["decision"] == "retry"
