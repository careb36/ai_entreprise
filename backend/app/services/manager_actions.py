"""What the Manager agent does when its policy lets it decide alone, and the
client's override (issue #331).

Each decision goes through the code a person's decision goes through:

- ``accept_delivery``: the task QA approved that waits for a person (only for
  autonomy level 1, ``services/manager_policy.py``) is marked done with a
  note, as a Manager's "Accept" does (``PATCH /v1/backlog/{id}``), authored
  by the Manager agent;
- ``adopt_procedure``: ``procedures.adopt``, as ``POST /procedures/{id}/adopt``;
- ``approve_run``: ``run_review.record_approval``, as ``POST /approve``;
- ``accept_high_risk`` (the strict rule): the same acceptance as
  ``accept_delivery``, its note naming the rule ``history_check`` reads;
- the rest of issue #394 (merge, undo, publish, send back, carry over,
  answer, resume, approve a specification or an architecture):
  ``services/manager_actions_extra.py``, which says what each override does.

Every one is audited with ``decided_by: "Manager agent"`` and the rule that
allowed it. An override (``POST /v1/manager_decisions/{id}/override``, a
Manager) takes the decision back the way a person would undo it, is audited
as the person's, and is counted in ``services/manager_record.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import AgentRun, AgentRunApproval, BacklogItem, Procedure
from models.manager import ManagerDecision
from sqlalchemy import update
from sqlmodel import Session, select

from services import (
    locks,
    manager_actions_extra,
    procedures,
    run_review,
    task_events,
    task_projection,
)
from services.agent_service import AgentService
from services.carry_over import carried_over

MANAGER_AGENT_NAME = "Manager agent"
# The note on a task the agent accepted; history_check.py restates it.
ACCEPTED_PREFIX = "Accepted by the Manager agent"
OVERRIDE_PREFIX = "Override:"


OVERRIDE_LOCK = "manager-override"
# The item changed since it was read (or no daemon answered): nothing was
# changed and the next cycle reads it again. The daemon or a check refused.
Stale = manager_actions_extra.Stale
Failed = manager_actions_extra.Failed


class OverrideRefused(Exception):
    """An override that cannot act: ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _accept(session: Session, item: BacklogItem, text: str) -> None:
    """Mark the held task done only if nobody touched it since it was read.

    One conditional ``UPDATE … WHERE status = 'blocked' AND updated_at = …``
    claims it: a person's send-back, comment or acceptance in between changes
    the row, and the agent changes nothing (``Stale``). The acceptance is then
    the task's event (issue #324), rendered as the agent's note.
    """
    result = session.exec(  # type: ignore[call-overload]
        update(BacklogItem)
        .where(
            BacklogItem.id == item.id,
            BacklogItem.status == "blocked",
            BacklogItem.updated_at == item.updated_at,
        )
        .values(status="done", updated_at=_now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise Stale(f"task {item.id} changed since the agent read it")
    session.expire(item)
    task_events.record(
        session,
        item,
        "moved",
        MANAGER_AGENT_NAME,
        author=MANAGER_AGENT_NAME,
        status="done",
        text=text,
        payload={"accepted_by": MANAGER_AGENT_NAME},
    )


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
    """Take the decision *row* suggests, as *rule* allowed; returns the outcome.

    One transaction: the change, the decision row and the audit commit
    together, so a replica that stops midway leaves the row pending and the
    item untouched. Raises ``Stale`` when the item changed since it was read.
    """
    reason = row.suggested_reason or ""
    facts: dict[str, Any] = {}
    if row.category in manager_actions_extra.CATEGORIES:
        outcome, facts = manager_actions_extra.act(session, row, rule)
    elif row.category in ("accept_delivery", "accept_high_risk"):
        item = session.get(BacklogItem, row.item_id)
        if item is None:
            raise Stale("the task is gone")
        _accept(
            session,
            item,
            f"{ACCEPTED_PREFIX} under the company's policy (rule {rule}, "
            f"decision #{row.id}): {reason}",
        )
        outcome = "accepted"
    elif row.category == "adopt_procedure":
        procedure = session.get(Procedure, row.item_id)
        if procedure is None or procedure.status != "proposed":
            raise Stale("the procedure is no longer proposed")
        procedures.adopt(session, procedure, MANAGER_AGENT_NAME)
        outcome = "adopted"
    elif row.category == "approve_run":
        run = session.get(AgentRun, row.item_id)
        if run is None or _latest_approval(session, row.item_id) is not None:
            raise Stale("the run was decided meanwhile")
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
    row.action_state = "done"
    session.add(row)
    audit = _audit(
        session,
        row,
        MANAGER_AGENT_NAME,
        f"manager_agent_{row.category}",
        {
            "outcome": outcome,
            "decision": row.suggested_decision,
            "reason": reason,
            "facts": facts,
        },
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


def _latest_approval(session: Session, run_id: int) -> AgentRunApproval | None:
    return session.exec(
        select(AgentRunApproval)
        .where(AgentRunApproval.agent_run_id == run_id)
        .order_by(AgentRunApproval.decided_at.desc(), AgentRunApproval.id.desc())
    ).first()


def _acceptance_is_latest(item: BacklogItem, row: ManagerDecision) -> bool:
    """The agent's acceptance of decision *row* is the last thing a person or
    the agent wrote on the task: nobody reopened or decided it since."""
    marker = f"decision #{row.id})"
    for comment in reversed([c for c in item.comments or [] if isinstance(c, dict)]):
        author = str(comment.get("author") or "").strip().lower()
        if author not in ("user", MANAGER_AGENT_NAME.lower()):
            continue
        text = str(comment.get("text") or "")
        return text.startswith(ACCEPTED_PREFIX) and marker in text
    return False


def override(session: Session, row: ManagerDecision, actor: str) -> dict[str, Any]:
    """The client takes back a decision the agent took alone.

    Once: the override holds a lock on the decision and claims it with a
    conditional update, so a second request at the same time gets a 409. And
    only while the agent's decision is still the latest on the item: a later
    acceptance, approval or retirement is not undone through an older one.
    """
    with locks.held(session.get_bind(), OVERRIDE_LOCK, row.id) as mine:
        if not mine:
            raise OverrideRefused(409, "This decision is being overridden already.")
        session.refresh(row)
        return _override(session, row, actor)


def _override(session: Session, row: ManagerDecision, actor: str) -> dict[str, Any]:
    if row.decided_by != MANAGER_AGENT_NAME:
        raise OverrideRefused(409, "The Manager agent did not decide this item.")
    now = _now()
    claimed = session.exec(  # type: ignore[call-overload]
        update(ManagerDecision)
        .where(
            ManagerDecision.id == row.id,
            ManagerDecision.overridden_at.is_(None),
        )
        .values(overridden_by=actor, overridden_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise OverrideRefused(409, "This decision was already overridden.")
    note = None
    if row.category in manager_actions_extra.CATEGORIES:
        try:
            outcome = manager_actions_extra.override(session, row, actor)
        except manager_actions_extra.Refused as exc:
            raise OverrideRefused(exc.status, exc.detail) from exc
        note = manager_actions_extra.override_note(row)
    elif row.category in ("accept_delivery", "accept_high_risk"):
        item = session.get(BacklogItem, row.item_id)
        if item is None or carried_over(item, task_projection.record_of(session, item)):
            raise OverrideRefused(409, "The task was carried into another sprint.")
        if item.status != "done":
            raise OverrideRefused(
                409, f"The task is {item.status} now, no longer as the agent left it."
            )
        if not _acceptance_is_latest(item, row):
            raise OverrideRefused(
                409, "The task was decided again after the agent's acceptance."
            )
        # Back to waiting for a person: the hold note is still the latest verdict.
        task_events.record(
            session,
            item,
            "moved",
            task_events.PERSON,
            status="blocked",
            text=f"{OVERRIDE_PREFIX} {actor} took back the Manager agent's acceptance "
            f"(decision #{row.id}); the delivery waits for a person again.",
        )
        outcome = "waits_for_a_person"
    elif row.category == "adopt_procedure":
        procedure = session.get(Procedure, row.item_id)
        if (
            procedure is None
            or procedure.status != "active"
            or procedure.decided_by != MANAGER_AGENT_NAME
        ):
            raise OverrideRefused(
                409, "The procedure is no longer active as the agent left it."
            )
        procedures.retire(session, procedure, actor)
        restored = _restore_superseded(session, procedure)
        outcome = f"retired, {restored} restored"
    elif row.category == "approve_run":
        run = session.get(AgentRun, row.item_id)
        latest = _latest_approval(session, row.item_id)
        if (
            run is None
            or latest is None
            or latest.approver_name != MANAGER_AGENT_NAME
            or latest.decision != "approved"
        ):
            raise OverrideRefused(
                409, "The run was decided again after the agent's approval."
            )
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
    session.refresh(row)
    audit = _audit(
        session,
        row,
        f"human:{actor}",
        "manager_agent_override",
        {"outcome": outcome, "overridden_by": actor, "note": note},
    )
    row.override_audit_log_id = audit.id
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "outcome": outcome, "overridden_by": actor, "note": note}
