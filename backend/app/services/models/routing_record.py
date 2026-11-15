"""Done, rework and cost by difficulty band and model (issue #319).

``services/model_routing.py`` routes a task by a deterministic difficulty
estimate; this module answers whether that routing pays: over the company's
completed sprints, per difficulty and model, how many tasks got done, how
many at the first attempt, the rework per task, the blocked share and the
tokens the provider reported.

Every number is an observation the loop recorded, never a model's account:

- the difficulty is the estimate the worker stored on the task before its
  first attempt (the first ``estimated`` event, ``services/models/difficulty.py``),
  with its band (``low``, ``medium``, ``high``) and the routing policy it ran
  under; a task from before the event is recomputed from its plan
  (``model_routing.estimate_difficulty`` of its ``covers``, no rework);
- the model is the one the proxy reported serving the task's last delivery
  (the task worker's latest ``AgentRun``, as ``model_record`` reads it); a
  task with no delivery counts under no model;
- done, blocked, rework and first attempt come from the board
  (``board.rework_request_count``, a person's send-back) and the number of
  deliveries, the same reading as the bench's scorecard;
- tokens are the audit log's turns for the task: only the turns whose usage
  the provider reported (``tokens: reported``) add tokens; estimated or
  unavailable counts are counted apart as turns, never added;
- the cost is ``run_cost``'s for the task's turns, in US cents; a task whose
  cost is unknown (``run_cost`` says why) is counted apart, never as 0.

Beside the rows per difficulty and model, ``split`` sums the same numbers by
difficulty band, by served model and by policy, so a policy is kept or
reverted on numbers, the way procedures are. The pure part (``task_fact``,
``aggregate``, ``split``) is shared with the bench
(``scripts/bench/scorecard.py``); ``record`` reads the database.
"""

from __future__ import annotations

from typing import Any

from services.tasks.board import comment_list, is_send_back, rework_request_count

