"""Paired comparison of repeated bench runs, with a significance test.

    paired.py C1.json C2.json C3.json \\
        --previous B1.json --previous B2.json --previous B3.json [--alpha 0.05]

``repeated.py`` reads K runs per side as medians and refuses to call a change
worse by less than the baseline's spread; it never says a change is better,
and a single spread can hide everything when a model is as noisy as DeepSeek
was on specs 05 and 06 on 2026-11-22 (0/7, 2/7 and 7/7 criteria in three
runs of the same spec). This module compares the same items on both sides
and tests whether the difference could be chance:

- **Criteria (McNemar).** Each acceptance criterion of a specification that
  completed on both cards of a pair is one paired pass/fail. The pairs that
  disagree decide: ``b`` passed before and fails now, ``c`` failed before and
  passes now. Under "no difference" each is a fair coin, so the two-sided p
  is the exact binomial tail for up to 25 discordant pairs and the
  continuity-corrected chi-square above.
- **Numbers (Wilcoxon signed-rank).** Per specification completed on both
  cards, the paired difference of first-attempt share, blocked share, rework
  per task and reported tokens. The signed-rank p is exact (enumerated) for
  up to 20 non-zero differences and the normal approximation above; the
  mean difference comes with a 95% percentile bootstrap interval (10 000
  resamples, fixed seed, so a card always reads the same).

A metric is ``better`` or ``worse`` only when p < alpha, and otherwise ``no
demonstrable difference``: never a verdict from too few pairs. Pairs are made
as ``repeated.pairs`` makes them (by stamp; a pair whose served models differ
is void), the models compared over the specifications both cards ran and
the roles served on both sides: a role only one side called (the
ScrumMaster, when a task was blocked) is an outcome, not a configuration. Exit status: 1 when anything is significantly worse, 2 when a card
does not match the schema, 0 otherwise.

The method is MLflow's paired comparison of two evaluation runs
(``mlflow/genai/evaluation/comparison/``, Apache-2.0: McNemar exact below 25
discordant pairs, Wilcoxon exact below 50, a seeded bootstrap). This module
is written from that design with the standard library only; no code is
copied.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repeated  # the sibling module, by path
from schema import validate

MCNEMAR_EXACT_MAX = 25
WILCOXON_EXACT_MAX = 20
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0
# (field, label, +1 when higher is better / -1 when lower is)
NUMBERS = (
    ("first_attempt_rate", "done at the first attempt", +1),
    ("blocked_share", "blocked share", -1),
    ("rework_per_task", "rework per task", -1),
    ("tokens_reported", "reported tokens", -1),
)
BETTER, WORSE, SAME = "better", "worse", "no demonstrable difference"


def _normal_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def mcnemar(b: int, c: int) -> float:
    """Two-sided p of McNemar's test on *b* and *c* discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    if n <= MCNEMAR_EXACT_MAX:
        tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
        return min(1.0, 2 * tail)
    chi2 = (abs(b - c) - 1) ** 2 / n
    return _normal_two_sided(math.sqrt(chi2))


