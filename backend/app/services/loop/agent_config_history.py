"""Every change to an agent's configuration is a revision, and can be undone.

An agent's prompt, model, persona and budget decide what it does for the
company. Until 2026-11-22 ``CompanyService.update_agent`` overwrote them in
place with no trace: a change in the sprint metrics could not be attributed to
a change of prompt or model, and a bad change could not be taken back. That
is against the factory's own rule that deterministic metrics keep or revert a
change.

A revision is an audit row (``AUDIT_ACTION``), so it enters the company's
hash chain (``audit_chain.py``) and cannot be edited after the fact: the
fields changed, their values before and after, who changed them (a person or
an API token, ``sod.actor_of``), the reason when one was given, and, for a
rollback, the revision it undoes. ``revisions`` lists an agent's revisions,
newest first; ``rollback`` puts back the values a revision replaced, through
the same update and the same Manager check, and records that as a revision
of its own. A field changed again since the revision is put back too: the
rollback restores what the revision replaced, and says which fields moved
since (``changed_since``) so the person sees it.

Taken from Paperclip's agent configuration revisions (MIT:
``packages/db/src/schema/agent_config_revisions.ts``,
``server/src/routes/agents.ts`` rollback): before/after, changed keys, the
actor, the source and ``rolledBackFromRevisionId``. Here the revision lives in
the chained audit log instead of a table of its own.
"""

from __future__ import annotations

from typing import Any

from models import Agent
from models.audit import AuditLog
from sqlmodel import Session, select

from services.platform.agent_service import AgentService

AUDIT_ACTION = "agent_config_changed"
# The fields a Manager edits (``CompanyService.update_agent``).
CONFIG_FIELDS = (
    "name",
    "role",
    "persona",
    "seniority",
    "specialty",
    "model",
    "system_prompt",
    "token_budget",
)
SOURCE_EDIT, SOURCE_ROLLBACK = "edit", "rollback"


class RevisionRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def snapshot(agent: Agent) -> dict[str, Any]:
    return {field: getattr(agent, field) for field in CONFIG_FIELDS}


def interaction(agent_id: int) -> str:
    return f"agent:{agent_id}:config"


def record(
    session: Session,
    agent: Agent,
    before: dict[str, Any],
    *,
    actor: dict[str, Any] | None,
    reason: str | None = None,
    source: str = SOURCE_EDIT,
    rolled_back_from: int | None = None,
) -> AuditLog | None:
    """Audit the change from *before* to the agent's current values, in the
    caller's transaction; nothing when no field changed."""
    after = snapshot(agent)
    changed = [f for f in CONFIG_FIELDS if before.get(f) != after.get(f)]
    if not changed:
        return None
    actor = actor or {}
    return AgentService().audit_interaction(
        session=session,
        company_id=agent.company_id,
        interaction_id=interaction(agent.id),
        agent_id=f"human:{actor.get('by') or 'A person'}",
        action=AUDIT_ACTION,
        details={
            "company_id": str(agent.company_id),
            "agent": agent.id,
            "user_id": actor.get("user_id"),
            "token_id": actor.get("token_id"),
            "source": source,
            "rolled_back_from": rolled_back_from,
            "reason": (reason or "").strip()[:500] or None,
        },
        result={
            "changed": changed,
            "from": {f: before.get(f) for f in changed},
            "to": {f: after.get(f) for f in changed},
        },
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def _row(row: AuditLog) -> dict[str, Any]:
    details = row.details if isinstance(row.details, dict) else {}
    result = row.result if isinstance(row.result, dict) else {}
    return {
        "revision_id": row.id,
        "at": row.created_at.isoformat() if row.created_at else None,
        "by": (row.agent_id or "").removeprefix("human:"),
        "user_id": details.get("user_id"),
        "token_id": details.get("token_id"),
        "source": details.get("source") or SOURCE_EDIT,
        "rolled_back_from": details.get("rolled_back_from"),
        "reason": details.get("reason"),
        "changed": list(result.get("changed") or []),
        "from": dict(result.get("from") or {}),
        "to": dict(result.get("to") or {}),
    }


def revisions(session: Session, agent: Agent) -> list[dict[str, Any]]:
    """The agent's revisions, newest first."""
    rows = session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == agent.company_id,
            AuditLog.interaction_id == interaction(agent.id),
            AuditLog.action == AUDIT_ACTION,
        )
        .order_by(AuditLog.id.desc())
    ).all()
    return [_row(row) for row in rows]


def rollback_payload(
    session: Session, agent: Agent, revision_id: int
) -> tuple[dict[str, Any], list[str]]:
    """The values *revision_id* replaced, as an update payload, and the fields
    that changed again since it."""
    row = session.get(AuditLog, revision_id)
    if (
        row is None
        or row.company_id != agent.company_id
        or row.interaction_id != interaction(agent.id)
        or row.action != AUDIT_ACTION
    ):
        raise RevisionRefused(404, "No such revision of this agent")
    revision = _row(row)
    restore = {
        f: revision["from"].get(f) for f in revision["changed"] if f in CONFIG_FIELDS
    }
    current = snapshot(agent)
    moved = [f for f in restore if current.get(f) != revision["to"].get(f)]
    if all(current.get(f) == v for f, v in restore.items()):
        raise RevisionRefused(
            409, "The agent already has the values this revision replaced"
        )
    return restore, moved
