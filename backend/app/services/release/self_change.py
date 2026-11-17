"""The Owner's hold on merging a self-improvement cycle's work
(``docs/design/rsi-loop.md``, decision 1; review of PR #632).

A cycle (``models/rsi_cycle.RsiCycle``, ``services/management/rsi_cycle.py``)
names its sprint and the integration whose diff measured C. Merging that
integration, or a task branch of that sprint, merges a change to the factory
itself, which only a signed-in Owner does: never the Manager agent, an API
token or a Manager (``merge_self_change`` is in ``ALWAYS_ESCALATE``). The
hold is enforced here, at the merge itself
(``local_merge.integration_merge_spec`` and ``run_merge_spec``, from the web
or the agent alike), not only in the inbox's categories, and it fails
closed: a caller that names no person is refused.
"""

from __future__ import annotations

import re
from typing import Any

from models import BacklogItem, User
from models.agent_run import AgentRun
from models.rsi_cycle import RsiCycle
from sqlmodel import Session, or_, select

from services.platform import teams
from services.platform.agent_service import AgentService
from services.release.git_publish import PublishRefused

AUDIT_REFUSED = "self_change_merge_refused"
DETAIL = (
    "This is a self-improvement cycle's change to the factory: a signed-in "
    "Owner merges it, never the Manager agent, an API token or a Manager"
)
_TASK_REF = re.compile(r"^task-(\d+)$")


def cycle_for(
    session: Session,
    company_id: int,
    *,
    integration_id: int | None = None,
    sprint_id: int | None = None,
) -> RsiCycle | None:
    """The company's cycle that names *integration_id* or *sprint_id*, if any."""
    terms = []
    if integration_id:
        terms.append(RsiCycle.integration_id == integration_id)
    if sprint_id:
        terms.append(RsiCycle.sprint_id == sprint_id)
    if not terms:
        return None
    return session.exec(
        select(RsiCycle).where(RsiCycle.company_id == company_id, or_(*terms))
    ).first()


def sprint_of_run(session: Session, run: AgentRun) -> int | None:
    """The sprint of the task a task-worker run delivered (``task-<id>``)."""
    match = _TASK_REF.match(run.external_ref or "")
    item = session.get(BacklogItem, int(match.group(1))) if match else None
    return item.sprint_id if item else None


def _owner(session: Session, company_id: int, merger: Any) -> bool:
    return (
        isinstance(merger, User)
        and getattr(merger, "_api_token_company_id", None) is None
        and merger.id is not None
        and merger.id > 0
        and session.get(User, merger.id) is not None
        and teams.member_role(session, company_id, merger.id) == "Owner"
    )


def require_owner(
    session: Session,
    company_id: int,
    *,
    integration_id: int | None = None,
    sprint_id: int | None = None,
    merger: Any = None,
) -> None:
    """Refuse (403, audited) the merge of a cycle's work unless *merger* is a
    signed-in Owner of the company; anything else merges as before."""
    cycle = cycle_for(
        session, company_id, integration_id=integration_id, sprint_id=sprint_id
    )
    if cycle is None or _owner(session, company_id, merger):
        return
    who = merger.email if isinstance(merger, User) else str(merger or "nobody")
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"rsi_cycle:{cycle.id}",
        agent_id=who,
        action=AUDIT_REFUSED,
        details={"company_id": company_id, "cycle_id": cycle.id},
        result={"integration_id": integration_id, "sprint_id": sprint_id},
        decision_signature=None,
        cost_tokens=0,
    )
    raise PublishRefused(403, DETAIL)
