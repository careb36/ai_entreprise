"""The weight of each prompt section, read back from the runs (#443, "Show").

The task worker measures every prompt it sends (``loop/worker/prompt_budget``)
and keeps the measure on the run's evidence (its latest ``Verification``)
as ``prompt_sections``: each
section's characters and estimated tokens, its priority, and whether the
budget left it out. That measure is the backend's own observation; this module
only adds it up for a run, a task or a sprint, so the cost views can say where
the tokens go. Nothing here is measured again or taken from a model's report.

Tokens are the measure's estimate (characters / 4), never the provider's
count, which the cost (``run_cost``) reports apart.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from models import BacklogItem
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select


def _measure(evidence: Any) -> dict[str, Any] | None:
    measure = evidence.get("prompt_sections") if isinstance(evidence, dict) else None
    if not isinstance(measure, dict) or not isinstance(measure.get("sections"), list):
        return None
    return measure


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def summarize(measures: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """The measures added up: per section its characters, tokens, share of the
    sections' tokens, how many prompts carried it and how many times the
    budget left it out, heaviest first."""
    calls = 0
    tokens = 0
    system_tokens = 0
    by_name: dict[str, dict[str, Any]] = {}
    for measure in measures:
        if measure is None:
            continue
        calls += 1
        tokens += _int(measure.get("tokens"))
        system_tokens += _int(measure.get("system_tokens"))
        for section in measure["sections"]:
            if not isinstance(section, dict) or not section.get("name"):
                continue
            entry = by_name.setdefault(
                str(section["name"]),
                {
                    "name": str(section["name"]),
                    "chars": 0,
                    "tokens": 0,
                    "calls": 0,
                    "cut": 0,
                },
            )
            if section.get("cut"):
                entry["cut"] += 1
                continue
            entry["calls"] += 1
            entry["chars"] += _int(section.get("chars"))
            entry["tokens"] += _int(section.get("tokens"))
    sent = sum(entry["tokens"] for entry in by_name.values())
    sections = sorted(by_name.values(), key=lambda e: (-e["tokens"], e["name"]))
    for entry in sections:
        entry["share"] = round(entry["tokens"] / sent, 4) if sent else 0.0
    return {
        "calls": calls,
        "tokens": tokens,
        "system_tokens": system_tokens,
        "sections": sections,
        "estimate": "chars/4",
    }


def _evidence(session: Session, runs: list[AgentRun]) -> list[Any]:
    """Each run's evidence, from its latest verification; a run never verified has none."""
    ids = [run.id for run in runs if run.id is not None]
    if not ids:
        return []
    latest: dict[int, Verification] = {}
    for row in session.exec(
        select(Verification).where(Verification.agent_run_id.in_(ids))  # type: ignore[union-attr]
    ).all():
        kept = latest.get(row.agent_run_id)
        if kept is None or (row.id or 0) > (kept.id or 0):
            latest[row.agent_run_id] = row
    return [latest[i].evidence if i in latest else None for i in ids]


def run_weight(session: Session, run: AgentRun) -> dict[str, Any]:
    """One run version's prompt, as measured when it was sent."""
    return summarize(_measure(e) for e in _evidence(session, [run]))


def _task_runs(session: Session, company_id: int, refs: list[str]) -> list[AgentRun]:
    if not refs:
        return []
    return list(
        session.exec(
            select(AgentRun).where(
                AgentRun.company_id == company_id,
                AgentRun.producer == TASK_WORKER_PRODUCER,
                AgentRun.external_ref.in_(refs),  # type: ignore[union-attr]
            )
        ).all()
    )


def task_weight(session: Session, company_id: int, task_id: int) -> dict[str, Any]:
    """Every prompt the task worker sent for the task, one per run version."""
    runs = _task_runs(session, company_id, [f"task-{task_id}"])
    return summarize(_measure(e) for e in _evidence(session, runs))


def sprint_weight(session: Session, company_id: int, sprint_id: int) -> dict[str, Any]:
    """Every prompt the task worker sent for the sprint's tasks."""
    ids = session.exec(
        select(BacklogItem.id).where(BacklogItem.sprint_id == sprint_id)
    ).all()
    runs = _task_runs(session, company_id, [f"task-{item_id}" for item_id in ids])
    return summarize(_measure(e) for e in _evidence(session, runs))
