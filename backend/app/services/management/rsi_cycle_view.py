"""What a person and the Manager agent read of the self-improvement cycles
(``docs/design/rsi-loop.md``, PR 10 of section 8): the loop's state for a
company, a cycle as the API shows it, and the Manager inbox's
``merge_self_change`` items, which no policy lets the agent decide
(``ALWAYS_ESCALATE``; decision 1: a person merges).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import settings
from models.rsi_cycle import AWAITING_MERGE, RsiCycle
from sqlmodel import Session

from services.management.rsi_cycle import (
    CATEGORY,
    ITEM_KIND,
    PERSON_MERGES_REQUIRED,
    company_cycles,
    consecutive_without_keep,
    cycles_today,
    kept_merged_by_person,
    stopped,
    token_budget,
    why_not_start,
)
from services.platform import protected_paths_setting


def list_cycles(session: Session, company_id: int) -> list[RsiCycle]:
    return company_cycles(session, company_id)


def state(session: Session, company_id: int) -> dict[str, Any]:
    """The loop's state for the company: its switches, limits and counts."""
    budget, budget_why = token_budget()
    blocked = why_not_start(session, company_id)
    return {
        "company_id": company_id,
        "rsi_enabled": bool(settings.RSI_ENABLED),
        "self_hosting": protected_paths_setting.enabled(session, company_id),
        "stopped": stopped(session, company_id),
        "consecutive_without_keep": consecutive_without_keep(session, company_id),
        "stop_after": settings.RSI_STOP_AFTER_CYCLES_WITHOUT_KEEP,
        "cycles_today": cycles_today(session, company_id),
        "cycles_per_day": settings.RSI_CYCLES_PER_DAY,
        "token_budget": budget,
        "budget_reason": budget_why,
        "max_diff_files": settings.RSI_MAX_DIFF_FILES,
        "max_diff_lines": settings.RSI_MAX_DIFF_LINES,
        "kept_merged_by_person": kept_merged_by_person(session, company_id),
        "person_merges_required": PERSON_MERGES_REQUIRED,
        "may_start": blocked is None,
        "why_not_start": blocked[1] if blocked else None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize(cycle: RsiCycle) -> dict[str, Any]:
    fields = (
        "id",
        "company_id",
        "cycle_key",
        "finding_id",
        "source",
        "status",
        "baseline_sha",
        "candidate_sha",
        "sprint_id",
        "integration_id",
        "pull_request",
        "decision",
        "diff_files",
        "diff_lines",
        "diff_sha256",
        "token_budget",
        "tokens_reported",
        "merged_sha",
        "merged_by",
        "started_by",
        "resumed_by",
    )
    data = {name: getattr(cycle, name) for name in fields}
    data["reasons"] = cycle.reasons or []
    data["metrics"] = cycle.metrics if cycle.metrics is not None else []
    data["bench_tokens"] = cycle.bench_tokens
    data["kept_files"] = cycle.kept_files or []
    for name in ("merged_at", "started_at", "ended_at", "resumed_at", "created_at"):
        data[name] = _iso(getattr(cycle, name))
    return data


def inbox_items(session: Session, company: Any) -> list[dict[str, Any]]:
    """Each kept cycle waiting for a person's merge, for the Manager inbox.

    Its category, ``merge_self_change``, is in ``ALWAYS_ESCALATE``: the agent
    may suggest, never merge."""
    merged = kept_merged_by_person(session, company.id)
    items = []
    for cycle in company_cycles(session, company.id):
        if cycle.status != AWAITING_MERGE:
            continue
        facts = {
            "candidate_sha": cycle.candidate_sha,
            "kept_merged_by_person": merged,
            "person_merges_required": PERSON_MERGES_REQUIRED,
            "autonomy_level": company.autonomy_level,
        }
        text = "\n".join(
            [
                (
                    f"Self-improvement cycle {cycle.cycle_key} was decided keep by the "
                    "fitness function (the platform's observations, not a model)."
                ),
                (
                    f"A person merges its pull request at C {cycle.candidate_sha}; "
                    f"{merged} of the first {PERSON_MERGES_REQUIRED} kept changes have "
                    "been merged by a person."
                ),
                (
                    f"Diff observed: {cycle.diff_files} files, {cycle.diff_lines} lines; "
                    f"tokens {cycle.tokens_reported} of {cycle.token_budget}."
                ),
            ]
        )
        items.append(
            {
                "kind": ITEM_KIND,
                "id": cycle.id,
                "state": f"{AWAITING_MERGE}:{cycle.candidate_sha}",
                "category": CATEGORY,
                "facts": facts,
                "text": text,
            }
        )
    return items
