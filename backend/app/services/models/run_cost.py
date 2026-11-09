"""What a run, a task and a sprint cost: tokens and US cents.

The idea is Aixle Flow's cost per session (Apache 2.0:
``app/services/usage_statistics/accumulator.rb``,
``app/services/session_cost_token_usage_service.rb``,
``app/frontend/shared/lib/sessionFormat.ts``): one usage record per call is
the only source, the cost is summed from it rather than copied onto the
session, fractional cents are kept unrounded until shown, and the models are
a sorted set beside the numbers. Here the usage record is the audit log's KPI
turn row (``orchestration_metrics.record_agent_turn``: ``cost_tokens``,
``details.tokens``, and the split ``token_usage`` adds); there is no second
ledger.

Where it differs from Flow: Flow shows a zero cost and an unknown one the
same way. Here only turns whose usage the provider ``reported`` are priced;
an ``estimated`` count is reported apart as ``estimated_tokens`` and never
enters the cents; a turn whose model has no price in the catalogue
(``model_prices``), or whose input/output split was not recorded, makes the
cost ``None`` with the reason, never 0.

Attribution to a run: a task's turns (``interaction_id`` ``task-<id>``) go to
the first version of its task-worker run ingested at or after the turn, so a
version carries the turns that led to its delivery; turns after the latest
version (its QA review) go to the latest. A task's cost is all its turns, a
sprint's all its tasks'.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from models import AuditLog, BacklogItem
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
from models.provider import ProviderConnection
from sqlmodel import Session, select

from services.models import model_prices

TURN_ACTIONS = ("execute_task", "qa_review", "architect_consult")
NO_TURNS = "no_turns"
NO_REPORTED_USAGE = "no_reported_usage"
SPLIT_UNKNOWN = "token_split_unknown"
CONNECTION_GONE = "connection-not-found"


def _naive_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.min
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def task_ref(task_id: int) -> str:
    return f"task-{task_id}"


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def turn_cost(
    row: AuditLog, connections: dict[str, str], catalog: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One turn: how its usage is known, its tokens and its cents (or why not)."""
    details = row.details if isinstance(row.details, dict) else {}
    status = details.get("tokens")
    if status is None:  # Rows from before the key: a total above 0 was reported.
        status = "reported" if (row.cost_tokens or 0) > 0 else "unavailable"
    model = details.get("model")
    turn: dict[str, Any] = {"status": status, "model": model}
    if status == "estimated":
        turn["estimated_tokens"] = _int(details.get("estimated_tokens")) or 0
        return turn
    if status != "reported":
        return turn
    turn["tokens"] = int(row.cost_tokens or 0)
    tokens_in, tokens_out = (
        _int(details.get("input_tokens")),
        _int(details.get("output_tokens")),
    )
    if tokens_in is None or tokens_out is None:
        turn["reason"] = SPLIT_UNKNOWN
        return turn
    turn["input_tokens"], turn["output_tokens"] = tokens_in, tokens_out
    provider = str(details.get("provider") or "")
    connection = None
    if provider.startswith("connection:"):
        connection = connections.get(provider.split(":", 1)[1], CONNECTION_GONE)
    price, reason = model_prices.price_for(model, provider, connection, catalog)
    if price is None:
        turn["reason"] = reason
    else:
        turn["cents"] = model_prices.cents(tokens_in, tokens_out, price)
    return turn


