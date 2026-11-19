#!/usr/bin/env python3
"""Compare a bench scorecard with the previous one; fail beyond a threshold.

    python3 scripts/bench/compare.py bench/scorecards/<stamp>.json \\
        [--previous FILE] [--dir bench/scorecards] [--rate 0.05] [--rework 0.25] [--tokens 0.5] \\
        [--prompt-tokens 0.2] [--time [0.25]]
    python3 scripts/bench/compare.py C1.json C2.json C3.json \\
        --previous B1.json --previous B2.json --previous B3.json [--runs 3] [...]

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
- a specification that completed before did not complete now;
- with ``--prompt-tokens`` (off by default), the mean estimated prompt tokens
  per task grew by more than that (relative, 0.2 = +20%), in the totals or a
  specification, when both cards measured prompts (schema v3);
- with ``--time`` (off by default; alone it means 0.25, the wall-clock
  threshold of ``docs/design/rsi-loop.md`` §10 decision 4), wall-clock grew by
  more than that (relative, 0.25 = +25%), in the totals or a specification.

A run the token budget cut (``run.status: budget_exceeded``, ``budget.py``)
on either side is not judged in the totals, which cover fewer
specifications, nor in the specification the budget stopped: the diff says
the run was cut, shows its budget and tokens, lists the specifications it
skipped (never graded, never a drop) and judges only the specifications
both cards ran in full. A card without ``run`` is a complete run.

Without ``--time``, wall-clock is shown, never judged: it depends on the
machine, so judge it only for runs made on the same one. Two runs made
under different routing policies (``scorecard.py write --policy``, each run
with its own ``AGENT_MODEL_POLICY``) are compared the same way, and the diff
adds a table by difficulty: per band, the tasks, the share done at the first
attempt, rework per task, the blocked share, the reported tokens and the
models that delivered, summed over the specifications. The table by
difficulty is shown, never judged: a policy is meant to move work between
models. A card from before the split (schema v1) shows none. A table of the
prompts' weight per section (estimated tokens, schema v3) is shown the same
way, never judged; only ``--prompt-tokens`` judges the mean. Prints the diff
as Markdown (the job summary and the issue body) and exits 1 on a
regression, 2 when a card does not match the schema, 0 otherwise.

Several cards per side (or ``--runs K``) are repeated runs: each side reads as
medians with their spread, a pair of runs served different models is void,
and a metric with fewer than K complete runs is unmeasured (``repeated.py``,
which says how). Then the exit status is 3 when nothing regressed but a
judged metric is unmeasured.

A card carrying a section ablation (schema v4, ``ablate.sh``,
``ablation.py``) adds its table: per prompt section turned off, the arm's
criteria passed, rework requests, estimated prompt tokens and reported tokens
beside the baseline's, with the difference. It is printed even with no
previous card and never judged: the exit status is the comparison's alone.
The header names the sections either run turned off (``sections_off``).

A card carrying HumanEval's block (schema v5, ``run.sh --humaneval``,
``humaneval.py``) adds pass@1 and each problem's outcome, before → after,
or the block alone with no previous card; shown, never judged.

A card carrying SWE-bench Lite's block (schema v6, ``run.sh --swebench``,
``swebench.py``) adds the resolve rate and each instance's outcome, before →
after, or the block alone with no previous card; shown, never judged.

A card carrying the edit format per model (schema v7, ``edit_formats.sh``,
``edit_format_card.py``) adds each model and format's outcome and criteria,
before → after, or the table alone with no previous card; shown, never
judged. The header names the format each run asked for (``edit_format``).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation  # the sibling modules, by path
import budget
import edit_format_card
import graders
import humaneval_card
import prompt_weight
import repeated
import routing_split
import swebench_card
from schema import validate

DEFAULTS = {
    "rate": 0.05,
    "rework": 0.25,
    "tokens": 0.5,
    "prompt_tokens": None,
    "time": None,
}
TIME_DEFAULT = 0.25  # docs/design/rsi-loop.md §10, decision 4
EXIT_UNMEASURED = 3
# (field, label, direction, limit): +1 when higher is better, -1 when lower is.
_JUDGED = repeated.ABSOLUTE
_numbers = repeated.numbers


def _judge(
    where: str,
    before: dict[str, float],
    after: dict[str, float],
    limits: dict[str, float],
) -> list[str]:
    found = []
    for field, label, direction, limit in _JUDGED:
        worse = (before[field] - after[field]) * direction
        if worse > limits[limit] + 1e-9:
            found.append(
                f"{where}: {label} went from {before[field]:.2f} to {after[field]:.2f}"
            )
    return found


def _judge_time(
    where: str, before: dict[str, Any], after: dict[str, Any], limit: float | None
) -> list[str]:
    """Wall-clock, judged only with ``--time``: relative growth beyond it."""
    b, a = before["wall_clock_seconds"], after["wall_clock_seconds"]
    if limit is None or not b or (a - b) / b <= limit + 1e-9:
        return []
    return [f"{where}: wall-clock grew from {b:.0f} s to {a:.0f} s"]


def _judge_prompt(
    where: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    limit: float | None,
) -> list[str]:
    """The mean estimated prompt tokens per task, judged only with a limit and
    only when both sides measured prompts."""
    if limit is None or not before or not after:
        return []
    if not (before["prompts"] and after["prompts"] and before["tokens_mean"]):
        return []
    growth = (after["tokens_mean"] - before["tokens_mean"]) / before["tokens_mean"]
    if growth <= limit + 1e-9:
        return []
    mean = f"{before['tokens_mean']:.0f} to {after['tokens_mean']:.0f}"
    return [f"{where}: estimated prompt tokens per task grew from {mean}"]


def _judge_totals(
    previous: dict[str, Any], current: dict[str, Any], limits: dict[str, Any]
) -> list[str]:
    """The regressions in the totals of two complete runs."""
    before = _numbers(previous["totals"], is_spec=False)
    after = _numbers(current["totals"], is_spec=False)
    found = _judge("total", before, after, limits)
    found += _judge_time("total", before, after, limits["time"])
    found += _judge_prompt(
        "total",
        previous["totals"].get("prompt"),
        current["totals"].get("prompt"),
        limits["prompt_tokens"],
    )
    before_tokens = previous["totals"]["tokens_reported"]
    after_tokens = current["totals"]["tokens_reported"]
    if (
        before_tokens
        and after_tokens
        and (after_tokens - before_tokens) / before_tokens > limits["tokens"] + 1e-9
    ):
        found.append(
            f"total: reported tokens grew from {before_tokens} to {after_tokens}"
        )
    return found


def compare(
    previous: dict[str, Any],
    current: dict[str, Any],
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The regressions of *current* against *previous* and the rows of the diff."""
    limits = {**DEFAULTS, **(limits or {})}
    cut = budget.is_cut(previous) or budget.is_cut(current)
    # A cut run covers fewer specifications: its totals are shown, not judged.
    regressions = [] if cut else _judge_totals(previous, current, limits)
    rows = [
        (
            "total",
            _numbers(previous["totals"], is_spec=False),
            _numbers(current["totals"], is_spec=False),
        )
    ]
    before_specs = {s["name"]: s for s in previous["specs"]}
    unjudged = []
    for spec in current["specs"]:
        old = before_specs.get(spec["name"])
        if old is None:
            continue
        b, a = _numbers(old, is_spec=True), _numbers(spec, is_spec=True)
        rows.append((spec["name"], b, a))
        if budget.BUDGET_EXCEEDED in (old["status"], spec["status"]):
            unjudged.append(spec["name"])  # stopped by the budget: not run in full
            continue
        regressions += _judge(spec["name"], b, a, limits)
        regressions += _judge_time(spec["name"], b, a, limits["time"])
        regressions += _judge_prompt(
            spec["name"], old.get("prompt"), spec.get("prompt"), limits["prompt_tokens"]
        )
        if old["status"] == "completed" and spec["status"] != "completed":
            regressions.append(
                f"{spec['name']}: completed before, {spec['status']} now"
            )
    skipped = set(budget.skipped_names(current))
    return {
        "regressions": regressions,
        "rows": rows,
        "cut": cut,
        "unjudged_specs": unjudged,
        "skipped_specs": sorted(skipped),
        "new_specs": sorted({s["name"] for s in current["specs"]} - set(before_specs)),
        "dropped_specs": sorted(
            set(before_specs) - {s["name"] for s in current["specs"]} - skipped
        ),
    }


