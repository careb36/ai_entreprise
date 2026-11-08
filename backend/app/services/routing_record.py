"""Done, rework and cost by difficulty band and model (issue #319).

``services/model_routing.py`` routes a task by a deterministic difficulty
estimate; this module answers whether that routing pays: over the company's
completed sprints, per difficulty and model, how many tasks got done, how
many at the first attempt, the rework per task, the blocked share and the
tokens the provider reported.

Every number is an observation the loop recorded, never a model's account:

- the difficulty is ``model_routing.estimate_difficulty`` of the task as it
  was planned (its ``covers``, no rework): what was known before it ran and
  what the worker routed its first attempt by. It is recomputed from the
  stored plan, so nothing new is stored and no migration is needed;
- the model is the one the proxy reported serving the task's last delivery
  (the task worker's latest ``AgentRun``, as ``model_record`` reads it); a
  task with no delivery counts under no model;
- done, blocked, rework and first attempt come from the board
  (``board.rework_request_count``, a person's send-back) and the number of
  deliveries, the same reading as the bench's scorecard;
- tokens are the audit log's turns for the task: only the turns whose usage
  the provider reported (``tokens: reported``) add tokens; estimated or
  unavailable counts are counted apart as turns, never added.

The pure part (``task_fact``, ``aggregate``) is shared with the bench
(``scripts/bench/scorecard.py``); ``record`` reads the database.
"""

from __future__ import annotations

from typing import Any

from services.board import comment_list, is_send_back, rework_request_count

TOKENS_REPORTED = "reported"


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def difficulty_of(item: Any) -> int:
    """The task's difficulty as planned: its first attempt's routing input."""
    from services import model_routing

    return model_routing.estimate_difficulty(item, rework=False)


def turn_tokens(turns: list[tuple[str | None, int | None]]) -> dict[str, int]:
    """``(usage status, cost)`` per audit turn → reported tokens and turn counts."""
    out = {"reported": 0, "turns_reported": 0, "turns_unreported": 0}
    for status, cost in turns:
        if status == TOKENS_REPORTED:
            out["reported"] += max(0, int(cost or 0))
            out["turns_reported"] += 1
        else:
            out["turns_unreported"] += 1
    return out


def task_fact(
    *,
    difficulty: int,
    model: str | None,
    status: str | None,
    comments: Any,
    runs: int,
    tokens: dict[str, int] | None = None,
) -> dict[str, Any]:
    """One task's observed facts. Done at the first attempt: done, no rework
    request, no person's send-back and at most one delivery."""
    rework = rework_request_count(comments)
    sent_back = any(is_send_back(c) for c in comment_list(comments))
    return {
        "difficulty": int(difficulty),
        "model": (model or "").strip() or None,
        "done": status == "done",
        "blocked": status == "blocked",
        "first_attempt": status == "done"
        and not rework
        and not sent_back
        and runs <= 1,
        "rework": rework,
        "tokens": dict(tokens or turn_tokens([])),
    }


def aggregate(
    facts: list[dict[str, Any]], policy: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Per (difficulty, model): tasks, done, first attempt, rework, blocked and
    reported tokens, lowest difficulty first. ``band`` is the policy's band key
    holding the difficulty, or None without a policy."""
    from services import model_routing

    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for fact in facts:
        groups.setdefault((fact["difficulty"], fact["model"] or ""), []).append(fact)
    rows = []
    for (difficulty, model), group in sorted(groups.items()):
        tasks = len(group)
        done = sum(f["done"] for f in group)
        blocked = sum(f["blocked"] for f in group)
        first = sum(f["first_attempt"] for f in group)
        rework = sum(f["rework"] for f in group)
        tokens = {
            k: sum(f["tokens"].get(k, 0) for f in group)
            for k in ("reported", "turns_reported", "turns_unreported")
        }
        rows.append(
            {
                "difficulty": difficulty,
                "band": model_routing.band_of(difficulty, policy or {}),
                "model": model or None,
                "tasks": tasks,
                "done": done,
                "done_first_attempt": first,
                "blocked": blocked,
                "rework": rework,
                "rework_per_task": _ratio(rework, tasks),
                "blocked_share": _ratio(blocked, tasks),
                "first_attempt_rate": _ratio(first, tasks),
                "tokens": tokens,
            }
        )
    return rows


def _task_id(external_ref: str) -> int | None:
    prefix, _, number = (external_ref or "").partition("-")
    return int(number) if prefix == "task" and number.isdigit() else None


def record(session: Any, company_id: int) -> dict[str, Any]:
    """The company's routing record over its completed sprints."""
    from models import AuditLog, BacklogItem, Sprint
    from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
    from sqlmodel import select

    from services import model_routing

    items = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.company_id == company_id, Sprint.status == "completed")
    ).all()
    ids = {item.id for item in items}
    runs: dict[int, int] = {}
    last_model: dict[int, str | None] = {}
    for run in session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.id)
    ).all():
        task_id = _task_id(run.external_ref)
        if task_id in ids:
            runs[task_id] = runs.get(task_id, 0) + 1
            last_model[task_id] = run.model
    turns: dict[int, list[tuple[str | None, int | None]]] = {}
    if ids:
        for row in session.exec(
            select(AuditLog).where(
                AuditLog.company_id == company_id,
                AuditLog.interaction_id.in_([f"task-{i}" for i in ids]),
            )
        ).all():
            details = row.details if isinstance(row.details, dict) else {}
            task_id = _task_id(row.interaction_id or "")
            if task_id is not None and details.get("tokens") is not None:
                turns.setdefault(task_id, []).append(
                    (details.get("tokens"), row.cost_tokens)
                )
    facts = [
        task_fact(
            difficulty=difficulty_of(item),
            model=last_model.get(item.id),
            status=item.status,
            comments=item.comments,
            runs=runs.get(item.id, 0),
            tokens=turn_tokens(turns.get(item.id, [])),
        )
        for item in items
    ]
    policy = model_routing.current_policy()
    return {
        "rows": aggregate(facts, policy),
        "tasks": len(facts),
        "policy": policy.get("bands")
        if isinstance(policy.get("bands"), dict)
        else None,
    }