def summarize(turns: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The cost of a set of turns, in the API's shape."""
    out: dict[str, Any] = {
        "turns": 0,
        "reported_turns": 0,
        "estimated_turns": 0,
        "unavailable_turns": 0,
        "reported_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_tokens": 0,
        "cost_cents": None,
        "reason": None,
        "unpriced_models": [],
        "models": [],
        "price_source": "models.dev",
    }
    cents, reasons, models, unpriced = 0.0, [], set(), set()
    for turn in turns:
        out["turns"] += 1
        if turn.get("model"):
            models.add(str(turn["model"]))
        if turn["status"] == "estimated":
            out["estimated_turns"] += 1
            out["estimated_tokens"] += turn.get("estimated_tokens", 0)
            continue
        if turn["status"] != "reported":
            out["unavailable_turns"] += 1
            continue
        out["reported_turns"] += 1
        out["reported_tokens"] += turn.get("tokens", 0)
        out["input_tokens"] += turn.get("input_tokens", 0)
        out["output_tokens"] += turn.get("output_tokens", 0)
        if "cents" in turn:
            cents += turn["cents"]
            continue
        reasons.append(turn["reason"])
        if turn["reason"] != SPLIT_UNKNOWN:
            unpriced.add(str(turn.get("model") or "?"))
    out["models"], out["unpriced_models"] = sorted(models), sorted(unpriced)
    if not out["turns"]:
        out["reason"] = NO_TURNS
    elif not out["reported_turns"]:
        out["reason"] = NO_REPORTED_USAGE
    elif reasons:
        out["reason"] = reasons[0]
    else:
        out["cost_cents"] = round(cents, 6)
    return out


def _connections(session: Session, company_id: int) -> dict[str, str]:
    rows = session.exec(
        select(ProviderConnection).where(ProviderConnection.company_id == company_id)
    ).all()
    return {row.name: row.provider for row in rows}


def _turn_rows(session: Session, company_id: int, refs: list[str]) -> list[AuditLog]:
    if not refs:
        return []
    return list(
        session.exec(
            select(AuditLog)
            .where(
                AuditLog.company_id == company_id,
                AuditLog.interaction_id.in_(refs),
                AuditLog.action.in_(TURN_ACTIONS),
            )
            .order_by(AuditLog.id)
        ).all()
    )


def _priced(session: Session, company_id: int, refs: list[str]):
    connections = _connections(session, company_id)
    return [
        (row, turn_cost(row, connections))
        for row in _turn_rows(session, company_id, refs)
    ]


def task_cost(session: Session, company_id: int, task_id: int) -> dict[str, Any]:
    """A task's cost, and each of its task-worker run versions' cost."""
    ref = task_ref(task_id)
    turns = _priced(session, company_id, [ref])
    runs = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == ref,
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version, AgentRun.id)
    ).all()
    per_run: dict[int, list[dict[str, Any]]] = {run.id: [] for run in runs}
    for row, turn in turns:
        moment = _naive_utc(row.created_at)
        owner = next(
            (r for r in runs if _naive_utc(r.ingested_at) >= moment),
            runs[-1] if runs else None,
        )
        if owner is not None:
            per_run[owner.id].append(turn)
    return {
        "task_id": task_id,
        **summarize(turn for _, turn in turns),
        "runs": [
            {"run_id": r.id, "version": r.version, **summarize(per_run[r.id])}
            for r in runs
        ],
    }


def run_cost(session: Session, run: AgentRun) -> dict[str, Any]:
    """One run version's cost; a run not made by the task worker has no turns here."""
    ref = run.external_ref or ""
    task_id = ref[len("task-") :] if ref.startswith("task-") else ""
    if run.producer != TASK_WORKER_PRODUCER or not task_id.isdigit():
        return {"run_id": run.id, "version": run.version, **summarize([])}
    for entry in task_cost(session, run.company_id, int(task_id))["runs"]:
        if entry["run_id"] == run.id:
            return entry
    return {"run_id": run.id, "version": run.version, **summarize([])}


def sprint_costs(
    session: Session, company_id: int, sprint_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Each sprint's cost, summed over its tasks' turns."""
    if not sprint_ids:
        return {}
    items = session.exec(
        select(BacklogItem.id, BacklogItem.sprint_id).where(
            BacklogItem.sprint_id.in_(sprint_ids)
        )
    ).all()
    sprint_of = {task_ref(item_id): sid for item_id, sid in items}
    grouped: dict[int, list[dict[str, Any]]] = {sid: [] for sid in sprint_ids}
    for row, turn in _priced(session, company_id, list(sprint_of)):
        grouped[sprint_of[row.interaction_id]].append(turn)
    return {sid: summarize(turns) for sid, turns in grouped.items()}


def sprint_task_costs(
    session: Session, company_id: int, sprint_id: int
) -> list[dict[str, Any]]:
    """Each task of a sprint with its cost (no per-run breakdown)."""
    items = session.exec(
        select(BacklogItem.id, BacklogItem.title)
        .where(BacklogItem.sprint_id == sprint_id)
        .order_by(BacklogItem.id)
    ).all()
    refs = {task_ref(item_id): item_id for item_id, _ in items}
    grouped: dict[int, list[dict[str, Any]]] = {
        item_id: [] for item_id in refs.values()
    }
    for row, turn in _priced(session, company_id, list(refs)):
        grouped[refs[row.interaction_id]].append(turn)
    return [
        {"task_id": item_id, "title": title, **summarize(grouped[item_id])}
        for item_id, title in items
    ]
