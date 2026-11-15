"""Predicted difficulty scored against observed effort (issue #534).

The prediction is the difficulty estimate stored on a task before its first
attempt (its first ``estimated`` event, ``services/models/difficulty.py``); a
task from before the event is recomputed from its plan, as
``routing_record.difficulty_of`` does, and counted as ``recomputed``. A later
``estimated`` event (a rework attempt, or a person's change) is a new event
and never replaces the prediction scored here.

The observation is the task's observed effort (``services/models/effort.py``):
its latest ``effort_observed`` event, written at sprint close from its own
records, else the same reading done now for a task closed before the event
existed (``observed_source``). Nothing comes from an agent's claim.

The report is deterministic: the same records give the same numbers. Per
group (all tasks, each predicted band, role, producer, served model, sprint):

- ``over_one_rework_share``: the share of tasks with more than one rework
  cycle (more than two deliveries); for the ``low`` band it is the share of
  easy tasks that needed repeated rework;
- ``tokens_spread``: min, first quartile, median, third quartile and max of
  the tasks whose tokens are known (linear interpolation between ranks);
  ``tokens_unknown`` counts the others apart, never as 0;
- ``spearman``: the rank correlation between predicted score and observed
  tokens over the group's tasks with known tokens (average ranks for ties);
  ``None`` with fewer than ``MIN_CORRELATION_TASKS`` such tasks or when
  either side does not vary;
- ``off_by_two_bands_share``: the share of tasks whose predicted band and
  observed band are two bands apart (``low`` predicted, ``high`` observed, or
  the reverse). The observed band ranks the known tokens of every task in
  the report: the lowest third ``low``, the middle ``medium``, the top
  ``high`` (by average rank, so equal tokens share a band). Tasks whose
  tokens are unknown have no observed band and are counted apart.

``signals`` names what the numbers suggest about #319's thresholds; they are
information only, shown beside the routing policy, and never change it.
Plane's estimates (``db/models/estimate.py``) and the points variant of its
burndown (``utils/analytics_plot.py``) were the design reference (AGPL-3.0,
design only): Plane records the estimate and the progress against it, but
has no observed side; the observation and the scoring are this project's.
"""

from __future__ import annotations

from typing import Any

from services.models import difficulty, effort

MIN_CORRELATION_TASKS = 3
# The signals' floors: fewer tasks than this in a band says nothing.
MIN_SIGNAL_TASKS = 5
LOW_REWORK_SIGNAL = 0.25
SPLITS = ("predicted_band", "role", "producer", "model", "sprint_id")


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def ranks(values: list[float]) -> list[float]:
    """1-based ranks of *values*, ties sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        rank = (start + end) / 2 + 1
        for index in order[start : end + 1]:
            out[index] = rank
        start = end + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rank correlation of the pairs, or None when it says nothing."""
    if len(xs) != len(ys) or len(xs) < MIN_CORRELATION_TASKS:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if not sxx or not syy:
        return None
    return round(sxy / (sxx * syy) ** 0.5, 4)


def quantile(sorted_values: list[float], q: float) -> float:
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    value = sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (
        position - low
    )
    return round(value, 1)


def spread(values: list[float]) -> dict[str, float] | None:
    """Min, quartiles and max of *values*; None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "q1": quantile(ordered, 0.25),
        "median": quantile(ordered, 0.5),
        "q3": quantile(ordered, 0.75),
        "max": ordered[-1],
    }


def observed_bands(facts: list[dict[str, Any]]) -> list[str | None]:
    """Each fact's observed band, by the rank of its known tokens among all."""
    known = [i for i, f in enumerate(facts) if f["observed"].get("tokens") is not None]
    out: list[str | None] = [None] * len(facts)
    if len(known) < MIN_CORRELATION_TASKS:
        return out
    tokens_ranks = ranks([float(facts[i]["observed"]["tokens"]) for i in known])
    n = len(known)
    for index, rank in zip(known, tokens_ranks, strict=True):
        share = (rank - 0.5) / n
        out[index] = difficulty.BANDS[min(2, int(share * 3))]
    return out


def task_fact(
    *,
    task_id: int,
    sprint_id: int | None,
    score: int,
    predicted_source: str,
    observation: dict[str, Any],
    observed_source: str,
) -> dict[str, Any]:
    """One task's prediction beside its observation (``effort.collect``'s or
    a stored ``effort_observed`` payload)."""
    attribution = observation.get("attribution") or {}
    return {
        "task_id": task_id,
        "sprint_id": sprint_id,
        "predicted_score": int(score),
        "predicted_band": difficulty.band_of_score(int(score)),
        "predicted_source": predicted_source,
        "observed_source": observed_source,
        "role": attribution.get("role"),
        "producer": attribution.get("producer"),
        "model": attribution.get("model"),
        "observed": dict(observation.get("observed") or {}),
    }


