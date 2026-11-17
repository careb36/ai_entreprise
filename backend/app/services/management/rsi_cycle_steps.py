"""The steps of a running self-improvement cycle (``docs/design/rsi-loop.md``,
PR 10 of section 8; REQ-22, REQ-24, REQ-25, REQ-26).

Each step is a boundary (``rsi_cycle.boundary``), checked before anything
else: the cycle must be running and both kill switches on, or the cycle stops
there and its sprint with it. Every piece of evidence fails closed:

- the sprint is the company's own, or it is refused;
- C's size is read only from the integration of the cycle's sprint at C,
  from the workspace daemon's diff in git, and only when the parsed diff
  agrees with the daemon's full inventory of paths
  (``protected_merge.inventory_problem``); otherwise it is unobserved, and a
  new C or integration forgets an earlier size;
- the usage is read again at the decision: every turn of the sprint reported
  by the provider (``run_cost``) and the bench runner's count recorded, or
  the keep is unmeasured; over the budget, the cycle ends ``budget_exceeded``;
- a decision is about this cycle's B and C (``fitness.decide`` returns both),
  or it is refused, and its metric rows are kept as they came.

Each step is audited ``rsi_cycle_<step>``.
"""

from __future__ import annotations

from typing import Any

from config import settings
from models import Sprint
from models.integration import SprintIntegration
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
from services.release import protected_merge


def record_sprint(session: Session, cycle: RsiCycle, sprint_id: int) -> RsiCycle:
    """The cycle's sprint, which must be one of its company's."""
    boundary(session, cycle)
    sprint = session.get(Sprint, sprint_id) if sprint_id else None
    if sprint is None or sprint.company_id != cycle.company_id:
        raise refuse(
            session,
            cycle.company_id,
            404,
            "bad_sprint",
            "no such sprint in this company",
            cycle=cycle,
        )
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
    """C, the pull request's head sha, and the integration whose diff is read.

    A change of C or of the integration forgets the size measured before.
    """
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
    if (candidate_sha, integration_id) != (cycle.candidate_sha, cycle.integration_id):
        cycle.diff_files = cycle.diff_lines = cycle.diff_sha256 = None
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


def usage(session: Session, cycle: RsiCycle) -> tuple[int | None, str | None]:
    """The cycle's reported tokens now, or ``(None, why)`` when incomplete."""
    if not cycle.sprint_id:
        return None, "sprint_usage_missing"
    costs = run_cost.sprint_costs(session, cycle.company_id, [cycle.sprint_id])
    sprint = costs[cycle.sprint_id]
    if not sprint["reported_turns"]:
        return None, "sprint_usage_missing"
    if sprint["unavailable_turns"] or sprint["estimated_turns"]:
        return None, "sprint_usage_incomplete"
    if cycle.bench_tokens is None:
        return None, "bench_usage_missing"
    return int(sprint["reported_tokens"]) + cycle.bench_tokens, None


def _exceeded(session: Session, cycle: RsiCycle) -> RsiCycle:
    reason = {
        "guard": "budget",
        "code": BUDGET_EXCEEDED,
        "tokens": cycle.tokens_reported,
        "budget": cycle.token_budget,
    }
    end_cycle(session, cycle, BUDGET_EXCEEDED, "unmeasured", [reason])
    audit(session, cycle.company_id, "rsi_cycle_budget_exceeded", RUNNER, reason, cycle)
    return cycle


