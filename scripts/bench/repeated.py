"""Repeated bench runs per side, read as medians with their spread
(``docs/design/rsi-loop.md`` §2.4 and §2.5; REQ-6, REQ-7, REQ-16).

    compare.py C1.json C2.json C3.json \\
        --previous B1.json --previous B2.json --previous B3.json [--runs 3] [--time]

Each side's cards are put in the order of their stamps, and the i-th card of
the baseline (B) is paired with the i-th card of the candidate (C), as the
runs were made alternately (B, C, B, C, ...). Then:

- **Void pairs (REQ-7).** A pair whose models served per role (the union of
  every specification's ``models``) differ is void: neither of its cards is
  counted. A card with no partner is not counted either.
- **Complete runs.** A specification's numbers count only from runs where it
  ``completed``; the totals only from runs where every specification
  completed and the token budget did not cut the run. A ``timeout``,
  ``error`` or ``budget_exceeded`` leaves its numbers unmeasured, never zero.
- **K (REQ-6, REQ-16).** ``--runs`` K, by default the number of cards on the
  larger side. With fewer than K complete, counted runs on either side, the
  metric is ``unmeasured``: listed, not judged, and never a pass.
- **Medians and spread.** Each metric reads as its median over the K runs and
  its spread, the range (max − min) over the baseline's runs. C regresses on
  an absolute metric (criteria rate, first attempt, blocked share, rework)
  when it is worse than B's median by more than ``max(threshold,
  spread_B)``, and on a relative one (reported tokens, wall-clock, estimated
  prompt tokens) when it grew by more than ``max(threshold, spread_B /
  median_B)``. A change cannot be called worse by less than the baseline's
  own variation.
- **Completion.** A specification that completed in fewer of the counted
  pairs on C than on B is a regression; pairs where the budget stopped it on
  either side are left out.

Reported tokens are judged in the totals only, when both medians are above
zero; wall-clock only with ``--time``; prompt tokens only with
``--prompt-tokens``, as for two single cards (``compare.py``).
"""

import statistics
from typing import Any

import budget  # the sibling modules, by path (compare.py puts them on sys.path)
import graders

COMPLETED = "completed"
# (field, label, direction, limit key): +1 when higher is better, -1 when lower is.
ABSOLUTE = (
    ("criteria_rate", "criteria passed", +1, "rate"),
    ("first_attempt_rate", "done at the first attempt", +1, "rate"),
    ("blocked_share", "blocked share", -1, "rate"),
    ("rework_per_task", "rework per task", -1, "rework"),
)
# (field, label, limit key, totals only): lower is better, judged by growth.
RELATIVE = (
    ("tokens_reported", "reported tokens", "tokens", True),
    ("wall_clock_seconds", "wall-clock", "time", False),
    ("prompt_tokens_mean", "estimated prompt tokens per task", "prompt_tokens", False),
)
_EPS = 1e-9


def _spec_rate(spec: dict[str, Any]) -> float:
    total = spec["criteria"]["total"]
    return round(spec["criteria"]["passed"] / total, 4) if total else 0.0


def numbers(row: dict[str, Any], *, is_spec: bool) -> dict[str, float | None]:
    """The metrics of a spec entry or of a card's totals. The prompt mean is
    None when no prompt was measured (a card before schema v3, or none sent)."""
    prompt = row.get("prompt") or {}
    out: dict[str, float | None] = {
        "prompt_tokens_mean": prompt["tokens_mean"] if prompt.get("prompts") else None
    }
    if is_spec:
        return {
            "criteria_rate": _spec_rate(row),
            "first_attempt_rate": row["first_attempt_rate"],
            "blocked_share": row["blocked_share"],
            "rework_per_task": row["rework_per_task"],
            "tokens_reported": row["tokens"]["reported"],
            "wall_clock_seconds": row["wall_clock_seconds"],
            **out,
        }
    keys = (*(a[0] for a in ABSOLUTE), "tokens_reported", "wall_clock_seconds")
    return {**{k: row[k] for k in keys}, **out}


def served_models(card: dict[str, Any]) -> dict[str, list[str]]:
    """Per role, every model the card's specifications were served."""
    roles: dict[str, set[str]] = {}
    for spec in card["specs"]:
        for role, models in (spec.get("models") or {}).items():
            roles.setdefault(role, set()).update(models)
    return {role: sorted(models) for role, models in sorted(roles.items())}


