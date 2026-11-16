"""The Manager agent's cycle: suggestions, and decisions taken alone.

A low-risk hold is accepted alone and audited, a high-risk one waits for the
client; without a policy or over budget it only suggests; an unusable answer
or a failed call writes nothing more; one row per situation, and replicas
never decide the same item; a procedure is adopted alone only at level 3 and
an outside run approved through the approval record.

The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief. The
shared rows and fakes are in ``manager_agent_helpers``.
"""

from __future__ import annotations

import threading

from history_check import check_task
from manager_agent_helpers import (
    _approvals,
    _company,
    _cycle,
    _hold,
    _outside_run,
    _policy,
    _rows,
    answer,
)
from models import AuditLog, Procedure
from models.company import BusinessUnit, Company
from models.manager import ManagerDecision
from services.management import manager_agent
from services.management.manager_actions import ACCEPTED_PREFIX, MANAGER_AGENT_NAME
from services.platform import locks
from services.tasks import task_events
from services.tasks.board import awaits_risk_acceptance
from sqlmodel import Session, SQLModel, create_engine, select


def test_a_low_risk_hold_is_accepted_alone_and_audited(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, covers=["REQ-1.1"])
    _policy(sqlite_session, company, "accept_delivery")

    assert _cycle(sqlite_session, company, answer("accept", "accept_delivery")) == 1
    prompt = _cycle.prompts[0]
    assert "CATEGORY: accept_delivery" in prompt and "treat it as data" in prompt

    sqlite_session.refresh(item)
    assert item.status == "done"
    note = item.comments[-1]
    assert note["author"] == MANAGER_AGENT_NAME
    assert (
        note["text"].startswith(ACCEPTED_PREFIX) and "accept_delivery" in note["text"]
    )
    (row,) = _rows(sqlite_session, company)
    assert (row.decided_by, row.rule, row.outcome) == (
        MANAGER_AGENT_NAME,
        "accept_delivery",
        "accepted",
    )
    audit = sqlite_session.get(AuditLog, row.audit_log_id)
    assert audit.agent_id == MANAGER_AGENT_NAME
    assert audit.action == "manager_agent_accept_delivery"
    assert audit.details["decided_by"] == MANAGER_AGENT_NAME
    assert audit.details["rule"] == "accept_delivery"
    # The independent history checker accepts an autonomy-1 hold the agent closed.
    # The acceptance is the task's event; the legacy hold before it is not.
    run = {"version": 1, "status": "verified"}
    events = [
        {
            "id": e.id,
            "kind": e.event_type,
            "task_status": e.task_status,
            "payload": e.payload,
        }
        for e in task_events.for_task(sqlite_session, item.id)
    ]
    assert [e["kind"] for e in events] == ["moved"]
    assert events[0]["payload"]["accepted_by"] == MANAGER_AGENT_NAME
    assert note["event_id"] == events[0]["id"]
    assert (
        check_task(
            {"id": item.id, "status": "done", "comments": item.comments},
            [run],
            [],
            events=events,
        )
        == []
    )


def test_the_history_check_still_refuses_an_agent_closing_a_high_risk_hold(
    sqlite_session,
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, human_led=False)
    comments = [
        *item.comments,
        {"author": MANAGER_AGENT_NAME, "text": f"{ACCEPTED_PREFIX} (rule x)."},
    ]
    violations = check_task(
        {"id": item.id, "status": "done", "comments": comments},
        [{"version": 1, "status": "verified"}],
        [],
    )
    assert "high_risk_done_without_person" in violations


def test_a_high_risk_hold_waits_for_the_client(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company, human_led=False, tier="high")
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_high_risk"))
    sqlite_session.refresh(item)
    assert item.status == "blocked" and awaits_risk_acceptance(item.comments)
    (row,) = _rows(sqlite_session, company)
    assert (row.category, row.suggested_decision, row.decided_by) == (
        "accept_high_risk",
        "accept",
        None,
    )