TOKENS_REPORTED = "reported"


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def difficulty_of(item: Any, stored: dict[str, Any] | None = None) -> int:
    """The task's difficulty as planned: the stored estimate of its first
    attempt (an ``estimated`` event's ``difficulty``), else recomputed."""
    from services.models import difficulty, model_routing

    score = difficulty.stored_score(stored)
    return score if score is not None else model_routing.estimate_difficulty(item)


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
    cents: float | None = None,
    policy: str | None = None,
) -> dict[str, Any]:
    """One task's observed facts. Done at the first attempt: done, no rework
    request, no person's send-back and at most one delivery. *cents*: its
    cost, ``None`` when unknown; *policy*: the label of the routing policy its
    first attempt ran under, ``None`` without one."""
    from services.models.difficulty import band_of_score

    rework = rework_request_count(comments)
    sent_back = any(is_send_back(c) for c in comment_list(comments))
    return {
        "difficulty": int(difficulty),
        "difficulty_band": band_of_score(int(difficulty)),
        "policy": (policy or "").strip() or None,
        "cents": cents,
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


def _numbers(group: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = len(group)
    done = sum(f["done"] for f in group)
    blocked = sum(f["blocked"] for f in group)
    first = sum(f["first_attempt"] for f in group)
    rework = sum(f["rework"] for f in group)
    priced = [f["cents"] for f in group if f.get("cents") is not None]
    return {
        "tasks": tasks,
        "done": done,
        "done_first_attempt": first,
        "blocked": blocked,
        "rework": rework,
        "rework_per_task": _ratio(rework, tasks),
        "blocked_share": _ratio(blocked, tasks),
        "first_attempt_rate": _ratio(first, tasks),
        "tokens": {
            k: sum(f["tokens"].get(k, 0) for f in group)
            for k in ("reported", "turns_reported", "turns_unreported")
        },
        "cost_cents": round(sum(priced), 6) if priced else None,
        "cost_unknown": tasks - len(priced),
    }


def aggregate(
    facts: list[dict[str, Any]], policy: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Per (difficulty, model): tasks, done, first attempt, rework, blocked,
    reported tokens and cost, lowest difficulty first. ``band`` is the
    policy's band key holding the difficulty, or None without a policy;
    ``difficulty_band`` is the estimate's own (``low``, ``medium``, ``high``)."""
    from services.models import model_routing
    from services.models.difficulty import band_of_score

    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for fact in facts:
        groups.setdefault((fact["difficulty"], fact["model"] or ""), []).append(fact)
    return [
        {
            "difficulty": difficulty,
            "difficulty_band": band_of_score(difficulty),
            "band": model_routing.band_of(difficulty, policy or {}),
            "model": model or None,
            **_numbers(group),
        }
        for (difficulty, model), group in sorted(groups.items())
    ]


SPLITS = ("difficulty_band", "model", "policy")


def split(facts: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """The same numbers summed by one of ``SPLITS``: per difficulty band (low
    first), per served model or per policy label (sorted; ``None`` last)."""
    from services.models.difficulty import BANDS

    groups: dict[Any, list[dict[str, Any]]] = {}
    for fact in facts:
        groups.setdefault(fact.get(key), []).append(fact)

    def order(value: Any) -> tuple[int, Any]:
        if key == "difficulty_band" and value in BANDS:
            return (BANDS.index(value), "")
        return (1 if value is None else 0, str(value or ""))

    return [
        {key: value, **_numbers(groups[value])} for value in sorted(groups, key=order)
    ]


def _task_id(external_ref: str) -> int | None:
    prefix, _, number = (external_ref or "").partition("-")
    return int(number) if prefix == "task" and number.isdigit() else None


def record(session: Any, company_id: int) -> dict[str, Any]:
    """The company's routing record over its completed sprints."""
    from models import AuditLog, BacklogItem, Sprint
    from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
    from sqlmodel import select

    from services.models import routing_policy

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
    estimates = first_estimates(session, company_id, ids)
    costs = task_cents(session, company_id, ids)
    facts = [
        task_fact(
            difficulty=difficulty_of(
                item, estimates.get(item.id, {}).get("difficulty")
            ),
            model=last_model.get(item.id),
            status=item.status,
            comments=item.comments,
            runs=runs.get(item.id, 0),
            tokens=turn_tokens(turns.get(item.id, [])),
            cents=costs.get(item.id),
            policy=(estimates.get(item.id, {}).get("routing") or {}).get("policy"),
        )
        for item in items
    ]
    policy, source = routing_policy.effective(session, company_id)
    return {
        "rows": aggregate(facts, policy),
        **{f"by_{key}": split(facts, key) for key in SPLITS},
        "tasks": len(facts),
        "policy": policy.get("bands")
        if isinstance(policy.get("bands"), dict)
        else None,
        "policy_label": policy.get("label") if policy else None,
        "policy_source": source,
    }


def first_estimates(
    session: Any, company_id: int, ids: set[int]
) -> dict[int, dict[str, Any]]:
    """Per task, the payload of its first ``estimated`` event: the difficulty
    and routing of its first attempt."""
    from models import TaskEvent
    from sqlmodel import select

    out: dict[int, dict[str, Any]] = {}
    if not ids:
        return out
    for event in session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.task_id.in_(list(ids)),
            TaskEvent.event_type == "estimated",
        )
        .order_by(TaskEvent.seq, TaskEvent.id)
    ).all():
        payload = event.payload if isinstance(event.payload, dict) else {}
        out.setdefault(event.task_id, payload)
    return out


def task_cents(session: Any, company_id: int, ids: set[int]) -> dict[int, float | None]:
    """Per task, its cost in US cents (``run_cost``), ``None`` when unknown."""
    from services.models import run_cost

    turns: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    for row, turn in run_cost._priced(
        session, company_id, [run_cost.task_ref(i) for i in ids]
    ):
        task_id = _task_id(row.interaction_id or "")
        if task_id in turns:
            turns[task_id].append(turn)
    return {i: run_cost.summarize(t)["cost_cents"] for i, t in turns.items()}
