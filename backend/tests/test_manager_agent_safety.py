"""The Manager agent acting alone safely (review of #388).

A frozen hold is never accepted alone; a send-back or a revoked policy during
the call wins over the answer; the acceptance holds only on the task as it was
read; a pending action left by a stopped replica is resumed and a row that
moved is tried again; the budget counts failed calls, the call is billed to the
company; new evidence is looked at afresh, and the history check dates a
dependency by the agent's acceptance.

The Manager agent (issue #331): suggestions in a fixed format, a policy that
lets it decide alone only by deterministic rules, decisions audited and
overridable, its record against the client's decisions, and the brief. The
shared rows and fakes are in ``manager_agent_helpers``.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from history_check import check_task
from manager_agent_helpers import (
    _company,
    _cycle,
    _hold,
    _outside_run,
    _policy,
    _rows,
    answer,
)
from models import AgentRun, AuditLog, BacklogItem, Procedure
from models.agent_run import Verification
from models.company import BusinessUnit
from models.manager import ManagerDecision
from services.loop.sprint_control import HUMAN_LED_REASON
from services.management import manager_actions, manager_agent, manager_policy
from services.management.manager_actions import ACCEPTED_PREFIX, MANAGER_AGENT_NAME
from services.tasks.board import RISK_HOLD_PREFIX
from sqlalchemy import update
from sqlmodel import select


def test_a_frozen_hold_is_suggested_on_but_never_accepted_alone(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    item.comments = [
        *item.comments,
        {"author": "user", "text": "Freeze after the outage: leave it to me."},
    ]
    sqlite_session.add(item)
    sqlite_session.commit()
    _policy(sqlite_session, company, "accept_delivery")
    assert _cycle(sqlite_session, company, answer("accept", "accept_delivery")) == 1
    (row,) = _rows(sqlite_session, company)
    assert row.suggested_decision == "accept" and row.decided_by is None
    assert row.action_state == manager_agent.NOT_ALLOWED
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_a_send_back_during_the_call_wins_over_the_answer(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")

    def person_acts_meanwhile(prompt):
        item.status = "todo"
        item.comments = [*item.comments, {"author": "System", "text": "Sent back."}]
        sqlite_session.add(item)
        sqlite_session.commit()
        return answer("accept", "accept_delivery")

    _cycle(sqlite_session, company, person_acts_meanwhile)
    (row,) = _rows(sqlite_session, company)
    assert row.decided_by is None and row.action_state == manager_agent.STALE
    sqlite_session.refresh(item)
    assert item.status == "todo"


def test_a_policy_revoked_during_the_call_is_honoured(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")

    def revoke(prompt):
        manager_policy.save(
            sqlite_session, company.id, manager_policy.Policy(rules=[]), "ana"
        )
        sqlite_session.commit()
        return answer("accept", "accept_delivery")

    _cycle(sqlite_session, company, revoke)
    (row,) = _rows(sqlite_session, company)
    assert row.decided_by is None and row.action_state == manager_agent.NOT_ALLOWED
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_the_acceptance_is_conditional_on_the_task_as_it_was_read(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    sqlite_session.refresh(item)
    # What the agent read, before a person commented on the task.
    stale = SimpleNamespace(
        id=item.id, comments=list(item.comments), updated_at=item.updated_at
    )
    sqlite_session.exec(  # type: ignore[call-overload]
        update(BacklogItem)
        .where(BacklogItem.id == item.id)
        .values(updated_at=datetime(2030, 1, 1))
    )
    sqlite_session.commit()
    with pytest.raises(manager_actions.Stale):
        manager_actions._accept(sqlite_session, stale, "Accepted by the Manager agent")
    sqlite_session.rollback()
    assert sqlite_session.get(BacklogItem, item.id).status == "blocked"


def test_a_pending_action_left_by_a_stopped_replica_is_resumed(sqlite_session):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    (situation,) = manager_agent.inbox_items(sqlite_session, company)
    # The suggestion was stored, then the replica stopped before acting.
    sqlite_session.add(
        ManagerDecision(
            company_id=company.id,
            item_kind="task",
            item_id=item.id,
            item_state=situation["state"],
            category="accept_delivery",
            suggested_decision="accept",
            suggested_reason="Verified, low risk.",
            action_state=manager_agent.PENDING,
        )
    )
    sqlite_session.commit()

    def never(prompt):
        raise AssertionError("a pending action needs no new model call")

    _cycle(sqlite_session, company, never)
    (row,) = _rows(sqlite_session, company)
    assert row.action_state == manager_agent.DONE
    assert row.decided_by == MANAGER_AGENT_NAME
    sqlite_session.refresh(item)
    assert item.status == "done"


def test_the_cycle_budget_counts_failed_calls_across_companies(sqlite_session):
    calls = []

    def boom(prompt):
        calls.append(prompt)
        raise RuntimeError("proxy timing out")

    budget = manager_agent.Budget(3)
    for _ in range(2):
        company = _company(sqlite_session)
        for _ in range(3):
            _hold(sqlite_session, company)
        manager_agent.company_cycle(sqlite_session, company, boom, budget)
    assert len(calls) == 3 and budget.left == 0


def test_the_call_is_billed_to_the_company_and_its_tokens_audited(
    sqlite_session, monkeypatch
):
    company = _company(sqlite_session)
    unit = BusinessUnit(company_id=company.id, unit_name="Main", token_quota_limit=1000)
    sqlite_session.add(unit)
    sqlite_session.commit()
    seen = {}

    def fake_ask(prompt, model=None, company_id=None, bu_id=None):
        seen.update(company_id=company_id, bu_id=bu_id)
        return {
            "choices": [{"message": {"content": answer("escalate", "blocked_task")}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
            "usage_status": "reported",
            "served_model": "fake",
        }

    monkeypatch.setattr(manager_agent, "ask_llm", fake_ask)
    call = manager_agent._billed_call(sqlite_session, company.id, None)
    assert "DECISION: escalate" in call("prompt")
    assert seen == {"company_id": company.id, "bu_id": unit.id}
    (audit,) = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company.id,
            AuditLog.action == "manager_agent_call",
        )
    ).all()
    assert audit.cost_tokens == 42 and audit.details["bu_id"] == unit.id


def test_the_prompt_carries_the_standards_and_the_company_s_procedures(
    sqlite_session,
):
    company = _company(sqlite_session)
    _hold(sqlite_session, company)
    sqlite_session.add(
        Procedure(
            company_id=company.id,
            role="All",
            title="Name the evidence",
            body="Cite the verdict in every decision.",
            status="active",
            proposed_by="ScrumMaster",
        )
    )
    sqlite_session.commit()
    _cycle(sqlite_session, company, answer("escalate", "accept_delivery"))
    prompt = _cycle.prompts[0]
    assert "## Quality standards" in prompt and "STD-SEC-1" in prompt
    assert "## Company Procedures" in prompt
    assert "Cite the verdict in every decision." in prompt


def test_an_imported_run_counts_its_changed_files_and_unknown_size_never_passes():
    imported = AgentRun(
        producer="github",
        subject={"changed_files": [f"f{i}.py" for i in range(12)]},
        claims={"files": ["f0.py"]},
    )
    verified = Verification(verdict="verified", checks=[])
    assert manager_agent.run_facts(imported, verified)["files"] == 12
    silent = AgentRun(producer="github", subject={}, claims={})
    facts = {
        **manager_agent.run_facts(silent, verified),
        "risk_tier": "low",
    }
    assert facts["files"] is None
    assert "over the limit" in manager_policy._rule_holds(
        "approve_run", facts, manager_policy.Policy(rules=["approve_run"])
    )


def test_new_evidence_on_a_run_is_looked_at_afresh(sqlite_session):
    company = _company(sqlite_session)
    run = _outside_run(sqlite_session, company, verdict="refuted")
    _cycle(sqlite_session, company, answer("reject", "approve_run"))
    assert _cycle(sqlite_session, company, answer("reject", "approve_run")) == 0
    sqlite_session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": "low"}],
        )
    )
    sqlite_session.commit()
    assert _cycle(sqlite_session, company, answer("approve", "approve_run")) == 1
    assert [r.suggested_decision for r in _rows(sqlite_session, company)] == [
        "reject",
        "approve",
    ]


def test_the_history_check_dates_a_dependency_by_the_agent_s_acceptance():
    dependency = {
        "status": "done",
        "comments": [
            {
                "author": "Developer",
                "text": "the work",
                "created_at": "2026-01-01T10:00:00",
            },
            {
                "author": "QA",
                "text": "APPROVED: ok",
                "created_at": "2026-01-01T11:00:00",
            },
            {
                "author": "System",
                "text": f"{RISK_HOLD_PREFIX} ({HUMAN_LED_REASON}).",
                "created_at": "2026-01-01T11:00:01",
            },
            {
                "author": MANAGER_AGENT_NAME,
                "text": f"{ACCEPTED_PREFIX} under the company's policy.",
                "created_at": "2026-01-01T13:00:00",
            },
        ],
    }
    task = {
        "id": 2,
        "status": "in_review",
        "depends_on": [1],
        "comments": [
            {
                "author": "Developer",
                "text": "early work",
                "created_at": "2026-01-01T12:00:00",
            }
        ],
    }
    violations = check_task(task, [], [], {1: dependency})
    assert any(v.startswith("worked_before_dependency") for v in violations)


def test_a_row_that_moved_without_a_new_situation_is_tried_again(
    sqlite_session, monkeypatch
):
    company = _company(sqlite_session)
    item = _hold(sqlite_session, company)
    _policy(sqlite_session, company, "accept_delivery")
    real_accept = manager_actions._accept
    attempts = []

    def moved_once(session, task, text):
        attempts.append(task.id)
        if len(attempts) == 1:
            raise manager_actions.Stale("updated_at moved")
        return real_accept(session, task, text)

    monkeypatch.setattr(manager_actions, "_accept", moved_once)
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    (row,) = _rows(sqlite_session, company)
    assert row.action_state == manager_agent.PENDING and row.decided_by is None
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(row)
    assert row.action_state == manager_agent.DONE and len(attempts) == 2
    sqlite_session.refresh(item)
    assert item.status == "done"
