"""The intake queue's vocabulary and measures, as plain data (issue #532).

A request reaches the company by one of four ways (``SOURCES``); it waits
``pending`` until a person decides it (``DECISIONS``). The moves a person may
make are ``TRANSITIONS``; the move back from ``snoozed`` when the date passes
is the system's (``WAKE``). The Support agent's suggestion and the person's
decision are separate fields of the item, and ``measures`` compares them
deterministically: it never asks a model.

After Plane's intake (``db/models/intake.py``, ``app/views/intake/base.py``,
``bgtasks/issue_automation_task.py``; AGPL-3.0, design only): a triage state
apart from the board, only an administrator changes it, and a snoozed item
comes back by itself.
"""

from __future__ import annotations

from statistics import median
from typing import Any

SOURCES = ("ticket", "brief", "webhook", "chat")
PENDING = "pending"
ACCEPTED = "accepted"
DECLINED = "declined"
DUPLICATE = "duplicate"
SNOOZED = "snoozed"
STATUSES = (PENDING, ACCEPTED, DECLINED, DUPLICATE, SNOOZED)
# What a person (or the agent's suggestion) may decide on a pending item.
DECISIONS = (ACCEPTED, DECLINED, DUPLICATE, SNOOZED)
# A decision that closes the item; ``snoozed`` only puts it aside.
FINAL = (ACCEPTED, DECLINED, DUPLICATE)
# The same words as the Support agent's ticket categories.
CATEGORIES = ("bug", "feature", "question", "incident")
UNCATEGORIZED = "uncategorized"

# from-status -> the statuses a person may move it to. An accepted item is
# final: the task or the revision it became exists. A declined or duplicate
# one may be reopened.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    PENDING: DECISIONS,
    SNOOZED: (PENDING, ACCEPTED, DECLINED, DUPLICATE),
    DECLINED: (PENDING,),
    DUPLICATE: (PENDING,),
    ACCEPTED: (),
}
WAKE = (SNOOZED, PENDING)

TITLE_LIMIT = 200
BODY_LIMIT = 8000
REASON_LIMIT = 2000
SOURCE_REF_LIMIT = 200


def can_move(current: str, target: str) -> bool:
    """Whether a person may move an item from *current* to *target*."""
    return target in TRANSITIONS.get(current, ())


def link(kind: str | None, ident: int | None) -> dict[str, Any] | None:
    """A typed link (``{"kind": "task", "id": 3}``) or None."""
    if kind is None or ident is None:
        return None
    return {"kind": kind, "id": ident}


def recorded(row: dict[str, Any]) -> bool:
    """Whether a decided item keeps what it led to: the task or revision an
    accepted one became, the item or task a duplicate repeats, the reason a
    declined one was refused for."""
    status = row.get("status")
    if status == ACCEPTED:
        return bool(row.get("outcome"))
    if status == DUPLICATE:
        return bool(row.get("duplicate_of"))
    if status == DECLINED:
        return bool((row.get("decision_reason") or "").strip())
    return False


def measures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The queue's numbers from its items, as plain dicts.

    Each row carries ``status``, ``outcome``, ``duplicate_of``,
    ``decision_reason``, ``created_at`` and ``decided_at`` (datetimes or
    None), ``suggested_status``, ``suggested_category``, ``decided_status``,
    ``decided_category`` and ``triaged``.

    - ``recorded_share``: of every item received, the share closed with what
      it led to recorded (``recorded``); None with no item.
    - ``median_seconds_to_decision``: from received to a person's final
      decision, over the closed items; None with none.
    - ``agreement``: per category (the person's, else the agent's, else
      ``uncategorized``), over the items the agent triaged and a person
      decided, how many the agent's suggested status matched the person's
      decided status, and how many it gave no usable answer for. Also the
      category itself, where both named one.
    """
    total = len(rows)
    closed = [r for r in rows if r.get("status") in FINAL]
    durations = [
        (r["decided_at"] - r["created_at"]).total_seconds()
        for r in closed
        if r.get("decided_at") is not None and r.get("created_at") is not None
    ]
    agreement: dict[str, dict[str, int]] = {}
    category = {"compared": 0, "agreed": 0}
    for r in rows:
        if not r.get("triaged") or r.get("decided_status") is None:
            continue
        name = r.get("decided_category") or r.get("suggested_category")
        tally = agreement.setdefault(
            name or UNCATEGORIZED, {"decided": 0, "agreed": 0, "unusable": 0}
        )
        tally["decided"] += 1
        suggested = r.get("suggested_status")
        tally["agreed"] += int(suggested == r["decided_status"])
        tally["unusable"] += int(suggested is None)
        if r.get("decided_category") and r.get("suggested_category"):
            category["compared"] += 1
            category["agreed"] += int(r["decided_category"] == r["suggested_category"])
    by_status = {s: 0 for s in STATUSES}
    for r in rows:
        if r.get("status") in by_status:
            by_status[r["status"]] += 1
    return {
        "received": total,
        "by_status": by_status,
        "recorded": sum(1 for r in closed if recorded(r)),
        "recorded_share": (
            round(sum(1 for r in closed if recorded(r)) / total, 4) if total else None
        ),
        "median_seconds_to_decision": median(durations) if durations else None,
        "agreement": dict(sorted(agreement.items())),
        "category_agreement": category,
    }