def wilcoxon(deltas: list[float]) -> float:
    """Two-sided p of the Wilcoxon signed-rank test on paired *deltas*
    (zeros dropped, ties given their mean rank)."""
    nonzero = [d for d in deltas if d != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    order = sorted(range(n), key=lambda i: abs(nonzero[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nonzero[order[j + 1]]) == abs(nonzero[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    w_plus = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
    mean = sum(ranks) / 2
    if n <= WILCOXON_EXACT_MAX:
        observed = abs(w_plus - mean)
        extreme = sum(
            1
            for signs in itertools.product((0, 1), repeat=n)
            if abs(sum(r for r, s in zip(ranks, signs, strict=True) if s) - mean)
            >= observed - 1e-9
        )
        return extreme / 2**n
    sd = math.sqrt(sum(r * r for r in ranks) / 4)
    return _normal_two_sided((w_plus - mean) / sd) if sd else 1.0


def bootstrap_ci(deltas: list[float], level: float = 0.95) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean of *deltas* (seeded)."""
    if len(set(deltas)) <= 1:
        value = deltas[0] if deltas else 0.0
        return value, value
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    means = sorted(
        sum(rng.choice(deltas) for _ in range(n)) / n
        for _ in range(BOOTSTRAP_RESAMPLES)
    )
    low = means[int((1 - level) / 2 * BOOTSTRAP_RESAMPLES)]
    high = means[
        min(BOOTSTRAP_RESAMPLES - 1, int((1 + level) / 2 * BOOTSTRAP_RESAMPLES))
    ]
    return round(low, 4), round(high, 4)


def pairs(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[tuple[dict, dict]], list[dict[str, Any]], list[str]]:
    """The pairs, as ``repeated.pairs`` makes them."""
    return repeated.pairs(before, after)


def _completed_specs(old: dict, new: dict) -> list[tuple[dict, dict]]:
    by_name = {s["name"]: s for s in old["specs"]}
    out = []
    for spec in new["specs"]:
        before = by_name.get(spec["name"])
        if (
            before is not None
            and before["status"] == spec["status"] == repeated.COMPLETED
        ):
            out.append((before, spec))
    return out


def _verdict(p: float, direction: float, alpha: float) -> str:
    if p >= alpha or direction == 0:
        return SAME
    return BETTER if direction > 0 else WORSE


def compare(
    before: list[dict[str, Any]], after: list[dict[str, Any]], alpha: float = 0.05
) -> dict[str, Any]:
    """The paired comparison of the baseline's cards and the candidate's."""
    counted, void, unpaired = pairs(before, after)
    b = c = both_pass = both_fail = 0
    deltas: dict[str, list[float]] = {field: [] for field, _l, _d in NUMBERS}
    for old, new in counted:
        for spec_b, spec_c in _completed_specs(old, new):
            if repeated.grade_unavailable(spec_b) or repeated.grade_unavailable(spec_c):
                continue
            res_b = spec_b["criteria"].get("results") or {}
            res_c = spec_c["criteria"].get("results") or {}
            for criterion in sorted(set(res_b) & set(res_c)):
                was, now = res_b[criterion] == "pass", res_c[criterion] == "pass"
                b += was and not now
                c += now and not was
                both_pass += was and now
                both_fail += not was and not now
            nb = repeated.numbers(spec_b, is_spec=True)
            nc = repeated.numbers(spec_c, is_spec=True)
            for field, _label, _d in NUMBERS:
                if nb.get(field) is not None and nc.get(field) is not None:
                    deltas[field].append(float(nc[field]) - float(nb[field]))
    p_criteria = mcnemar(b, c)
    result: dict[str, Any] = {
        "pairs": len(counted),
        "void": void,
        "unpaired": unpaired,
        "alpha": alpha,
        "criteria": {
            "fixed": c,
            "broken": b,
            "both_pass": both_pass,
            "both_fail": both_fail,
            "p": round(p_criteria, 4),
            "verdict": _verdict(p_criteria, c - b, alpha),
        },
        "numbers": [],
    }
    for field, label, direction in NUMBERS:
        values = deltas[field]
        if not values:
            result["numbers"].append(
                {"field": field, "label": label, "n": 0, "verdict": SAME}
            )
            continue
        p = wilcoxon(values)
        mean = statistics.fmean(values)
        result["numbers"].append(
            {
                "field": field,
                "label": label,
                "n": len(values),
                "mean_delta": round(mean, 4),
                "ci95": bootstrap_ci(values),
                "p": round(p, 4),
                "verdict": _verdict(p, mean * direction, alpha),
            }
        )
    return result


def render(result: dict[str, Any]) -> str:
    crit = result["criteria"]
    lines = [
        f"Paired comparison: {result['pairs']} pair(s), alpha {result['alpha']}"
        + (f"; {len(result['void'])} void" if result["void"] else "")
        + (
            f"; unpaired: {', '.join(result['unpaired'])}" if result["unpaired"] else ""
        ),
        "",
        "| Metric | n | Change | 95% interval | p | Verdict |",
        "|---|---|---|---|---|---|",
        (
            f"| criteria (McNemar) | {crit['fixed'] + crit['broken']} discordant | "
            f"+{crit['fixed']} fixed, -{crit['broken']} broken | | {crit['p']} | {crit['verdict']} |"
        ),
    ]
    for row in result["numbers"]:
        if not row["n"]:
            lines.append(f"| {row['label']} | 0 | | | | unmeasured |")
            continue
        low, high = row["ci95"]
        lines.append(
            f"| {row['label']} (Wilcoxon) | {row['n']} | {row['mean_delta']:+g} | "
            f"[{low:+g}, {high:+g}] | {row['p']} | {row['verdict']} |"
        )
    return "\n".join(lines)


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
        description="Paired comparison of repeated bench runs."
    )
    parser.add_argument("current", type=Path, nargs="+", help="the candidate's cards")
    parser.add_argument(
        "--previous", type=Path, action="append", required=True, help="a baseline card"
    )
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    after = [_load(p) for p in args.current]
    before = [_load(p) for p in args.previous]
    if any(card is None for card in after + before):
        return 2
    result = compare(before, after, args.alpha)
    print(json.dumps(result, indent=2) if args.json else render(result))
    worse = result["criteria"]["verdict"] == WORSE or any(
        row["verdict"] == WORSE for row in result["numbers"]
    )
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