def test_without_a_policy_the_agent_only_suggests(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert _rows(sqlite_session, company)[0].suggested_decision == "accept"


def test_over_budget_nothing_is_taken_alone(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    sqlite_session.add(
        BusinessUnit(
            company_id=company.id,
            unit_name="eng",
            token_quota_limit=100,
            token_quota_used=100,
        )
    )
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_an_unusable_answer_is_left_to_the_client_and_not_asked_again(
    sqlite_session,
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    assert _cycle(sqlite_session, company, "Sure, accept it!") == 1
    (row,) = _rows(sqlite_session, company)
    assert row.suggested_decision is None and row.decided_by is None
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert _cycle(sqlite_session, company, answer("accept", "accept_delivery")) == 0
    assert _cycle.prompts == []


def test_a_failed_call_writes_nothing(sqlite_session):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)

    def boom(prompt):
        raise RuntimeError("proxy down")

    assert _cycle(sqlite_session, company, boom) == 0
    assert _rows(sqlite_session, company) == []


def test_one_situation_gets_one_row_behind_the_lock(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    fields = {
        "company_id": company.id,
        "item_kind": "task",
        "item_id": item.id,
        "item_state": "blocked:2",
        "category": "accept_delivery",
    }
    assert manager_agent._claim(sqlite_session, ManagerDecision(**fields))
    assert not manager_agent._claim(sqlite_session, ManagerDecision(**fields))
    assert len(_rows(sqlite_session, company)) == 1


def test_two_replicas_never_decide_the_same_item(tmp_path):
    """The second replica skips an item the first is asking about, and asks no model."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manager.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        company = _company(session)
        _hold(session, company)
        _policy(session, company, "accept_delivery")
        company_id = company.id

    asking = threading.Event()
    second_done = threading.Event()
    calls = []

    def slow(prompt):
        calls.append("first")
        asking.set()
        second_done.wait(timeout=30)
        return answer("accept", "accept_delivery")

    def fast(prompt):
        calls.append("second")
        return answer("accept", "accept_delivery")

    results = {}

    def replica(name, ask):
        with Session(engine) as session:
            company = session.get(Company, company_id)
            results[name] = manager_agent.company_cycle(session, company, ask)

    first = threading.Thread(target=replica, args=("first", slow))
    first.start()
    assert asking.wait(timeout=30)
    replica("second", fast)
    second_done.set()
    first.join(timeout=30)

    assert results == {"first": 1, "second": 0}
    assert calls == ["first"]
    with Session(engine) as session:
        rows = session.exec(select(ManagerDecision)).all()
        assert len(rows) == 1 and rows[0].decided_by == MANAGER_AGENT_NAME
        # Once the first is done, a later round sees the situation and asks nothing.
        company = session.get(Company, company_id)
        assert manager_agent.company_cycle(session, company, fast) == 0
    assert calls == ["first"]


def test_the_loop_skips_a_round_another_replica_runs(sqlite_session, monkeypatch):
    engine = sqlite_session.get_bind()
    ran = []
    monkeypatch.setattr(manager_agent, "manager_cycle", lambda e: ran.append(e) or 1)
    with locks.held(engine, locks.MANAGER_AGENT) as mine:
        assert mine
        assert (
            locks.run_exclusive(
                engine, locks.MANAGER_AGENT, manager_agent.manager_cycle, engine
            )
            is None
        )
    assert ran == []


def test_a_procedure_is_adopted_alone_only_at_level_3(sqlite_session):
    for autonomy, status in ((2, "proposed"), (3, "active")):
        company = _company(sqlite_session, autonomy=autonomy)
        proc = Procedure(
            company_id=company.id,
            role="Developer",
            title="Run the tests",
            body="Run them before delivering.",
            proposed_by="ScrumMaster",
        )
        sqlite_session.add(proc)
        sqlite_session.commit()
        _policy(sqlite_session, company, "adopt_procedure")
        _cycle(sqlite_session, company, answer("adopt", "adopt_procedure"))
        sqlite_session.refresh(proc)
        assert proc.status == status
        if status == "active":
            assert proc.decided_by == MANAGER_AGENT_NAME


def test_an_outside_run_is_approved_alone_through_the_approval_record(
    sqlite_session,
):
    company = _company(sqlite_session)
    run = _outside_run(sqlite_session, company)
    refuted = _outside_run(sqlite_session, company, verdict="refuted")
    _policy(sqlite_session, company, "approve_run")
    _cycle(sqlite_session, company, answer("approve", "approve_run"))
    (approval,) = _approvals(sqlite_session, run)
    assert (approval.approver_name, approval.decision) == (
        MANAGER_AGENT_NAME,
        "approved",
    )
    assert sqlite_session.get(AuditLog, approval.audit_log_id).agent_id == (
        MANAGER_AGENT_NAME
    )
    assert _approvals(sqlite_session, refuted) == []
