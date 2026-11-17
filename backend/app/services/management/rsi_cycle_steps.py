"""The steps of a running self-improvement cycle (``docs/design/rsi-loop.md``,
PR 10 of section 8; REQ-22, REQ-24, REQ-25, REQ-26).

Each step is a boundary (``rsi_cycle.boundary``): the cycle must be running
and both kill switches on, or the cycle stops there and its sprint with it.
The runner records the cycle's sprint and C; the reported tokens are read
from the sprint's turns (``run_cost``) and checked against the budget; C's
size is read from the integration's diff the workspace daemon observed in
git, never declared; and the fitness decision is recorded, a keep only with
C, an observed size within the cap and a known budget. Each step is audited
``rsi_cycle_<step>``.
"""

from __future__ import annotations

from typing import Any

from config import settings
from models.rsi_cycle import AWAITING_PERSON, BUDGET_EXCEEDED, RsiCycle
from sqlmodel import Session

from services.contracts import rsi_cycle_record
from services.management.rsi_cycle import (
    RUNNER,
    STATUS_BY_DECISION,
    audit,
    boundary,
    end_cycle,
    refuse,
    utcnow,
)
from services.models import run_cost


def record_sprint(session: Session, cycle: RsiCycle, sprint_id: int) -> RsiCycle:
    boundary(session, cycle)
    cycle.sprint_id = sprint_id
    cycle.updated_at = utcnow()
    session.add(cycle)
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_sprint",
        RUNNER,
        {"sprint_id": sprint_id},
        cycle,
    )
    return cycle


def record_candidate(
    session: Session,
    cycle: RsiCycle,
    candidate_sha: str,
    *,
    integration_id: int | None = None,
    pull_request: int | None = None,
) -> RsiCycle:
    """C, the pull request's head sha, and the integration whose diff is read."""
    boundary(session, cycle)
    if not rsi_cycle_record.sha(candidate_sha):
        raise refuse(
            session,
            cycle.company_id,
            422,
            "bad_candidate",
            "C must be a full commit sha",
            cycle=cycle,
        )
    cycle.candidate_sha = candidate_sha
    cycle.integration_id = integration_id
    cycle.pull_request = pull_request
    cycle.updated_at = utcnow()
    session.add(cycle)
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_candidate",
        RUNNER,
        {
            "candidate_sha": candidate_sha,
            "integration_id": integration_id,
            "pull_request": pull_request,
        },
        cycle,
    )
    return cycle


def account_tokens(
    session: Session, cycle: RsiCycle, bench_tokens: int = 0
) -> RsiCycle:
    """The cycle's reported tokens: its sprint's, from the audit log's turns
    (``run_cost``, the provider's numbers), plus the bench runner's sum. Over
    the budget, the cycle ends ``budget_exceeded`` and its sprint stops."""
    boundary(session, cycle)
    if (
        isinstance(bench_tokens, bool)
        or not isinstance(bench_tokens, int)
        or bench_tokens < 0
    ):
        raise refuse(
            session,
            cycle.company_id,
            422,
            "bad_tokens",
            "the bench's tokens are a count",
            cycle=cycle,
        )
    sprint = 0
    if cycle.sprint_id:
        costs = run_cost.sprint_costs(session, cycle.company_id, [cycle.sprint_id])
        sprint = int(costs[cycle.sprint_id]["reported_tokens"])
    cycle.tokens_reported = sprint + bench_tokens
    cycle.updated_at = utcnow()
    session.add(cycle)
    budget = cycle.token_budget
    if budget is None or cycle.tokens_reported > budget:
        reason = {
            "guard": "budget",
            "code": BUDGET_EXCEEDED,
            "tokens": cycle.tokens_reported,
            "budget": budget,
        }
        end_cycle(session, cycle, BUDGET_EXCEEDED, "unmeasured", [reason])
        audit(
            session,
            cycle.company_id,
            "rsi_cycle_budget_exceeded",
            RUNNER,
            reason,
            cycle,
        )
        return cycle
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_tokens",
        RUNNER,
        {
            "tokens": cycle.tokens_reported,
            "sprint": sprint,
            "bench": bench_tokens,
            "budget": budget,
        },
        cycle,
    )
    return cycle


