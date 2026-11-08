"""What the Manager agent does when its policy lets it decide alone, and the
client's override (issue #331).

Each decision goes through the code a person's decision goes through:

- ``accept_delivery``: the task QA approved that waits for a person (only for
  autonomy level 1, ``services/manager_policy.py``) is marked done with a
  note, as a Manager's "Accept" does (``PATCH /v1/backlog/{id}``), authored
  by the Manager agent;
- ``adopt_procedure``: ``procedures.adopt``, as ``POST /procedures/{id}/adopt``;
- ``approve_run``: ``run_review.record_approval``, as ``POST /approve``.

Every one is audited with ``decided_by: "Manager agent"`` and the rule that
allowed it. An override (``POST /v1/manager_decisions/{id}/override``, a
Manager) takes the decision back the way a person would undo it, is audited
as the person's, and is counted in ``services/manager_record.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import AgentRun, BacklogItem, Procedure
from models.manager import ManagerDecision
from sqlmodel import Session, select

from services import procedures, run_review
from services.agent_service import AgentService
from services.carry_over import carried_over

MANAGER_AGENT_NAME = "Manager agent"
# The note on a task the agent accepted; history_check.py restates it.
ACCEPTED_PREFIX = "Accepted by the Manager agent"
OVERRIDE_PREFIX = "Override:"


class OverrideRefused(Exception):
    """An override that cannot act: ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _note(item: BacklogItem, author: str, text: str) -> None:
    comments = list(item.comments or [])
    comments.append({"author": author, "text": text, "created_at": str(_now())})
    item.comments = comments
    item.updated_at = _now()


def _audit(
    session: Session, row: ManagerDecision, agent_id: str, action: str, result: dict
):
    return AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"manager-decision:{row.id}",
        agent_id=agent_id,
        action=action,
        details={
            "company_id": str(row.company_id),
            "manager_decision_id": row.id,
            "item_kind": row.item_kind,
            "item_id": row.item_id,
            "category": row.category,
            "rule": row.rule,
            "decided_by": MANAGER_AGENT_NAME,
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def act(session: Session, row: ManagerDecision, rule: str) -> str:
    """Take the decision *row* suggests, as *rule* allowed; returns the outcome."""
    reason = row.suggested_reason or ""
    if row.category == "accept_delivery":
        item = session.get(BacklogItem, row.item_id)
        item.status = "done"
        _note(
            item,
            MANAGER_AGENT_NAME,
            f"{ACCEPTED_PREFIX} under the company's policy (rule {rule}, "
            f"decision #{row.id}): {reason}",
        )
        session.add(item)
        outcome = "accepted"
    elif row.category == "adopt_procedure":
        procedure = session.get(Procedure, row.item_id)
        procedures.adopt(session, procedure, MANAGER_AGENT_NAME)
        outcome = "adopted"
    elif row.category == "approve_run":
        run = session.get(AgentRun, row.item_id)
        run_review.record_approval(
            session,
            run,
            approver_name=MANAGER_AGENT_NAME,
            decision="approved",
            feedback=reason,
            actor_id=MANAGER_AGENT_NAME,
        )
        outcome = "approved"
    else:  # pragma: no cover - the policy names no other category
        raise ValueError(f"no action for {row.category}")
    row.decided_by = MANAGER_AGENT_NAME
    row.rule = rule
    row.outcome = outcome
    row.decided_at = _now()
    session.add(row)
    audit = _audit(
        session,
        row,
        MANAGER_AGENT_NAME,
        f"manager_agent_{row.category}",
        {"outcome": outcome, "decision": row.suggested_decision, "reason": reason},
    )
    row.audit_log_id = audit.id
    session.add(row)
    session.commit()
    return outcome


def _restore_superseded(session: Session, procedure: Procedure) -> int:
    """Bring back what *procedure*'s adoption retired; returns how many."""
    restored = 0
    for old in session.exec(
        select(Procedure).where(
            Procedure.company_id == procedure.company_id,
            Procedure.status == "retired",
        )
    ).all():
        outcome = old.outcome or {}
        if outcome.get("result") != "superseded" or outcome.get("by") != procedure.id:
            continue
        old.status = "active"
        old.retired_at = None
        old.outcome = {
            k: v for k, v in outcome.items() if k not in ("result", "by")
        } or None
        session.add(old)
        restored += 1
    return restored


def override(session: Session, row: ManagerDecision, actor: str) -> dict[str, Any]:
    """The client takes back a decision the agent took alone."""
    if row.decided_by != MANAGER_AGENT_NAME:
        raise OverrideRefused(409, "The Manager agent did not decide this item.")
    if row.overridden_at is not None:
        raise OverrideRefused(409, "This decision was already overridden.")
    if row.category == "accept_delivery":
        item = session.get(BacklogItem, row.item_id)
        if item is None or carried_over(item):
            raise OverrideRefused(409, "The task was carried into another sprint.")
        if item.status != "done":
            raise OverrideRefused(
                409, f"The task is {item.status} now, no longer as the agent left it."
            )
        # Back to waiting for a person: the hold note is still the latest verdict.
        item.status = "blocked"
        _note(
            item,
            "user",
            f"{OVERRIDE_PREFIX} {actor} took back the Manager agent's acceptance "
            f"(decision #{row.id}); the delivery waits for a person again.",
        )
        session.add(item)
        outcome = "waits_for_a_person"
    elif row.category == "adopt_procedure":
        procedure = session.get(Procedure, row.item_id)
        if procedure is None or procedure.status != "active":
            raise OverrideRefused(
                409, "The procedure is no longer active as the agent left it."
            )
        procedures.retire(session, procedure, actor)
        restored = _restore_superseded(session, procedure)
        outcome = f"retired, {restored} restored"
    elif row.category == "approve_run":
        run = session.get(AgentRun, row.item_id)
        run_review.record_approval(
            session,
            run,
            approver_name=actor,
            decision="rejected",
            feedback=f"{OVERRIDE_PREFIX} the Manager agent's approval taken back.",
        )
        outcome = "rejected"
    else:  # pragma: no cover
        raise OverrideRefused(409, "Nothing to override.")
    row.overridden_by = actor
    row.overridden_at = _now()
    session.add(row)
    audit = _audit(
        session,
        row,
        f"human:{actor}",
        "manager_agent_override",
        {"outcome": outcome, "overridden_by": actor},
    )
    row.override_audit_log_id = audit.id
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "outcome": outcome, "overridden_by": actor}
