"""The fitness decision of the self-improvement loop: keep, revert or unmeasured.

``decide(baseline, candidate, graders=..., target=..., protected=...)`` reads
observations of the baseline B (the commit of ``main`` a cycle starts from)
and of the candidate C (the head of the cycle's pull request) and answers
``keep``, ``revert`` or ``unmeasured`` with every reason
(``docs/design/rsi-loop.md`` §2.4 to §2.6, REQ-5, REQ-15, REQ-16, REQ-27).
It is pure: no I/O, no clock, no model call (rule 5 of
``docs/POSITIONING.md``), and it imports no other context, not even
contracts: the protected-path match is an input, computed by the runner from
git with ``contracts/protected_paths.matches``.

**Provenance.** Every observation names who produced it (``produced_by``).
Only ``PLATFORM`` is accepted: the bench runner, the runner's reading of
GitHub's check-runs by sha, the target measure run by the runner, git. An
observation produced by anyone else (the build under test, a producer, an
agent) is refused and counts as missing (REQ-27, rule 6). An observation is
also refused when it names a commit other than the side's sha, a check-run
included. No hash is read from any input.

**The decision.** Every guard is judged and named in ``reasons``, each with
its ``outcome`` (``pass``, ``revert`` or ``unmeasured``; ``ignored`` names an
input left out of the count, such as a void pair, and never decides). The
decision is

1. ``revert`` when C's diff touches a protected path without a person's
   approval recorded by the platform at C's sha naming every such path
   (REQ-5);
2. otherwise ``unmeasured`` when any guard is unmeasured: an observation
   missing or refused, graders that differ, fewer than K complete runs on
   either side for a metric, CI not finished (REQ-16; unknown is not absent,
   rule 7);
3. otherwise ``revert`` when any guard regressed: a failing or missing CI
   check, fewer tests collected, a Verifier bench case B passes and C fails,
   the fake bench below 100%, the target measure not going from fail on B to
   pass on C, or a bench metric worse beyond ``max(threshold, spread_B)``;
4. otherwise ``keep`` (REQ-15).

**Bench runs.** Each side's runs are paired by ``pair`` (B, C, B, C...).
A pair whose models per role differ is void and neither run counts
(REQ-7); a run without a partner does not count either. A run that did not
complete (``timeout``, ``budget_exceeded``, ``error``) leaves its metrics
unmeasured. Medians are compared in the totals and per specification; the
spread is B's range over its counted runs, relative to B's median for the
relative metrics. The prompt weight is judged only when the target says the
specification targets the prompt.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from services.evidence.fitness_checks import (
    CI_FAILING,
    CI_PASSING,
    IGNORED,
    KEEP,
    PASS,
    PLATFORM,
    REVERT,
    TRUSTED_CI_APPS,
    UNMEASURED,
    _accepted,
    _ci,
    _fake,
    _graders,
    _protected,
    _reason,
    _verifier,
)

__all__ = [
    "CI_FAILING",
    "CI_PASSING",
    "COMPLETE",
    "GUARDED",
    "IGNORED",
    "KEEP",
    "METRICS",
    "PASS",
    "PLATFORM",
    "REVERT",
    "THRESHOLDS",
    "TRUSTED_CI_APPS",
    "UNMEASURED",
    "K",
    "decide",
]
K = 3
# The design's decision 4 and compare.py's DEFAULTS: tokens and wall-clock
# relative, the rates and rework absolute.
THRESHOLDS: dict[str, float] = {
    "rate": 0.05,
    "rework": 0.25,
    "tokens": 0.15,
    "time": 0.25,
    "prompt_tokens": 0.2,
}
# metric -> (threshold key, +1 when higher is better / -1 when lower, relative)
METRICS: dict[str, tuple[str, int, bool]] = {
    "criteria_rate": ("rate", +1, False),
    "first_attempt_rate": ("rate", +1, False),
    "blocked_share": ("rate", -1, False),
    "rework_per_task": ("rework", -1, False),
    "tokens_reported": ("tokens", -1, True),
    "wall_clock_seconds": ("time", -1, True),
    "prompt_tokens_mean": ("prompt_tokens", -1, True),
}
GUARDED = tuple(m for m in METRICS if m != "prompt_tokens_mean")
COMPLETE = "complete"
_EPS = 1e-9


def _models(run: Mapping) -> dict[str, list[str]]:
    models = run.get("models") or {}
    return {str(role): sorted(map(str, ms or [])) for role, ms in models.items()}


def _counted_runs(
    b: Mapping, c: Mapping, b_sha: str, c_sha: str, reasons: list[dict]
) -> tuple[list[Mapping], list[Mapping]]:
    """The runs of each side that count: accepted, paired, models equal."""
    sides = []
    for side, obs, sha in (("baseline", b, b_sha), ("candidate", c, c_sha)):
        by_pair: dict[Any, Mapping] = {}
        for index, run in enumerate(obs.get("bench") or []):
            guard = f"bench_{side}[{index}]"
            if _accepted(run, guard, reasons, sha):
                by_pair[run.get("pair", index)] = run
        sides.append(by_pair)
    counted_b, counted_c = [], []
    for pair in sorted(set(sides[0]) | set(sides[1]), key=str):
        rb, rc = sides[0].get(pair), sides[1].get(pair)
        if rb is None or rc is None:
            _reason(
                reasons, "bench_pairs", IGNORED, "unpaired", f"pair {pair} has one side"
            )
        elif _models(rb) != _models(rc):
            _reason(
                reasons,
                "bench_pairs",
                IGNORED,
                "void_pair",
                f"pair {pair}: models differ ({_models(rb)} vs {_models(rc)})",
            )
        else:
            counted_b.append(rb)
            counted_c.append(rc)
    return counted_b, counted_c


def _values(runs: list[Mapping], scope: str, metric: str) -> list[float]:
    """The metric's values over the complete runs that measured it."""
    found = []
    for run in runs:
        if run.get("status") != COMPLETE:
            continue
        row = (
            run.get("totals")
            if scope == "totals"
            else (run.get("specs") or {}).get(scope)
        )
        value = (row or {}).get(metric)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            found.append(float(value))
    return found