def render_cut(
    previous: dict[str, Any], current: dict[str, Any], result: dict[str, Any]
) -> list[str]:
    """What the diff says when the token budget cut either run."""
    if not result.get("cut"):
        return []
    lines = []
    for side, card in (("This run", current), ("The previous run", previous)):
        if budget.is_cut(card):
            run = card["run"]
            lines.append(
                f"{side} was cut: token budget {run['budget']} exceeded, "
                f"{run['tokens_spent']} reported tokens spent, "
                f"{run['turns_unreported']} turn(s) without reported usage (not budgeted)."
            )
    lines.append(
        "The totals are shown, not judged; only the specifications both runs ran in full are."
    )
    if result["unjudged_specs"]:
        lines.append(
            f"Stopped by the budget, not judged: {', '.join(result['unjudged_specs'])}"
        )
    if result["skipped_specs"]:
        lines.append(
            f"Skipped for the budget (not run, not graded): {', '.join(result['skipped_specs'])}"
        )
    return [*lines, ""]


def by_difficulty(card: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """The card's tasks per difficulty, summed over its specs and models."""
    out: dict[int, dict[str, Any]] = {}
    for spec in card["specs"]:
        for row in spec.get("routing") or []:
            band = out.setdefault(
                row["difficulty"],
                {
                    "tasks": 0,
                    "first": 0,
                    "blocked": 0,
                    "rework": 0,
                    "tokens": 0,
                    "models": set(),
                },
            )
            band["tasks"] += row["tasks"]
            band["first"] += row["done_first_attempt"]
            band["blocked"] += row["blocked"]
            band["rework"] += row["rework"]
            band["tokens"] += row["tokens"]["reported"]
            band["models"].add(row["model"] or "none")
    return out


def _band_cell(band: dict[str, Any] | None) -> tuple[str, ...]:
    if not band or not band["tasks"]:
        return ("–",) * 6
    tasks = band["tasks"]
    return (
        str(tasks),
        f"{band['first'] / tasks:.2f}",
        f"{band['rework'] / tasks:.2f}",
        f"{band['blocked'] / tasks:.2f}",
        str(band["tokens"]),
        ", ".join(sorted(band["models"])),
    )


def render_bands(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """The Markdown table by difficulty, before → after; empty without a split."""
    before, after = by_difficulty(previous), by_difficulty(current)
    if not before and not after:
        return []
    lines = [
        "",
        f"### By difficulty (policy `{previous.get('policy', 'default')}` → `{current.get('policy', 'default')}`)",
        "",
        "| Difficulty | Tasks | First attempt | Rework / task | Blocked | Tokens | Models |",
        "|---|---|---|---|---|---|---|",
    ]
    for difficulty in sorted(set(before) | set(after)):
        b, a = _band_cell(before.get(difficulty)), _band_cell(after.get(difficulty))
        lines.append(
            f"| {difficulty} | "
            + " | ".join(f"{x} → {y}" for x, y in zip(b, a, strict=True))
            + " |"
        )
    return lines


def previous_of(current: Path, directory: Path) -> Path | None:
    """The newest scorecard in *directory* whose name sorts before *current*'s."""
    earlier = sorted(
        p
        for p in directory.glob("*.json")
        if p.name < current.name and p.resolve() != current.resolve()
    )
    return earlier[-1] if earlier else None


def render(
    previous: dict[str, Any], current: dict[str, Any], result: dict[str, Any]
) -> str:
    lines = [
        f"## Bench: {current['stamp']} against {previous['stamp']}",
        "",
        (
            f"Commits `{previous['commit'][:12]}` → `{current['commit'][:12]}`; models `{previous['llm']}` → `{current['llm']}`; "
            f"routing policy `{previous.get('policy', 'default')}` → `{current.get('policy', 'default')}`; "
            f"graders {graders.label(previous)} → {graders.label(current)}."
            + _sections_off(previous, current)
            + _edit_format(previous, current)
        ),
        "",
        *render_cut(previous, current, result),
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
    lines += render_bands(previous, current)
    lines += routing_split.render(previous, current, "band")
    lines += routing_split.render(previous, current, "model")
    lines += prompt_weight.render_table(previous, current)
    lines += ablation.render_lines(current, level="###")
    lines += humaneval_card.render_compare(previous, current)
    lines += swebench_card.render_compare(previous, current)
    lines += edit_format_card.render_compare(previous, current)
    if result["new_specs"]:
        lines += [
            "",
            f"New specifications (no reference yet): {', '.join(result['new_specs'])}",
        ]
    if result["dropped_specs"]:
        lines += [
            "",
            f"Specifications no longer run: {', '.join(result['dropped_specs'])}",
        ]
    lines += [
        "",
        "### Regressions"
        if result["regressions"]
        else "No regression beyond the thresholds.",
    ]
    lines += [f"- {r}" for r in result["regressions"]]
    return "\n".join(lines) + "\n"


def _sections_off(previous: dict[str, Any], current: dict[str, Any]) -> str:
    """The header's prompt sections turned off, when either run turned some off."""
    if not previous.get("sections_off") and not current.get("sections_off"):
        return ""
    off = [
        ", ".join(c.get("sections_off") or []) or "none" for c in (previous, current)
    ]
    return f" Prompt sections off: {off[0]} → {off[1]}."


def _edit_format(previous: dict[str, Any], current: dict[str, Any]) -> str:
    """The header's edit format, when either run asked for one (#689)."""
    if "edit_format" not in previous and "edit_format" not in current:
        return ""
    fmt = [c.get("edit_format") or "auto (unset)" for c in (previous, current)]
    return f" Edit format: {fmt[0]} → {fmt[1]}."


def _load(path: Path) -> dict[str, Any] | None:
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{path}: cannot be read: {exc}", file=sys.stderr)
        return None
    problems = validate(card)
    if problems:
        print(
            f"{path} does not match the scorecard schema:\n  " + "\n  ".join(problems),
            file=sys.stderr,
        )
        return None
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a bench scorecard with the previous one."
    )
    parser.add_argument("current", type=Path, nargs="+", help="the candidate's card(s)")
    parser.add_argument(
        "--previous",
        type=Path,
        action="append",
        default=[],
        help="the baseline's card; repeat it for repeated runs",
    )
    parser.add_argument("--dir", type=Path, default=Path("bench/scorecards"))
    parser.add_argument("--rate", type=float, default=DEFAULTS["rate"])
    parser.add_argument("--rework", type=float, default=DEFAULTS["rework"])
    parser.add_argument("--tokens", type=float, default=DEFAULTS["tokens"])
    parser.add_argument(
        "--prompt-tokens",
        type=float,
        default=DEFAULTS["prompt_tokens"],
        help="judge the mean estimated prompt tokens per task (relative); off by default",
    )
    parser.add_argument(
        "--time",
        type=float,
        nargs="?",
        const=TIME_DEFAULT,
        default=DEFAULTS["time"],
        help=f"judge wall-clock (relative; {TIME_DEFAULT} when given alone); off by default",
    )
    parser.add_argument(
        "--runs",
        type=int,
        help="K, the complete runs each side needs (repeated runs); "
        "by default the number of cards on the larger side",
    )
    args = parser.parse_args(argv)
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be at least 1")
    limits = {
        "rate": args.rate,
        "rework": args.rework,
        "tokens": args.tokens,
        "prompt_tokens": args.prompt_tokens,
        "time": args.time,
    }
    if len(args.current) > 1 or len(args.previous) > 1 or args.runs is not None:
        return _main_repeated(args.current, args.previous, limits, args.runs)
    current = _load(args.current[0])
    if current is None:
        return 2
    previous_path = (
        args.previous[0] if args.previous else previous_of(args.current[0], args.dir)
    )
    if previous_path is None:
        print(
            f"No previous scorecard in {args.dir}: {args.current[0].name} is the first reference."
        )
        if current.get("ablation"):
            print("\n".join(ablation.render_lines(current, level="###")))
        if current.get("humaneval"):
            print("\n".join(humaneval_card.render_lines(current, level="###")))
        if current.get("swebench"):
            print("\n".join(swebench_card.render_lines(current, level="###")))
        if current.get("edit_formats"):
            print("\n".join(edit_format_card.render_lines(current, level="###")))
        return 0
    previous = _load(previous_path)
    if previous is None:
        return 2
    result = compare(previous, current, limits)
    print(render(previous, current, result), end="")
    return 1 if result["regressions"] else 0


def _main_repeated(
    current: list[Path],
    previous: list[Path],
    limits: dict[str, Any],
    runs: int | None,
) -> int:
    """Repeated runs per side: medians, spread, void pairs (``repeated.py``)."""
    if not previous:
        print("repeated runs need the baseline's cards (--previous)", file=sys.stderr)
        return 2
    after = [_load(p) for p in current]
    before = [_load(p) for p in previous]
    if any(card is None for card in (*after, *before)):
        return 2
    for side, cards in (("candidate", after), ("baseline", before)):
        stamps = [card["stamp"] for card in cards]
        repeats = sorted({s for s in stamps if stamps.count(s) > 1})
        if repeats:
            # One run passed twice, or under two paths, is one run: counting
            # it again would make up K.
            print(
                f"the {side}'s cards repeat a run: {', '.join(repeats)}",
                file=sys.stderr,
            )
            return 2
    result = repeated.compare_runs(before, after, limits, runs)
    print(repeated.render(before, after, result), end="")
    if result["regressions"]:
        return 1
    return EXIT_UNMEASURED if result["unmeasured"] else 0


if __name__ == "__main__":
    sys.exit(main())