def size_of(diff: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    """Files and changed lines of the daemon's diff, or why they are unknown."""
    paths = diff.get("paths")
    if not isinstance(paths, list) or diff.get("paths_truncated"):
        return None, None, "the daemon did not list every changed path"
    if diff.get("truncated"):
        return len(set(paths)), None, "the daemon cut the diff short"
    lines = sum(
        int(f.get("additions") or 0) + int(f.get("deletions") or 0)
        for f in diff.get("files") or []
    )
    return len(set(paths)), lines, None


def observe_size(session: Session, cycle: RsiCycle, diff: dict[str, Any]) -> RsiCycle:
    """Record C's size from *diff*, the integration's diff as
    ``integration_review.observed`` checks the daemon's reply; over the cap
    or unreadable, the cycle is unmeasured and waits for a person (REQ-25)."""
    boundary(session, cycle)
    files, lines, problem = size_of(diff)
    cycle.diff_files, cycle.diff_lines = files, lines
    cycle.diff_sha256 = diff.get("diff_sha256")
    cycle.updated_at = utcnow()
    session.add(cycle)
    caps = {
        "max_files": settings.RSI_MAX_DIFF_FILES,
        "max_lines": settings.RSI_MAX_DIFF_LINES,
    }
    observed = {"files": files, "lines": lines, **caps}
    if problem is None and files <= caps["max_files"] and lines <= caps["max_lines"]:
        audit(session, cycle.company_id, "rsi_cycle_size", RUNNER, observed, cycle)
        return cycle
    code = "size_unobserved" if problem else "size_cap"
    reason = {"guard": "size", "code": code, **observed, "detail": problem}
    end_cycle(session, cycle, AWAITING_PERSON, "unmeasured", [reason])
    audit(session, cycle.company_id, "rsi_cycle_size_refused", RUNNER, reason, cycle)
    return cycle


async def read_size(nats_client: Any, session: Session, cycle: RsiCycle) -> RsiCycle:
    """Ask the workspace daemon for the integration's diff and record C's size."""
    from models.integration import SprintIntegration

    from services.release import integration_review

    row = session.get(SprintIntegration, cycle.integration_id or 0)
    if row is None or row.company_id != cycle.company_id:
        return observe_size(session, cycle, {})
    diff = await integration_review.read_diff(nats_client, session, row)
    return observe_size(session, cycle, diff)


def record_decision(
    session: Session, cycle: RsiCycle, result: dict[str, Any]
) -> RsiCycle:
    """The fitness decision (``fitness.decide``'s ``{decision, reasons,
    metrics}``). A keep needs C, an observed size within the cap and the
    tokens within the budget; otherwise it is recorded ``unmeasured``."""
    boundary(session, cycle)
    decision = result.get("decision") if isinstance(result, dict) else None
    if decision not in STATUS_BY_DECISION:
        raise refuse(
            session,
            cycle.company_id,
            422,
            "bad_decision",
            "the decision is keep, revert or unmeasured",
            cycle=cycle,
        )
    reasons = list(result.get("reasons") or [])
    if decision == "keep":
        missing = [
            code
            for code, absent in (
                ("no_candidate", not cycle.candidate_sha),
                (
                    "size_unobserved",
                    cycle.diff_files is None or cycle.diff_lines is None,
                ),
                ("budget_unknown", cycle.token_budget is None),
            )
            if absent
        ]
        if missing:
            decision = "unmeasured"
            reasons += [{"guard": "cycle", "code": code} for code in missing]
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else None
    end_cycle(session, cycle, STATUS_BY_DECISION[decision], decision, reasons, metrics)
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_decided",
        RUNNER,
        {
            "decision": decision,
            "reasons": reasons,
            "candidate_sha": cycle.candidate_sha,
        },
        cycle,
    )
    return cycle