def _compare(
    runs_b: list[Mapping],
    runs_c: list[Mapping],
    scope: str,
    metric: str,
    k: int,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    """B and C medians of one metric, its threshold, and how much C is worse."""
    key, direction, relative = METRICS[metric]
    vb, vc = _values(runs_b, scope, metric), _values(runs_c, scope, metric)
    row: dict[str, Any] = {
        "scope": scope,
        "metric": metric,
        "runs_b": len(vb),
        "runs_c": len(vc),
    }
    if len(vb) < k or len(vc) < k:
        row["outcome"] = UNMEASURED
        row["detail"] = f"{len(vb)} complete runs on B and {len(vc)} on C, {k} needed"
        return row
    mb, mc, spread = median(vb), median(vc), max(vb) - min(vb)
    row.update(baseline_median=mb, candidate_median=mc, baseline_spread=spread)
    if relative:
        if mb == 0:
            row["outcome"] = PASS if mc == 0 else UNMEASURED
            row["detail"] = "B's median is 0; a relative change is undefined"
            row["worse"] = 0.0 if mc == 0 else None
            return row
        worse, spread = (mc - mb) / mb * -direction, spread / mb
    else:
        worse = (mb - mc) * direction
    row["worse"] = round(worse, 6)
    row["threshold"] = max(thresholds[key], spread)
    regressed = worse > row["threshold"] + _EPS
    row["outcome"] = REVERT if regressed else PASS
    row["detail"] = f"C worse by {worse:.4f}, allowed {row['threshold']:.4f}"
    return row


def _bench(
    runs_b: list[Mapping],
    runs_c: list[Mapping],
    metrics: tuple[str, ...],
    k: int,
    thresholds: Mapping[str, float],
    reasons: list[dict],
) -> list[dict]:
    scopes = ["totals"] + sorted(
        {name for run in (*runs_b, *runs_c) for name in (run.get("specs") or {})}
    )
    rows = [
        _compare(runs_b, runs_c, scope, metric, k, thresholds)
        for scope in scopes
        for metric in metrics
    ]
    for row in rows:
        if row["outcome"] != PASS:
            code = "regression" if row["outcome"] == REVERT else "too_few_runs"
            _reason(
                reasons,
                "bench",
                row["outcome"],
                code,
                f"{row['scope']} {row['metric']}: {row['detail']}",
            )
    if all(r["outcome"] == PASS for r in rows):
        _reason(
            reasons, "bench", PASS, "no_regression", f"{len(rows)} medians compared"
        )
    return rows


def _target(
    target: Any,
    b_sha: str,
    c_sha: str,
    runs: tuple[list[Mapping], list[Mapping]],
    k: int,
    thresholds: Mapping[str, float],
    reasons: list[dict],
) -> None:
    """O8: the finding's measure fails on B and passes on C (REQ-15)."""
    guard = "target"
    if not _accepted(target, guard, reasons):
        return
    metric = target.get("metric")
    if metric is not None:
        if metric not in METRICS:
            _reason(reasons, guard, UNMEASURED, "missing", f"unknown metric {metric!r}")
            return
        row = _compare(*runs, "totals", metric, k, thresholds)
        if row["outcome"] == UNMEASURED:
            _reason(reasons, guard, UNMEASURED, "target_not_run", row["detail"])
        elif -(row.get("worse") or 0.0) > row.get("threshold", 0.0) + _EPS:
            _reason(reasons, guard, PASS, "improved", f"{metric}: {row['detail']}")
        else:
            _reason(
                reasons, guard, REVERT, "not_improved", f"{metric}: {row['detail']}"
            )
        return
    sides = {}
    for side, sha in (("baseline", b_sha), ("candidate", c_sha)):
        obs = target.get(side)
        if not isinstance(obs, Mapping) or obs.get("sha") != sha:
            got = obs.get("sha") if isinstance(obs, Mapping) else None
            _reason(
                reasons,
                guard,
                UNMEASURED,
                "wrong_sha",
                f"the {side} result was read at {got!r}, expected {sha}; refused",
            )
            return
        # A result graded outside the sandbox (``run_measure`` with
        # ``--in-process-measure``) ran the candidate's code in the runner's
        # own Python: local development, never a cycle's measurement (PR 12).
        if obs.get("isolation", "sandbox") != "sandbox":
            _reason(
                reasons,
                guard,
                UNMEASURED,
                "not_sandboxed",
                f"the {side} result was graded {obs.get('isolation')!r}, not in the sandbox",
            )
            return
        sides[side] = obs.get("result")
    rb, rc = sides["baseline"], sides["candidate"]
    if rb not in ("pass", "fail") or rc not in ("pass", "fail"):
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "target_not_run",
            f"the measure did not run: B {rb!r}, C {rc!r}",
        )
    elif rb == "pass":
        _reason(
            reasons, guard, REVERT, "already_passed", "the measure already passed on B"
        )
    elif rc == "fail":
        _reason(reasons, guard, REVERT, "not_flipped", "the measure still fails on C")
    else:
        _reason(
            reasons,
            guard,
            PASS,
            "flipped",
            f"{target.get('measure')}: fail on B, pass on C",
        )