def numbers(group: list[dict[str, Any]]) -> dict[str, Any]:
    """The calibration numbers of a group of facts (each with ``observed_band``)."""
    tasks = len(group)
    known = [f for f in group if f["observed"].get("tokens") is not None]
    banded = [f for f in group if f.get("observed_band")]
    off = [
        f
        for f in banded
        if abs(
            difficulty.BANDS.index(f["predicted_band"])
            - difficulty.BANDS.index(f["observed_band"])
        )
        >= 2
    ]
    reworked = [f for f in group if int(f["observed"].get("rework_cycles") or 0) > 1]
    walls = [
        f["observed"]["wall_seconds"]
        for f in group
        if f["observed"].get("wall_seconds") is not None
    ]
    files = [
        f["observed"]["files_written"]
        for f in group
        if f["observed"].get("files_written") is not None
    ]
    return {
        "tasks": tasks,
        "done": sum(1 for f in group if f["observed"].get("done")),
        "mean_predicted": _ratio(sum(f["predicted_score"] for f in group), tasks),
        "rework_cycles_per_task": _ratio(
            sum(int(f["observed"].get("rework_cycles") or 0) for f in group), tasks
        ),
        "over_one_rework": len(reworked),
        "over_one_rework_share": _ratio(len(reworked), tasks),
        "review_failures": sum(
            int(f["observed"].get("review_failures") or 0) for f in group
        ),
        "tokens_known": len(known),
        "tokens_unknown": tasks - len(known),
        "tokens_spread": spread([float(f["observed"]["tokens"]) for f in known]),
        "spearman": spearman(
            [float(f["predicted_score"]) for f in known],
            [float(f["observed"]["tokens"]) for f in known],
        ),
        "off_by_two_bands": len(off),
        "off_by_two_bands_share": _ratio(len(off), len(banded)),
        "observed_band_unknown": tasks - len(banded),
        "wall_seconds_median": spread(walls)["median"] if walls else None,
        "wall_unknown": tasks - len(walls),
        "files_written_median": spread(files)["median"] if files else None,
        "files_unknown": tasks - len(files),
    }


def split(facts: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """The numbers per value of *key* (bands low first, else sorted; None last)."""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for fact in facts:
        groups.setdefault(fact.get(key), []).append(fact)

    def order(value: Any) -> tuple[int, Any]:
        if key == "predicted_band" and value in difficulty.BANDS:
            return (0, difficulty.BANDS.index(value))
        if value is None:
            return (2, "")
        return (1, value if isinstance(value, int) else str(value))

    return [
        {key: value, **numbers(groups[value])} for value in sorted(groups, key=order)
    ]


def signals(
    by_band: list[dict[str, Any]], overall: dict[str, Any]
) -> list[dict[str, Any]]:
    """What the numbers suggest about the band thresholds: information only."""
    bands = {row["predicted_band"]: row for row in by_band}
    out: list[dict[str, Any]] = []
    low = bands.get(difficulty.LOW)
    if (
        low
        and low["tasks"] >= MIN_SIGNAL_TASKS
        and (low["over_one_rework_share"] or 0) >= LOW_REWORK_SIGNAL
    ):
        out.append(
            {
                "signal": "low_band_reworked",
                "band": difficulty.LOW,
                "value": low["over_one_rework_share"],
            }
        )
    high = bands.get(difficulty.HIGH)
    if (
        low
        and high
        and min(low["tasks"], high["tasks"]) >= MIN_SIGNAL_TASKS
        and low["tokens_spread"]
        and high["tokens_spread"]
        and high["tokens_spread"]["median"] <= low["tokens_spread"]["median"]
    ):
        out.append(
            {
                "signal": "high_band_not_costlier",
                "band": difficulty.HIGH,
                "value": high["tokens_spread"]["median"],
            }
        )
    if (
        overall["tokens_known"] >= MIN_SIGNAL_TASKS
        and overall["spearman"] is not None
        and overall["spearman"] <= 0
    ):
        out.append(
            {"signal": "no_rank_agreement", "band": None, "value": overall["spearman"]}
        )
    return out


def report(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """The calibration report over *facts* (``task_fact``). Pure."""
    facts = [dict(f) for f in sorted(facts, key=lambda f: f["task_id"])]
    for fact, band in zip(facts, observed_bands(facts), strict=True):
        fact["observed_band"] = band
    overall = numbers(facts)
    by = {f"by_{key}": split(facts, key) for key in SPLITS}
    return {
        "tasks": len(facts),
        "overall": overall,
        **by,
        "predicted_recomputed": sum(
            1 for f in facts if f["predicted_source"] == "recomputed"
        ),
        "observed_computed": sum(
            1 for f in facts if f["observed_source"] == "computed"
        ),
        "signals": signals(by["by_predicted_band"], overall),
        "information_only": True,
    }


def record(session: Any, company_id: int) -> dict[str, Any]:
    """The company's calibration report over its completed sprints."""
    from models import BacklogItem, Sprint
    from sqlmodel import select

    from services.models import routing_record

    items = list(
        session.exec(
            select(BacklogItem)
            .join(Sprint, Sprint.id == BacklogItem.sprint_id)
            .where(Sprint.company_id == company_id, Sprint.status == "completed")
            .order_by(BacklogItem.id)
        ).all()
    )
    ids = [item.id for item in items]
    estimates = routing_record.first_estimates(session, company_id, set(ids))
    stored = effort.latest_stored(session, company_id, ids)
    missing = [item for item in items if item.id not in stored]
    computed = effort.collect(session, company_id, missing)
    facts = []
    for item in items:
        stored_score = difficulty.stored_score(
            estimates.get(item.id, {}).get("difficulty")
        )
        facts.append(
            task_fact(
                task_id=item.id,
                sprint_id=item.sprint_id,
                score=routing_record.difficulty_of(
                    item, estimates.get(item.id, {}).get("difficulty")
                ),
                predicted_source="estimated"
                if stored_score is not None
                else "recomputed",
                observation=stored.get(item.id) or computed.get(item.id) or {},
                observed_source="stored" if item.id in stored else "computed",
            )
        )
    return report(facts)