def pairs(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[tuple[dict, dict]], list[dict[str, Any]], list[str]]:
    """The counted pairs, the void ones (with the roles whose models differ)
    and the stamps of the cards left without a partner."""
    b = sorted(before, key=lambda c: c["stamp"])
    a = sorted(after, key=lambda c: c["stamp"])
    counted, void = [], []
    for old, new in zip(b, a, strict=False):
        mo, mn = served_models(old), served_models(new)
        if mo == mn:
            counted.append((old, new))
            continue
        roles = sorted(r for r in set(mo) | set(mn) if mo.get(r) != mn.get(r))
        void.append({"before": old["stamp"], "after": new["stamp"], "roles": roles})
    n = min(len(b), len(a))
    return counted, void, [c["stamp"] for c in b[n:] + a[n:]]


def _complete_card(card: dict[str, Any]) -> bool:
    return not budget.is_cut(card) and all(
        s["status"] == COMPLETED for s in card["specs"]
    )


def _values(cards: list[dict[str, Any]], where: str) -> list[dict[str, Any]]:
    """The metrics of *where* (``total`` or a spec's name) from the complete runs."""
    if where == "total":
        return [numbers(c["totals"], is_spec=False) for c in cards if _complete_card(c)]
    found = []
    for card in cards:
        spec = next((s for s in card["specs"] if s["name"] == where), None)
        if spec is not None and spec["status"] == COMPLETED:
            found.append(numbers(spec, is_spec=True))
    return found


def summary(values: list[float | None], k: int) -> dict[str, float] | None:
    """Median and spread of *values*; None (unmeasured) with fewer than *k*."""
    measured = [v for v in values if v is not None]
    if len(measured) < k or not measured:
        return None
    return {
        "median": round(statistics.median(measured), 4),
        "spread": round(max(measured) - min(measured), 4),
        "runs": len(measured),
    }


def _judged(limits: dict[str, Any], where: str) -> list[tuple[str, str]]:
    """(field, label) of every metric judged at *where* under *limits*."""
    out = [(f, label) for f, label, _d, _l in ABSOLUTE]
    for field, label, limit, totals_only in RELATIVE:
        if limits.get(limit) is not None and not (totals_only and where != "total"):
            out.append((field, label))
    return out


def _judge(
    where: str, field: str, label: str, b: dict, a: dict, limits: dict, k: int
) -> str | None:
    tail = f"(medians over {k} runs"
    for f, _label, direction, limit in ABSOLUTE:
        if f == field:
            bar = max(limits[limit], b["spread"])
            worse = (b["median"] - a["median"]) * direction
            if worse > bar + _EPS:
                return (
                    f"{where}: {label} went from {b['median']:.2f} to "
                    f"{a['median']:.2f} {tail}, beyond {bar:.2f})"
                )
            return None
    limit = next(r[2] for r in RELATIVE if r[0] == field)
    if not b["median"] or (field == "tokens_reported" and not a["median"]):
        return None  # nothing to grow from; tokens only when both reported some
    bar = max(limits[limit], b["spread"] / b["median"])
    growth = (a["median"] - b["median"]) / b["median"]
    if growth > bar + _EPS:
        return (
            f"{where}: {label} grew from {b['median']:.0f} to {a['median']:.0f} "
            f"{tail}, +{growth:.0%} beyond +{bar:.0%})"
        )
    return None


def _completion(name: str, counted: list[tuple[dict, dict]]) -> str | None:
    done_b = done_a = n = 0
    for old, new in counted:
        sb = next((s for s in old["specs"] if s["name"] == name), None)
        sa = next((s for s in new["specs"] if s["name"] == name), None)
        if (
            sb is None
            or sa is None
            or budget.BUDGET_EXCEEDED in (sb["status"], sa["status"])
        ):
            continue
        n += 1
        done_b += sb["status"] == COMPLETED
        done_a += sa["status"] == COMPLETED
    if done_a < done_b:
        return f"{name}: completed in {done_b} of {n} runs before, {done_a} of {n} now"
    return None