def decide(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    graders: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    protected: Mapping[str, Any] | None,
    k: int = K,
    thresholds: Mapping[str, float] | None = None,
    trusted_ci_apps: Iterable[str] = TRUSTED_CI_APPS,
) -> dict[str, Any]:
    """``{decision, reasons, metrics}`` for C against B (module docstring).

    *baseline* and *candidate* each carry ``sha`` and the side's
    observations: ``bench`` (runs), ``ci`` (check-runs and tests collected)
    and ``verifier_bench``; *candidate* also ``fake_bench``.
    """
    limits = {**THRESHOLDS, **(thresholds or {})}
    reasons: list[dict] = []
    b_sha, c_sha = str(baseline.get("sha") or ""), str(candidate.get("sha") or "")
    if not b_sha or not c_sha or b_sha == c_sha:
        _reason(
            reasons, "shas", UNMEASURED, "missing", "B and C need two distinct shas"
        )
    _protected(protected, c_sha, reasons)
    _graders(graders, reasons)
    _ci(baseline, candidate, b_sha, c_sha, reasons, tuple(trusted_ci_apps))
    _verifier(baseline, candidate, b_sha, c_sha, reasons)
    _fake(candidate, c_sha, reasons)
    runs = _counted_runs(baseline, candidate, b_sha, c_sha, reasons)
    _target(target, b_sha, c_sha, runs, k, limits, reasons)
    prompt = isinstance(target, Mapping) and (
        target.get("prompt_targeted") or target.get("metric") == "prompt_tokens_mean"
    )
    metrics = (*GUARDED, "prompt_tokens_mean") if prompt else GUARDED
    rows = _bench(*runs, metrics, k, limits, reasons)
    outcomes = {r["outcome"] for r in reasons}
    if any(r["code"] == "protected_path" for r in reasons):
        decision = REVERT
    elif UNMEASURED in outcomes:
        decision = UNMEASURED
    elif REVERT in outcomes:
        decision = REVERT
    else:
        decision = KEEP
    return {
        "decision": decision,
        "baseline": b_sha,
        "candidate": c_sha,
        "k": k,
        "thresholds": limits,
        "reasons": reasons,
        "metrics": rows,
    }
