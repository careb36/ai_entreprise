"""Work performance figures per sprint, from observations only (issue #530, phase 2).

The PMBOK Guide's chain of work performance data, information and report maps
onto La Fabrique's observation, analysis and claim: this module is the
analysis, the report (``status_docs``) and the check results (``checks``)
render it. Every figure is counted from a record the factory observed itself:

- the sprint's tasks and their board status (``sprints``);
- the carries, holds and escalations, as task events with their chain hash
  (``events``);
- the Verifier's readings of each delivery (``verifications``);
- the effort the backend observed at sprint close (``effort``: the latest
  ``effort_observed`` event per task, ``services/models/effort.py``):
  deliveries, rework cycles, rework requests, a person's send-backs, review
  failures;
- the model usage the LLM proxy reported, priced from the catalogue
  (``costs``, ``services/models/run_cost.py``): a spend that is not known
  stays ``None`` with its reason, never 0.

Nothing here reads a retrospective, an agent message or a delivery's text:
those are claims (``docs/POSITIONING.md``).

Pure; no I/O.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from services.contracts.verdicts import (
    VERDICT_REFUTED,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
)

VERDICTS = (VERDICT_VERIFIED, VERDICT_REFUTED, VERDICT_UNVERIFIABLE)
COMPLETED = "completed"
HOLD_KINDS = ("blocked", "escalated", "risk_hold")
PERFORMANCE_INPUTS = ("sprints", "events", "verifications", "effort", "costs")


def usd(cents: float) -> str:
    """US cents as dollars with two decimals, rounded half up."""
    value = Decimal(str(cents)) / Decimal(100)
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def effort_of(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """The backend's effort observation per task id (the snapshot keeps the latest)."""
    return {
        e.get("task_id"): e
        for e in snapshot.get("effort") or []
        if isinstance(e, dict) and isinstance(e.get("observed"), dict)
    }


def cost_of(snapshot: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """The reported model usage per sprint id."""
    return {
        c.get("sprint_id"): c
        for c in snapshot.get("costs") or []
        if isinstance(c, dict)
    }


def _carried(event: dict[str, Any], direction: str) -> bool:
    return event.get("kind") == "carried_over" and event.get("direction") == direction


def sprint_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """One row of figures per sprint, in sprint order, with the records behind each."""
    efforts, costs = effort_of(snapshot), cost_of(snapshot)
    events = snapshot.get("events") or []
    verifications = snapshot.get("verifications") or []
    rows = []
    for sprint in snapshot.get("sprints") or []:
        tasks = sprint.get("tasks") or []
        ids = {t.get("id") for t in tasks}
        sid = sprint.get("id")
        mine = [e for e in events if e.get("sprint_id") == sid]
        readings = [v for v in verifications if v.get("task_id") in ids]
        observed = [efforts[t.get("id")] for t in tasks if t.get("id") in efforts]
        done_ids = {t.get("id") for t in tasks if t.get("status") == "done"}
        done_observed = [e for e in observed if e.get("task_id") in done_ids]

        def total(name: str, entries: list[dict[str, Any]] = observed) -> int:
            return sum(_count(e["observed"].get(name)) for e in entries)

        rows.append(
            {
                "sprint": sprint,
                "planned": len(tasks),
                "done": len(done_ids),
                "blocked": sum(1 for t in tasks if t.get("status") == "blocked"),
                "carried_out": sum(1 for e in mine if _carried(e, "to")),
                "carried_in": sum(1 for e in mine if _carried(e, "from")),
                "carry_events": [e for e in mine if e.get("kind") == "carried_over"],
                "readings": {
                    v: sum(1 for r in readings if r.get("verdict") == v)
                    for v in VERDICTS
                },
                "high_risk": sum(1 for r in readings if r.get("tier") == "high"),
                "verifications": readings,
                "observed": len(observed),
                "done_observed": len(done_observed),
                "first_pass": sum(
                    1
                    for e in done_observed
                    if _count(e["observed"].get("rework_cycles")) == 0
                    and _count(e["observed"].get("rework_requests")) == 0
                    and _count(e["observed"].get("send_backs")) == 0
                ),
                "deliveries": total("deliveries"),
                "rework_cycles": total("rework_cycles"),
                "rework_requests": total("rework_requests"),
                "send_backs": total("send_backs"),
                "review_failures": total("review_failures"),
                "efforts": observed,
                "hold_events": [e for e in mine if e.get("kind") in HOLD_KINDS],
                "blocks": sum(1 for e in mine if e.get("kind") == "blocked"),
                "escalations": sum(1 for e in mine if e.get("kind") == "escalated"),
                "risk_holds": sum(1 for e in mine if e.get("kind") == "risk_hold"),
                "cost": costs.get(sid),
            }
        )
    return rows


def known_cost(cost: dict[str, Any] | None) -> bool:
    return bool(cost) and isinstance(cost.get("cost_cents"), int | float)


SUMMED = (
    "planned",
    "done",
    "blocked",
    "carried_out",
    "observed",
    "done_observed",
    "first_pass",
    "deliveries",
    "rework_cycles",
    "rework_requests",
    "send_backs",
    "review_failures",
    "blocks",
    "escalations",
    "risk_holds",
    "high_risk",
)


def totals(
    snapshot: dict[str, Any], rows: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The project's figures over every sprint. The spend is known only when
    every sprint with model usage, and at least one, has it priced."""
    rows = sprint_rows(snapshot) if rows is None else rows
    out: dict[str, Any] = {name: sum(r[name] for r in rows) for name in SUMMED}
    out["sprints"] = len(rows)
    out["completed"] = sum(1 for r in rows if r["sprint"].get("status") == COMPLETED)
    out["readings"] = {v: sum(r["readings"][v] for r in rows) for v in VERDICTS}
    costs = [r["cost"] for r in rows if r["cost"] and r["cost"].get("turns")]
    out["cost_known"] = bool(costs) and all(known_cost(c) for c in costs)
    out["cost_cents"] = (
        round(sum(c["cost_cents"] for c in costs), 6) if out["cost_known"] else None
    )
    out["reported_tokens"] = sum(_count(c.get("reported_tokens")) for c in costs)
    return out