def compare_runs(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    limits: dict[str, Any],
    runs: int | None = None,
) -> dict[str, Any]:
    """The regressions of the candidate's runs against the baseline's, the
    metrics left unmeasured, the void pairs and the rows of the diff."""
    k = runs or max(len(before), len(after))
    counted, void, unpaired = pairs(before, after)
    b_cards, a_cards = [p[0] for p in counted], [p[1] for p in counted]
    b_names = {s["name"] for c in before for s in c["specs"]}
    a_names = {s["name"] for c in after for s in c["specs"]}
    regressions, unmeasured, rows = [], [], []
    for where in ["total", *sorted(b_names & a_names)]:
        bv, av = _values(b_cards, where), _values(a_cards, where)
        row: dict[str, Any] = {"where": where, "complete": (len(bv), len(av))}
        missing = []
        for field in (*(x[0] for x in ABSOLUTE), *(x[0] for x in RELATIVE)):
            row[field] = (
                summary([v[field] for v in bv], k),
                summary([v[field] for v in av], k),
            )
        for field, label in _judged(limits, where):
            b, a = row[field]
            if b is None or a is None:
                missing.append(label)
                continue
            found = _judge(where, field, label, b, a, limits, k)
            if found:
                regressions.append(found)
        if missing:
            unmeasured.append(
                f"{where}: {', '.join(missing)} unmeasured "
                f"({len(bv)} of {k} complete runs before, {len(av)} of {k} after)"
            )
        if where != "total" and (found := _completion(where, counted)):
            regressions.append(found)
        rows.append(row)
    return {
        "k": k,
        "pairs": len(counted),
        "void_pairs": void,
        "unpaired": unpaired,
        "regressions": regressions,
        "unmeasured": unmeasured,
        "rows": rows,
        "new_specs": sorted(a_names - b_names),
        "dropped_specs": sorted(b_names - a_names),
    }


def _cell(pair: tuple[dict | None, dict | None], fmt: str) -> str:
    def one(s: dict | None) -> str:
        return (
            "unmeasured" if s is None else f"{s['median']:{fmt}} [{s['spread']:{fmt}}]"
        )

    return f"{one(pair[0])} → {one(pair[1])}"


def _distinct(cards: list[dict[str, Any]], key: str) -> str:
    return ", ".join(sorted({f"`{str(c.get(key, 'default'))[:12]}`" for c in cards}))


def render(
    before: list[dict[str, Any]], after: list[dict[str, Any]], result: dict[str, Any]
) -> str:
    """The diff of repeated runs as Markdown."""
    k = result["k"]
    lines = [
        f"## Bench: {len(after)} run(s) against {len(before)}, K = {k}",
        "",
        (
            f"Commits {_distinct(before, 'commit')} → {_distinct(after, 'commit')}; "
            f"models {_distinct(before, 'llm')} → {_distinct(after, 'llm')}; routing policy "
            f"{_distinct(before, 'policy')} → {_distinct(after, 'policy')}; graders "
            f"{', '.join(sorted({graders.label(c) for c in before}))} → "
            f"{', '.join(sorted({graders.label(c) for c in after}))}."
        ),
        "",
        (
            f"{result['pairs']} pair(s) counted. Each cell is the median [the spread, "
            "max − min] over the complete runs, unmeasured with fewer than K."
        ),
    ]
    lines += [
        f"- Void pair {v['before']} / {v['after']}: the models served differ "
        f"({', '.join(v['roles'])}); neither run is counted."
        for v in result["void_pairs"]
    ]
    if result["unpaired"]:
        lines.append(
            f"- Without a partner, not counted: {', '.join(result['unpaired'])}"
        )
    lines += [
        "",
        "| Where | Complete runs | Criteria | First attempt | Blocked | Rework / task | Tokens | Wall-clock (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        nb, na = row["complete"]
        lines.append(
            f"| {row['where']} | {nb} → {na} "
            f"| {_cell(row['criteria_rate'], '.2f')} | {_cell(row['first_attempt_rate'], '.2f')} "
            f"| {_cell(row['blocked_share'], '.2f')} | {_cell(row['rework_per_task'], '.2f')} "
            f"| {_cell(row['tokens_reported'], '.0f')} | {_cell(row['wall_clock_seconds'], '.0f')} |"
        )
    for key, text in (
        ("new_specs", "Only in the candidate's runs (not judged)"),
        ("dropped_specs", "Only in the baseline's runs (not judged)"),
    ):
        if result[key]:
            lines += ["", f"{text}: {', '.join(result[key])}"]
    if result["unmeasured"]:
        lines += ["", "### Unmeasured (never a pass)", ""]
        lines += [f"- {u}" for u in result["unmeasured"]]
    if result["regressions"]:
        lines += ["", "### Regressions", ""]
        lines += [f"- {r}" for r in result["regressions"]]
    elif result["unmeasured"]:
        lines += [
            "",
            "No regression among the measured metrics; the rest is unmeasured.",
        ]
    else:
        lines += ["", "No regression beyond the thresholds and the baseline's spread."]
    return "\n".join(lines) + "\n"
