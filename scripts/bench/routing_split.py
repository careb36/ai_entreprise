"""Two routing policies side by side: by difficulty band and by served model
(issue #319, ``compare.py``).

A card's ``routing`` rows (per spec, per difficulty and model of the last
delivery) are summed over the specifications into one row per difficulty
band (``low``, ``medium``, ``high``: the estimate's own bands,
``services/models/difficulty.py``) and one per served model, with done, the
share done at the first attempt, rework per task, the blocked share, the
reported tokens and, when the card carries it, the cost in US cents (tasks
whose cost is unknown are counted apart, never as 0). A row from a card
written before the band was recorded is placed by its difficulty score.

The tables are shown, never judged: a policy is meant to move work between
models, and whether it pays is read from these numbers by a person, the way
a procedure is kept or reverted.
"""

from typing import Any

BANDS = ("low", "medium", "high")
# The scores each band holds, as services/models/difficulty.py has them.
_BAND_OF_SCORE = {1: "low", 2: "low", 3: "medium", 4: "high", 5: "high"}


def band_of(row: dict[str, Any]) -> str:
    band = row.get("difficulty_band")
    return band if band in BANDS else _BAND_OF_SCORE.get(row.get("difficulty"), "low")


def _empty() -> dict[str, Any]:
    return {
        "tasks": 0,
        "done": 0,
        "first": 0,
        "blocked": 0,
        "rework": 0,
        "tokens": 0,
        "cents": None,
        "cost_unknown": 0,
    }


def split(card: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    """The card's tasks summed by ``band`` or ``model`` over its specs."""
    out: dict[str, dict[str, Any]] = {}
    for spec in card.get("specs") or []:
        for row in spec.get("routing") or []:
            name = band_of(row) if key == "band" else (row.get("model") or "none")
            cell = out.setdefault(name, _empty())
            cell["tasks"] += row["tasks"]
            cell["done"] += row["done"]
            cell["first"] += row["done_first_attempt"]
            cell["blocked"] += row["blocked"]
            cell["rework"] += row["rework"]
            cell["tokens"] += row["tokens"]["reported"]
            cents = row.get("cost_cents")
            if cents is not None:
                cell["cents"] = (cell["cents"] or 0) + cents
            cell["cost_unknown"] += row.get("cost_unknown", row["tasks"] if cents is None else 0)
    return out


def _cell(cell: dict[str, Any] | None) -> tuple[str, ...]:
    if not cell or not cell["tasks"]:
        return ("–",) * 7
    tasks = cell["tasks"]
    cost = "unknown" if cell["cents"] is None else f"{cell['cents']:.2f}¢"
    if cell["cents"] is not None and cell["cost_unknown"]:
        cost += f" (+{cell['cost_unknown']} unknown)"
    return (
        str(tasks),
        str(cell["done"]),
        f"{cell['first'] / tasks:.2f}",
        f"{cell['rework'] / tasks:.2f}",
        f"{cell['blocked'] / tasks:.2f}",
        str(cell["tokens"]),
        cost,
    )


def _order(key: str, names: set[str]) -> list[str]:
    if key == "band":
        return [b for b in BANDS if b in names]
    return sorted(names, key=lambda n: (n == "none", n))


def render(previous: dict[str, Any], current: dict[str, Any], key: str) -> list[str]:
    """The Markdown table by ``band`` or ``model``, before → after; empty without a split."""
    before, after = split(previous, key), split(current, key)
    if not before and not after:
        return []
    title = "difficulty band" if key == "band" else "served model"
    lines = [
        "",
        f"### By {title} (policy `{previous.get('policy', 'default')}` → `{current.get('policy', 'default')}`)",
        "",
        f"| {title.capitalize()} | Tasks | Done | First attempt | Rework / task | Blocked | Tokens | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in _order(key, set(before) | set(after)):
        b, a = _cell(before.get(name)), _cell(after.get(name))
        lines.append(
            f"| {name} | " + " | ".join(f"{x} → {y}" for x, y in zip(b, a, strict=True)) + " |"
        )
    return lines
