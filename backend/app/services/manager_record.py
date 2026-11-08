"""How the Manager agent fared against its client (issue #331).

Counted from the database, never reported by anyone:

- for each item the agent suggested on and the client then decided, whether
  the suggestion matched (an unusable answer counts as a miss, and is
  counted); the client's decision is read from what they did: the person's
  note on the task after the agent looked (a send-back, an acceptance), a
  carry-over, the procedure's status and who decided it, or the run's latest
  approval by someone other than the agent;
- for each decision the agent took alone, whether the client overrode it.

Both by category. A rule whose override rate rises (``flagged_rules``: at
least ``FLAG_MIN_OVERRIDES`` overrides among its last ``RECENT`` decisions, at
a higher rate than before them) is flagged; the agent stops applying it
until a Manager looks: only decisions after the policy was last saved count,
so a Manager saving it again clears the flag. The agent never widens a
policy. The agent's next prompt
carries these numbers (``record_context``) and ``GET /v1/manager_record``
shows them.
"""

from __future__ import annotations

from typing import Any

from models import AgentRunApproval, BacklogItem, Procedure
from models.manager import ManagerDecision, ManagerPolicy
from sqlmodel import Session, select

from services import task_projection
from services.board import SEND_BACK_MARKER, comment_list
from services.carry_over import carried_over

MANAGER_AGENT_NAME = "Manager agent"
RECENT = 5
FLAG_MIN_OVERRIDES = 2
# Who decided a procedure on nobody's behalf: the agent, or autonomy level 3.
_NOT_THE_CLIENT = {MANAGER_AGENT_NAME, "autonomy", None}


def _task_decision(session: Session, row: ManagerDecision) -> str | None:
    item = session.get(BacklogItem, row.item_id)
    if item is None:
        return None
    if carried_over(item, task_projection.record_of(session, item)):
        return "carry_over"
    seen = int((row.evidence or {}).get("comments_seen") or 0)
    notes = [
        c
        for c in comment_list(item.comments)[seen:]
        if str(c.get("author") or "").strip().lower() == "user"
    ]
    for note in notes:
        if str(note.get("text") or "").startswith(SEND_BACK_MARKER):
            return "send_back"
    if notes and item.status == "done":
        return "accept"
    return None


def _procedure_decision(session: Session, row: ManagerDecision) -> str | None:
    procedure = session.get(Procedure, row.item_id)
    if procedure is None or procedure.decided_by in _NOT_THE_CLIENT:
        return None
    if procedure.status == "rejected":
        return "reject"
    return "adopt" if procedure.status != "proposed" else None


def _run_decision(session: Session, row: ManagerDecision) -> str | None:
    approval = session.exec(
        select(AgentRunApproval)
        .where(
            AgentRunApproval.agent_run_id == row.item_id,
            AgentRunApproval.company_id == row.company_id,
            AgentRunApproval.approver_name != MANAGER_AGENT_NAME,
        )
        .order_by(AgentRunApproval.decided_at.desc(), AgentRunApproval.id.desc())
    ).first()
    if approval is None:
        return None
    return {"approved": "approve", "rejected": "reject"}.get(approval.decision)


def client_decision(session: Session, row: ManagerDecision) -> str | None:
    """What the client decided on the item the agent suggested on, or ``None``."""
    from services.manager_record_extra import READERS

    reader = {
        "task": _task_decision,
        "procedure": _procedure_decision,
        "run": _run_decision,
        **READERS,
    }.get(row.item_kind)
    return reader(session, row) if reader else None


def _empty() -> dict[str, Any]:
    return {
        "suggested": 0,
        "decided": 0,
        "agreed": 0,
        "unusable": 0,
        "taken_alone": 0,
        "overridden": 0,
        "override_rate": None,
    }


def _rate(overridden: int, taken: int) -> float | None:
    return round(overridden / taken, 3) if taken else None


def _flagged(acted: list[ManagerDecision]) -> list[str]:
    by_rule: dict[str, list[ManagerDecision]] = {}
    for row in sorted(acted, key=lambda r: (r.decided_at, r.id)):
        by_rule.setdefault(row.rule or "", []).append(row)
    flagged = []
    for rule, rows in by_rule.items():
        recent, earlier = rows[-RECENT:], rows[:-RECENT]
        recent_over = sum(1 for r in recent if r.overridden_at)
        earlier_over = sum(1 for r in earlier if r.overridden_at)
        if recent_over < FLAG_MIN_OVERRIDES:
            continue
        if recent_over / len(recent) > (earlier_over / len(earlier) if earlier else 0):
            flagged.append(rule)
    return sorted(flagged)


def track_record(session: Session, company_id: int) -> dict[str, Any]:
    """The agent's record in *company_id*, by category and in total."""
    rows = session.exec(
        select(ManagerDecision).where(ManagerDecision.company_id == company_id)
    ).all()
    policy = session.exec(
        select(ManagerPolicy).where(ManagerPolicy.company_id == company_id)
    ).first()
    since = policy.updated_at if policy else None
    categories: dict[str, dict[str, Any]] = {}
    total = _empty()
    acted = []
    for row in rows:
        counts = categories.setdefault(row.category, _empty())
        for bucket in (counts, total):
            bucket["suggested"] += int(row.suggested_decision is not None)
        if row.decided_by == MANAGER_AGENT_NAME:
            if since is None or (row.decided_at and row.decided_at >= since):
                acted.append(row)
            for bucket in (counts, total):
                bucket["taken_alone"] += 1
                bucket["overridden"] += int(row.overridden_at is not None)
            continue
        decision = client_decision(session, row)
        if decision is None:
            continue
        for bucket in (counts, total):
            bucket["decided"] += 1
            bucket["agreed"] += int(row.suggested_decision == decision)
            bucket["unusable"] += int(row.suggested_decision is None)
    for bucket in (*categories.values(), total):
        bucket["override_rate"] = _rate(bucket["overridden"], bucket["taken_alone"])
    return {
        "categories": categories,
        "total": total,
        "flagged_rules": _flagged(acted),
    }


def record_context(record: dict[str, Any]) -> str:
    """The prompt section with how the agent fared, or ''."""
    lines = []
    for category, c in sorted(record.get("categories", {}).items()):
        parts = []
        if c["decided"]:
            part = f"your suggestion matched the client's decision {c['agreed']} of {c['decided']}"
            if c["unusable"]:
                part += f" ({c['unusable']} without a usable answer from you)"
            parts.append(part)
        if c["taken_alone"]:
            parts.append(
                f"the client overrode {c['overridden']} of the {c['taken_alone']} "
                "decisions you took alone"
            )
        if parts:
            lines.append(f"- {category}: " + "; ".join(parts))
    if record.get("flagged_rules"):
        lines.append(
            "- flagged (overrides rising, you do not apply them): "
            + ", ".join(record["flagged_rules"])
        )
    if not lines:
        return ""
    return (
        "\n\nHow your earlier suggestions and decisions compared with the "
        "client's in this company (counted from the database, not reported by "
        "anyone):\n" + "\n".join(lines)
    )
