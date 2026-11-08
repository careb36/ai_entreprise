"""What the client decided on the items of issue #394, read from what they
did, never reported (``services/manager_record.py`` counts it):

- a branch merged locally, or published, by someone other than the agent;
- a delivery undone by a person (the undo's audit row);
- a specification or an architecture approved by someone other than the agent;
- open questions answered while the agent's suggestion waited;
- a sprint paused for credit that is running again.
"""

from __future__ import annotations

from models import Architecture, AuditLog, Specification, Sprint
from models.integration import SprintIntegration
from models.manager import ManagerDecision
from models.review import RunPublication
from sqlmodel import Session, select

AGENT = "Manager agent"


def _publication(session: Session, row: ManagerDecision, local: bool) -> str | None:
    query = select(RunPublication).where(
        RunPublication.agent_run_id == row.item_id,
        RunPublication.company_id == row.company_id,
        RunPublication.published_by != AGENT,
    )
    query = query.where(
        RunPublication.how == "local" if local else RunPublication.how != "local"
    )
    if session.exec(query).first() is None:
        return None
    return "merge" if local else "publish"


def _merge(session: Session, row: ManagerDecision) -> str | None:
    return _publication(session, row, local=True)


def _publish(session: Session, row: ManagerDecision) -> str | None:
    return _publication(session, row, local=False)


def _integration(session: Session, row: ManagerDecision) -> str | None:
    integration = session.get(SprintIntegration, row.item_id)
    merged = ((integration.details or {}) if integration else {}).get("local_merge")
    if isinstance(merged, dict) and merged.get("merged_by") != AGENT:
        return "merge"
    return None


def _delivery(session: Session, row: ManagerDecision) -> str | None:
    from models import AgentRun

    from services.delivery_undo import UNDO_ACTION

    run = session.get(AgentRun, row.item_id)
    if run is None:
        return None
    undone = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == row.company_id,
            AuditLog.action == UNDO_ACTION,
            AuditLog.interaction_id == f"agent-run:{run.external_ref}",
            AuditLog.agent_id.startswith("human:"),
        )
    ).first()
    return "undo" if undone is not None else None


def _approval(model):
    def read(session: Session, row: ManagerDecision) -> str | None:
        doc = session.get(model, row.item_id)
        if doc is None or doc.status == "draft" or not doc.approved_by:
            return None
        return "approve" if doc.approved_by != AGENT else None

    return read


def _questions(model):
    def read(session: Session, row: ManagerDecision) -> str | None:
        doc = session.get(model, row.item_id)
        if doc is None:
            return None
        open_now = sum(
            1
            for q in doc.questions or []
            if isinstance(q, dict) and not str(q.get("answer") or "").strip()
        )
        by_agent = any(
            isinstance(q, dict) and q.get("answered_by") == AGENT
            for q in doc.questions or []
        )
        was_open = int((row.evidence or {}).get("open_questions") or 0)
        return "answer" if open_now < was_open and not by_agent else None

    return read


def _sprint(session: Session, row: ManagerDecision) -> str | None:
    sprint = session.get(Sprint, row.item_id)
    return "resume" if sprint is not None and sprint.status == "active" else None


READERS = {
    "task_branch": _merge,
    "integration": _integration,
    "publication": _publish,
    "delivery": _delivery,
    "specification": _approval(Specification),
    "architecture": _approval(Architecture),
    "specification_questions": _questions(Specification),
    "architecture_questions": _questions(Architecture),
    "sprint": _sprint,
}
