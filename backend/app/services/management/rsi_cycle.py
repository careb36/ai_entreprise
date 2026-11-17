"""The self-improvement cycle in the backend (``docs/design/rsi-loop.md``, PR 10
of section 8; REQ-22 to REQ-26, decisions 1, 3, 4 and 7).

A cycle is one finding worked on from a baseline B to a candidate C, decided
keep, revert or unmeasured by ``services/evidence/fitness.decide`` (never by a
model), and, when kept, merged by a person at C's sha. This module holds the
cycle record (``models/rsi_cycle.RsiCycle``) and the limits the backend
enforces, each refusing with a reason code and audited in the company's
chain (``rsi_cycle_refused``):

- ``rsi_disabled``: ``RSI_ENABLED`` is off (the deployment's kill switch);
  nothing starts, and a running cycle stops at its next step (REQ-24).
- ``not_self_hosting``: the company is not marked by its Owner as hosting
  the factory (``companies.protected_paths_enabled``, decision 3; REQ-3).
  That flag is the company's switch; no second flag is needed.
- ``stopped``: N cycles in a row (``RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP``, 3)
  ended without a keep; nothing starts until a signed-in Owner resumes
  (REQ-23). A Manager, the Manager agent or an API token cannot.
- ``cycle_running``, ``daily_limit``: one cycle at a time, and at most
  ``RSI_CYCLES_PER_DAY`` (1) started per UTC day.
- ``budget_unknown``: the first manual sprint's reported tokens
  (``RSI_FIRST_SPRINT_TOKENS``) are unknown, so no budget can be set and no
  cycle starts. A cycle's budget is ``RSI_CYCLE_TOKEN_BUDGET`` capped at twice
  those tokens (decision 7); passing it ends the cycle ``budget_exceeded``,
  stops its sprint and keeps nothing (REQ-22).
- The size cap (``RSI_MAX_DIFF_FILES`` 20, ``RSI_MAX_DIFF_LINES`` 600) is read
  from the integration's diff the workspace daemon observed in git, never
  from what anyone declares; over it, or unreadable, the cycle is
  ``unmeasured`` and waits for a person (REQ-25). A keep without an observed
  size within the cap is recorded ``unmeasured``.

A kept cycle waits in the Manager inbox as ``merge_self_change``, which is in
``ALWAYS_ESCALATE``: no policy names it and the agent never merges a
self-change, at any autonomy (decision 1: the first five kept changes are
always merged by a person; the automation after them is not built). The
person's merge is recorded by a signed-in Owner, pinned to C's sha (REQ-17).
Every state change is audited ``rsi_cycle_<step>`` (REQ-26).

The steps of a running cycle (sprint, C, tokens, size, decision) are in
``rsi_cycle_steps``; what is read (the state, a cycle, the inbox items) in
``rsi_cycle_view``; a runner's record is imported by ``rsi_cycle_import``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from config import settings
from models import Finding, Sprint, User
from models.rsi_cycle import (
    AWAITING_MERGE,
    AWAITING_PERSON,
    KEPT_STATUSES,
    MERGED,
    REVERTED,
    RUNNING,
    STOPPED,
    UNMEASURED,
    RsiCycle,
)
from sqlmodel import Session, select

from services.contracts import rsi_cycle_record
from services.learning import findings
from services.loop import sprint_control
from services.platform import locks, protected_paths_setting, teams
from services.platform.agent_service import AgentService

CATEGORY = "merge_self_change"
ITEM_KIND = "rsi_cycle"
RUNNER = "rsi-runner"
PERSON_MERGES_REQUIRED = 5  # decision 1: the first five kept changes
BUDGET_MULTIPLIER = 2  # decision 7: twice the first manual sprint
STATUS_BY_DECISION = {
    "keep": AWAITING_MERGE,
    "revert": REVERTED,
    "unmeasured": UNMEASURED,
}
NOT_A_PERSON = "decided by a signed-in Owner of the company, never a token or an agent"


class CycleRefused(Exception):
    """A cycle step refused: ``status`` is the HTTP status, ``code`` the reason."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- The audit -------------------------------------------------------------------