def account_tokens(
    session: Session, cycle: RsiCycle, bench_tokens: int = 0
) -> RsiCycle:
    """Record the bench runner's reported tokens and read the sprint's turns
    (``run_cost``, the provider's numbers). Over the budget, the cycle ends
    ``budget_exceeded`` and its sprint stops."""
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
    cycle.bench_tokens = bench_tokens
    total, why = usage(session, cycle)
    sprint = 0
    if cycle.sprint_id:
        costs = run_cost.sprint_costs(session, cycle.company_id, [cycle.sprint_id])
        sprint = int(costs[cycle.sprint_id]["reported_tokens"])
    cycle.tokens_reported = sprint + bench_tokens
    cycle.updated_at = utcnow()
    session.add(cycle)
    if cycle.token_budget is None or cycle.tokens_reported > cycle.token_budget:
        return _exceeded(session, cycle)
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_tokens",
        RUNNER,
        {
            "tokens": cycle.tokens_reported,
            "sprint": sprint,
            "bench": bench_tokens,
            "budget": cycle.token_budget,
            "complete": why is None and total is not None,
            "incomplete": why,
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
    problem = protected_merge.inventory_problem(diff)
    if problem:
        return len(set(paths)), None, problem
    lines = sum(
        int(f.get("additions") or 0) + int(f.get("deletions") or 0)
        for f in diff.get("files") or []
    )
    return len(set(paths)), lines, None


def _not_bound(cycle: RsiCycle, row: SprintIntegration | None) -> str | None:
    """Why *row* is not the integration that measures this cycle's C."""
    if row is None or row.company_id != cycle.company_id:
        return "no integration of this company names C"
    if not cycle.candidate_sha or row.commit != cycle.candidate_sha:
        return "the integration's commit is not C"
    if not cycle.sprint_id or row.sprint_id != cycle.sprint_id:
        return "the integration is not of the cycle's sprint"
    if cycle.integration_id and row.id != cycle.integration_id:
        return "the integration is not the one recorded with C"
    return None


def observe_size(
    session: Session,
    cycle: RsiCycle,
    diff: dict[str, Any],
    *,
    integration: SprintIntegration | None,
) -> RsiCycle:
    """Record C's size from *diff*, *integration*'s diff as
    ``integration_review.observed`` checks the daemon's reply; over the cap,
    unreadable or not the cycle's own, the cycle is unmeasured and waits for a
    person (REQ-25)."""
    boundary(session, cycle)
    unbound = _not_bound(cycle, integration)
    files, lines, problem = (None, None, unbound) if unbound else size_of(diff)
    cycle.diff_files, cycle.diff_lines = files, lines
    cycle.diff_sha256 = None if unbound else diff.get("diff_sha256")
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
    """Ask the workspace daemon for the cycle's integration's diff and record
    C's size; the kill switches are checked before the daemon is asked."""
    from services.release import integration_review

    boundary(session, cycle)
    row = session.get(SprintIntegration, cycle.integration_id or 0)
    if _not_bound(cycle, row):
        return observe_size(session, cycle, {}, integration=row)
    diff = await integration_review.read_diff(nats_client, session, row)
    return observe_size(session, cycle, diff, integration=row)


def record_decision(
    session: Session, cycle: RsiCycle, result: dict[str, Any]
) -> RsiCycle:
    """The fitness decision, as ``fitness.decide`` returns it. It must be
    about this cycle's B and C. A keep needs C, an observed size within the
    cap and complete usage within the budget, read now; otherwise it is
    recorded ``unmeasured`` (or ``budget_exceeded``)."""
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
    if (result.get("baseline") or "", result.get("candidate") or "") != (
        cycle.baseline_sha or "",
        cycle.candidate_sha or "",
    ):
        raise refuse(
            session,
            cycle.company_id,
            422,
            "decision_not_this_cycle",
            "the decision was made for another B or C than this cycle's",
            cycle=cycle,
        )
    reasons = list(result.get("reasons") or [])
    if decision == "keep":
        total, why = usage(session, cycle)
        if total is not None:
            cycle.tokens_reported = total
            if cycle.token_budget is None or total > cycle.token_budget:
                return _exceeded(session, cycle)
        missing = [
            code
            for code, absent in (
                ("no_candidate", not cycle.candidate_sha),
                (
                    "size_unobserved",
                    cycle.diff_files is None or cycle.diff_lines is None,
                ),
                ("budget_unknown", cycle.token_budget is None),
                (why or "", why is not None),
            )
            if absent
        ]
        if missing:
            decision = "unmeasured"
            reasons += [{"guard": "cycle", "code": code} for code in missing]
    metrics = result.get("metrics")
    metrics = metrics if isinstance(metrics, (list, dict)) else None
    end_cycle(session, cycle, STATUS_BY_DECISION[decision], decision, reasons, metrics)
    audit(
        session,
        cycle.company_id,
        "rsi_cycle_decided",
        RUNNER,
        {
            "decision": decision,
            "reasons": reasons,
            "baseline_sha": cycle.baseline_sha,
            "candidate_sha": cycle.candidate_sha,
        },
        cycle,
    )
    return cycle
