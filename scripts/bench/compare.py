#!/usr/bin/env python3
"""Compare a bench scorecard with the previous one; fail beyond a threshold.

    python3 scripts/bench/compare.py bench/scorecards/<stamp>.json \\
        [--previous FILE] [--dir bench/scorecards] [--rate 0.05] [--rework 0.25] [--tokens 0.5]

Without ``--previous`` the previous scorecard is the newest one in ``--dir``
whose stamp sorts before the current one's (the stamps are UTC
``YYYYmmddTHHMMSSZ``, so the order of names is the order of runs). No
previous scorecard is not a failure: the first run sets the reference.

A regression, in the totals or in any specification both cards hold:

- the share of criteria passed (the person-written tests) fell by more than
  ``--rate`` (absolute, 0.05 = five points);
- the share of tasks done at the first attempt fell by more than ``--rate``;
- the blocked share rose by more than ``--rate``;
- rework requests per task rose by more than ``--rework`` (absolute);
- reported tokens grew by more than ``--tokens`` (relative, 0.5 = +50%),
  totals only, and only when both cards reported some;
- a specification that completed before did not complete now.

Wall-clock is shown, never judged: it depends on the machine. Prints the diff
as Markdown (the job summary and the issue body) and exits 1 on a
regression, 2 when a card does not match the schema, 0 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scorecard import validate  # noqa: E402 - the sibling module, by path.

DEFAULTS = {"rate": 0.05, "rework": 0.25, "tokens": 0.5}
# (field, label, direction): +1 when higher is better, -1 when lower is.
_JUDGED = (
    ("criteria_rate", "criteria passed", +1, "rate"),
    ("first_attempt_rate", "done at the first attempt", +1, "rate"),
    ("blocked_share", "blocked share", -1, "rate"),
    ("rework_per_task", "rework per task", -1, "rework"),
)


def _spec_rate(spec: dict[str, Any]) -> float:
    total = spec["criteria"]["total"]
    return round(spec["criteria"]["passed"] / total, 4) if total else 0.0


def _numbers(row: dict[str, Any], *, is_spec: bool) -> dict[str, float]:
    if is_spec:
        return {
            "criteria_rate": _spec_rate(row),
            "first_attempt_rate": row["first_attempt_rate"],
            "blocked_share": row["blocked_share"],
            "rework_per_task": row["rework_per_task"],
            "tokens_reported": row["tokens"]["reported"],
            "wall_clock_seconds": row["wall_clock_seconds"],
        }
    return {k: row[k] for k in (*(j[0] for j in _JUDGED), "tokens_reported", "wall_clock_seconds")}


def _judge(where: str, before: dict[str, float], after: dict[str, float], limits: dict[str, float]) -> list[str]:
    found = []
    for field, label, direction, limit in _JUDGED:
        worse = (before[field] - after[field]) * direction
        if worse > limits[limit] + 1e-9:
            found.append(f"{where}: {label} went from {before[field]:.2f} to {after[field]:.2f}")
    return found


def compare(previous: dict[str, Any], current: dict[str, Any], limits: dict[str, float] | None = None) -> dict[str, Any]:
    """The regressions of *current* against *previous* and the rows of the diff."""
    limits = {**DEFAULTS, **(limits or {})}
    regressions = _judge("total", _numbers(previous["totals"], is_spec=False),
                         _numbers(current["totals"], is_spec=False), limits)
    before_tokens = previous["totals"]["tokens_reported"]
    after_tokens = current["totals"]["tokens_reported"]
    if before_tokens and after_tokens and (after_tokens - before_tokens) / before_tokens > limits["tokens"] + 1e-9:
        regressions.append(f"total: reported tokens grew from {before_tokens} to {after_tokens}")
    rows = [("total", _numbers(previous["totals"], is_spec=False), _numbers(current["totals"], is_spec=False))]
    before_specs = {s["name"]: s for s in previous["specs"]}
    for spec in current["specs"]:
        old = before_specs.get(spec["name"])
        if old is None:
            continue
        b, a = _numbers(old, is_spec=True), _numbers(spec, is_spec=True)
        rows.append((spec["name"], b, a))
        regressions += _judge(spec["name"], b, a, limits)
        if old["status"] == "completed" and spec["status"] != "completed":
            regressions.append(f"{spec['name']}: completed before, {spec['status']} now")
    return {"regressions": regressions, "rows": rows,
            "new_specs": sorted(set(s["name"] for s in current["specs"]) - set(before_specs)),
            "dropped_specs": sorted(set(before_specs) - set(s["name"] for s in current["specs"]))}


def previous_of(current: Path, directory: Path) -> Path | None:
    """The newest scorecard in *directory* whose name sorts before *current*'s."""
    earlier = sorted(p for p in directory.glob("*.json") if p.name < current.name and p.resolve() != current.resolve())
    return earlier[-1] if earlier else None


def render(previous: dict[str, Any], current: dict[str, Any], result: dict[str, Any]) -> str:
    lines = [
        f"## Bench: {current['stamp']} against {previous['stamp']}",
        "",
        f"Commits `{previous['commit'][:12]}` → `{current['commit'][:12]}`; models `{previous['llm']}` → `{current['llm']}`.",
        "",
        "| Where | Criteria | First attempt | Blocked | Rework / task | Tokens | Wall-clock |",
        "|---|---|---|---|---|---|---|",
    ]
    for where, b, a in result["rows"]:
        lines.append(
            f"| {where} | {b['criteria_rate']:.2f} → {a['criteria_rate']:.2f} "
            f"| {b['first_attempt_rate']:.2f} → {a['first_attempt_rate']:.2f} "
            f"| {b['blocked_share']:.2f} → {a['blocked_share']:.2f} "
            f"| {b['rework_per_task']:.2f} → {a['rework_per_task']:.2f} "
            f"| {b['tokens_reported']:.0f} → {a['tokens_reported']:.0f} "
            f"| {b['wall_clock_seconds']:.0f} s → {a['wall_clock_seconds']:.0f} s |"
        )
    if result["new_specs"]:
        lines += ["", f"New specifications (no reference yet): {', '.join(result['new_specs'])}"]
    if result["dropped_specs"]:
        lines += ["", f"Specifications no longer run: {', '.join(result['dropped_specs'])}"]
    lines += ["", "### Regressions" if result["regressions"] else "No regression beyond the thresholds."]
    lines += [f"- {r}" for r in result["regressions"]]
    return "\n".join(lines) + "\n"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{path}: cannot be read: {exc}", file=sys.stderr)
        return None
    problems = validate(card)
    if problems:
        print(f"{path} does not match the scorecard schema:\n  " + "\n  ".join(problems), file=sys.stderr)
        return None
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare a bench scorecard with the previous one.")
    parser.add_argument("current", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--dir", type=Path, default=Path("bench/scorecards"))
    parser.add_argument("--rate", type=float, default=DEFAULTS["rate"])
    parser.add_argument("--rework", type=float, default=DEFAULTS["rework"])
    parser.add_argument("--tokens", type=float, default=DEFAULTS["tokens"])
    args = parser.parse_args(argv)
    current = _load(args.current)
    if current is None:
        return 2
    previous_path = args.previous or previous_of(args.current, args.dir)
    if previous_path is None:
        print(f"No previous scorecard in {args.dir}: {args.current.name} is the first reference.")
        return 0
    previous = _load(previous_path)
    if previous is None:
        return 2
    result = compare(previous, current, {"rate": args.rate, "rework": args.rework, "tokens": args.tokens})
    print(render(previous, current, result), end="")
    return 1 if result["regressions"] else 0


if __name__ == "__main__":
    sys.exit(main())