def audit(
    session: Session,
    company_id: int,
    action: str,
    actor: str,
    result: dict[str, Any],
    cycle: RsiCycle | None = None,
) -> None:
    """One entry in the company's chain; it commits the change with it."""
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"rsi_cycle:{cycle.id}" if cycle else f"rsi:{company_id}",
        agent_id=actor,
        action=action,
        details={
            "company_id": company_id,
            "cycle_id": cycle.id if cycle else None,
            "cycle_key": cycle.cycle_key if cycle else None,
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def refuse(
    session: Session,
    company_id: int,
    status: int,
    code: str,
    detail: str,
    *,
    actor: str = RUNNER,
    cycle: RsiCycle | None = None,
) -> CycleRefused:
    """Audit a refusal (``rsi_cycle_refused``) and return the error to raise."""
    session.rollback()
    audit(
        session,
        company_id,
        "rsi_cycle_refused",
        actor,
        {"code": code, "detail": detail},
        cycle,
    )
    return CycleRefused(status, code, detail)


def owner_person(session: Session, company_id: int, person: Any) -> User:
    """The signed-in Owner behind *person*, or a refusal (audited).

    An API token's user, an agent's name, a Manager, a person of another
    company or nobody is refused, as ``routers/sod.owner_person`` refuses.
    """
    ok = (
        isinstance(person, User)
        and getattr(person, "_api_token_company_id", None) is None
        and person.id is not None
        and person.id > 0
        and session.get(User, person.id) is not None
        and teams.member_role(session, company_id, person.id) == "Owner"
    )
    if not ok:
        raise refuse(session, company_id, 403, "not_an_owner", NOT_A_PERSON)
    return session.get(User, person.id)


# -- The limits ------------------------------------------------------------------


def token_budget() -> tuple[int | None, str | None]:
    """The budget per cycle in reported tokens, or ``(None, why)`` when unknown."""
    first = settings.RSI_FIRST_SPRINT_TOKENS
    if isinstance(first, bool) or not isinstance(first, int) or first <= 0:
        return None, "budget_unknown"
    cap = BUDGET_MULTIPLIER * first
    configured = settings.RSI_CYCLE_TOKEN_BUDGET
    if isinstance(configured, int) and configured > 0:
        return min(configured, cap), None
    return cap, None


def company_cycles(session: Session, company_id: int) -> list[RsiCycle]:
    return list(
        session.exec(
            select(RsiCycle)
            .where(RsiCycle.company_id == company_id)
            .order_by(RsiCycle.id.desc())
        ).all()
    )


def _without_keep(rows: list[RsiCycle]) -> list[RsiCycle]:
    """The ended cycles, newest first, since the last keep or the last resume."""
    streak = []
    for row in rows:
        if row.status == RUNNING:
            continue
        if row.status in KEPT_STATUSES or row.resumed_at is not None:
            break
        streak.append(row)
    return streak


def consecutive_without_keep(session: Session, company_id: int) -> int:
    return len(_without_keep(company_cycles(session, company_id)))


def stopped(session: Session, company_id: int) -> bool:
    limit = max(1, settings.RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP)
    return consecutive_without_keep(session, company_id) >= limit


def cycles_today(session: Session, company_id: int, now: datetime | None = None) -> int:
    day = (now or utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(
        1
        for row in company_cycles(session, company_id)
        if row.started_at is not None and day <= row.started_at < day + timedelta(1)
    )


def kept_merged_by_person(session: Session, company_id: int) -> int:
    return sum(
        1
        for row in company_cycles(session, company_id)
        if row.status == MERGED and row.merged_by
    )


def why_not_start(
    session: Session, company_id: int, now: datetime | None = None
) -> tuple[int, str, str] | None:
    """Why no cycle may start now, as ``(status, code, detail)``; None when one may."""
    if not settings.RSI_ENABLED:
        return 409, "rsi_disabled", "RSI_ENABLED is off: no cycle starts"
    if not protected_paths_setting.enabled(session, company_id):
        return (
            409,
            "not_self_hosting",
            (
                "the company is not marked by its Owner as hosting the factory "
                "(protected paths off): no cycle starts (REQ-3)"
            ),
        )
    if stopped(session, company_id):
        return (
            409,
            "stopped",
            (
                f"{consecutive_without_keep(session, company_id)} cycles in a row ended "
                "without a keep: the loop waits until an Owner resumes it (REQ-23)"
            ),
        )
    if any(row.status == RUNNING for row in company_cycles(session, company_id)):
        return 409, "cycle_running", "a cycle of this company is still running"
    if cycles_today(session, company_id, now) >= max(1, settings.RSI_CYCLES_PER_DAY):
        return 429, "daily_limit", "this company already started its cycle today"
    budget, why = token_budget()
    if budget is None:
        return (
            409,
            why or "budget_unknown",
            (
                "the first manual sprint's reported tokens are unknown "
                "(RSI_FIRST_SPRINT_TOKENS): no budget, no cycle"
            ),
        )
    return None


# -- Starting and the steps -------------------------------------------------------


def start(
    session: Session,
    company_id: int,
    finding_id: int,
    baseline_sha: str,
    *,
    started_by: str = RUNNER,
    cycle_key: str | None = None,
    now: datetime | None = None,
) -> RsiCycle:
    """Start a cycle for *finding_id* from B, once every limit allows it.

    The checks, the creation and its commit run under the company's
    ``locks.RSI_CYCLE_START``, so two starts cannot both pass the daily and
    running limits; a start that finds the lock taken is refused.
    """
    engine = session.get_bind()
    with locks.held(engine, locks.RSI_CYCLE_START, company_id) as mine:
        if not mine:
            raise refuse(
                session,
                company_id,
                409,
                "start_in_progress",
                "another start of a cycle for this company is under way",
                actor=started_by,
            )
        return _start(
            session, company_id, finding_id, baseline_sha, started_by, cycle_key, now
        )


def _start(
    session: Session,
    company_id: int,
    finding_id: int,
    baseline_sha: str,
    started_by: str,
    cycle_key: str | None,
    now: datetime | None,
) -> RsiCycle:
    moment = now or utcnow()
    blocked = why_not_start(session, company_id, moment)
    if blocked:
        raise refuse(session, company_id, *blocked, actor=started_by)
    if not rsi_cycle_record.sha(baseline_sha):
        raise refuse(
            session,
            company_id,
            422,
            "bad_baseline",
            "B must be a full commit sha",
            actor=started_by,
        )
    key = cycle_key or f"cycle-{finding_id}-{moment:%Y%m%d}"
    taken = session.exec(
        select(RsiCycle).where(
            RsiCycle.company_id == company_id, RsiCycle.cycle_key == key
        )
    ).first()
    if not rsi_cycle_record.cycle_id_ok(key) or taken is not None:
        raise refuse(
            session,
            company_id,
            422,
            "bad_cycle_key",
            "the cycle's name must be branch-safe and new in this company",
            actor=started_by,
        )
    finding = session.get(Finding, finding_id)
    problem = _finding_problem(session, company_id, finding)
    if problem:
        raise refuse(session, company_id, 409, "finding", problem, actor=started_by)
    if finding.status == "open":
        findings.mark_in_cycle(session, finding)
    budget, _ = token_budget()
    cycle = RsiCycle(
        company_id=company_id,
        cycle_key=key,
        finding_id=finding.id,
        baseline_sha=baseline_sha,
        token_budget=budget,
        started_by=started_by,
        started_at=moment,
        updated_at=moment,
    )
    session.add(cycle)
    session.flush()
    audit(
        session,
        company_id,
        "rsi_cycle_started",
        started_by,
        {
            "finding_id": finding.id,
            "baseline_sha": baseline_sha,
            "token_budget": budget,
        },
        cycle,
    )
    return cycle


def _finding_problem(
    session: Session, company_id: int, finding: Finding | None
) -> str | None:
    if finding is None or finding.company_id != company_id:
        return "no such finding in this company"
    if not finding.target_measure:
        return "the finding has no target measure (REQ-2)"
    if finding.status == "open":
        answer = findings.may_start(session, company_id, finding)
        return None if answer.allowed else f"no cycle may start: {answer.reason}"
    if finding.status != "in_cycle":
        return f"the finding is {finding.status}"
    active = session.exec(
        select(RsiCycle).where(
            RsiCycle.finding_id == finding.id,
            RsiCycle.status.in_((RUNNING, AWAITING_MERGE, AWAITING_PERSON)),
        )
    ).first()
    return "the finding already has a cycle" if active else None


def boundary(session: Session, cycle: RsiCycle, actor: str = RUNNER) -> None:
    """A step boundary: the cycle must be running, and the kill switches on."""
    if cycle.status != RUNNING:
        raise refuse(
            session,
            cycle.company_id,
            409,
            "not_running",
            f"the cycle is {cycle.status}",
            actor=actor,
            cycle=cycle,
        )
    if not settings.RSI_ENABLED:
        why = "rsi_disabled"
    elif not protected_paths_setting.enabled(session, cycle.company_id):
        why = "not_self_hosting"
    else:
        return
    end_cycle(
        session, cycle, STOPPED, "unmeasured", [{"guard": "kill_switch", "code": why}]
    )
    audit(session, cycle.company_id, "rsi_cycle_stopped", actor, {"code": why}, cycle)
    raise CycleRefused(409, why, f"the cycle stopped: {why} (REQ-24)")


def _stop_sprint(session: Session, cycle: RsiCycle) -> int:
    sprint = session.get(Sprint, cycle.sprint_id) if cycle.sprint_id else None
    if sprint is None or sprint.status in ("completed", sprint_control.CANCELLED):
        return 0
    return sprint_control.stop(session, sprint)


def end_cycle(
    session: Session,
    cycle: RsiCycle,
    status: str,
    decision: str,
    reasons: list[Any],
    metrics: list[Any] | dict[str, Any] | None = None,
) -> None:
    """End *cycle* (not committed: the caller's audit commits it)."""
    cycle.status = status
    cycle.decision = decision
    cycle.reasons = list(reasons)
    if metrics is not None:
        cycle.metrics = metrics
    cycle.ended_at = cycle.updated_at = utcnow()
    session.add(cycle)
    if status != AWAITING_MERGE:
        _stop_sprint(session, cycle)
    finding = session.get(Finding, cycle.finding_id) if cycle.finding_id else None
    if finding is not None and finding.status == "in_cycle":
        # No candidate yet: C is B unchanged, and the outcome says so.
        findings.record_outcome(
            session,
            finding,
            produced_by=findings.PLATFORM,
            decision=decision,
            baseline_sha=cycle.baseline_sha or "",
            candidate_sha=cycle.candidate_sha or cycle.baseline_sha or "",
            reasons=list(reasons),
            cycle=cycle.cycle_key,
        )


# -- What a person decides ---------------------------------------------------------


def record_person_merge(
    session: Session, cycle: RsiCycle, merged_sha: str, person: Any
) -> RsiCycle:
    """A signed-in Owner records the merge they made, pinned to C (REQ-17)."""
    owner = owner_person(session, cycle.company_id, person)
    actor = f"human:{owner.email}"
    if cycle.status != AWAITING_MERGE:
        raise refuse(
            session,
            cycle.company_id,
            409,
            "not_kept",
            f"the cycle is {cycle.status}, not kept and waiting for a merge",
            actor=actor,
            cycle=cycle,
        )
    if merged_sha != cycle.candidate_sha:
        raise refuse(
            session,
            cycle.company_id,
            409,
            "not_pinned",
            "a kept change is merged at C's recorded sha; a moved head voids "
            "the decision (REQ-17)",
            actor=actor,
            cycle=cycle,
        )
    ordinal = kept_merged_by_person(session, cycle.company_id) + 1
    cycle.status = MERGED
    cycle.merged_sha = merged_sha
    cycle.merged_by = owner.email
    cycle.merged_at = cycle.updated_at = utcnow()
    session.add(cycle)
    finding = session.get(Finding, cycle.finding_id) if cycle.finding_id else None
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_merged",
        actor,
        {
            "merged_sha": merged_sha,
            "user_id": owner.id,
            "finding_id": finding.id if finding else None,
            "kept_merged_by_person": ordinal,
        },
        cycle,
    )
    return cycle


def resume(session: Session, company_id: int, person: Any) -> dict[str, Any]:
    """A signed-in Owner resumes the loop after the stop (REQ-23)."""
    owner = owner_person(session, company_id, person)
    actor = f"human:{owner.email}"
    streak = _without_keep(company_cycles(session, company_id))
    if not stopped(session, company_id):
        raise refuse(
            session,
            company_id,
            409,
            "not_stopped",
            "the loop is not stopped: nothing to resume",
            actor=actor,
        )
    moment = utcnow()
    for row in streak:
        row.resumed_by, row.resumed_at = owner.email, moment
        session.add(row)
    audit(
        session,
        company_id,
        "rsi_cycle_resumed",
        actor,
        {"user_id": owner.id, "cycles": [row.id for row in streak]},
    )
    return {"company_id": company_id, "resumed": [row.id for row in streak]}
