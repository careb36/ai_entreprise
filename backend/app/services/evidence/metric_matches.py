"""``metric_matches`` and ``metric_threshold``: metrics against what the platform read.

``docs/design/project-programs-and-screenshots.md`` section 3. A metric the
agent states in its answer (``claimed-metric:`` lines, kept in the run's
claims as ``metrics`` by ``loop/worker/record.py``) is a claim. A metric a
declared program printed and the backend parsed from bytes it hashed
(``workspace__run_program``, ``tools/program_runs.py``) is an observation,
``observed_metrics`` on the call's ``program_run``. The two never share a
field, and this module compares them deterministically:

- ``metric_matches``: each claimed metric against the latest observed metric
  of the same name and split. A difference above the tolerance a person set
  in the program's declaration (``metrics.tolerances[name]``, else
  ``metrics.tolerance``) is ``refuted``. No observed value is
  ``unverifiable``. A match passes only when the observed metric's
  provenance may support a quality claim at the delivery's risk tier
  (``program_metrics.supports_quality``, the owner's decision 2);
  otherwise it is ``unverifiable`` with the reason.
- ``metric_threshold``: each threshold a person declared on a program that
  ran, against the latest observed metric of that name (and split). A metric
  whose provenance cannot support quality at the tier never satisfies it
  (``unverifiable``); outside ``min``/``max`` is ``refuted``.

No check without a claimed metric or a declared threshold: never a pass on
nothing. Pure: reads the claims and the evidence as data.
"""

from __future__ import annotations

from typing import Any

from services.contracts import program_metrics
from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE

CHECK_MATCHES = "metric_matches"
CHECK_THRESHOLD = "metric_threshold"
PASS = "pass"
MAX_LISTED = 20


def program_runs(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every ``program_run`` observation of the run's tool calls, in order."""
    calls = (evidence or {}).get("tool_calls")
    return [
        c["program_run"]
        for c in calls or []
        if isinstance(c, dict) and isinstance(c.get("program_run"), dict)
    ]


def _observed(
    runs: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """``(metric, its run's declared rules)`` for every observed metric."""
    out = []
    for run in runs:
        rules = (
            run.get("metrics_declared")
            if isinstance(run.get("metrics_declared"), dict)
            else {}
        )
        for metric in run.get("observed_metrics") or []:
            if isinstance(metric, dict) and isinstance(
                metric.get("value"), int | float
            ):
                out.append((metric, rules))
    return out


def _latest(observed: list, name: str, split: str | None) -> tuple[dict, dict] | None:
    found = [
        (m, r)
        for m, r in observed
        if m.get("name") == name and (split is None or m.get("split") == split)
    ]
    return found[-1] if found else None


def _aggregate(rows: list[dict[str, Any]]) -> str:
    outcomes = {r["outcome"] for r in rows}
    if VERDICT_REFUTED in outcomes:
        return VERDICT_REFUTED
    return VERDICT_UNVERIFIABLE if VERDICT_UNVERIFIABLE in outcomes else PASS


def _check(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcome = _aggregate(rows)
    shown = [r for r in rows if r["outcome"] == outcome] or rows
    text = "; ".join(r["why"] for r in shown[:5])
    return {
        "name": name,
        "outcome": outcome,
        "observation": text,
        "metrics": rows[:MAX_LISTED],
    }


def _claims(claims: dict[str, Any] | None) -> list[dict[str, Any]]:
    listed = (claims or {}).get("metrics")
    out = []
    for raw in listed if isinstance(listed, list) else []:
        claim, _why = program_metrics.entry(raw)
        if claim is not None:
            out.append(claim)
    return out


def check_matches(
    claims: dict[str, Any] | None, evidence: dict[str, Any] | None, tier: Any
) -> dict[str, Any] | None:
    """``metric_matches`` (module docstring); ``None`` without a claimed metric."""
    claimed = _claims(claims)
    if not claimed:
        return None
    observed = _observed(program_runs(evidence))
    rows = []
    for claim in claimed:
        label = f"{claim['name']} [{claim['split']}] claimed {claim['value']:g}"
        row: dict[str, Any] = {**claim, "claimed": claim["value"]}
        found = _latest(observed, claim["name"], claim["split"])
        if found is None:
            rows.append(
                {
                    **row,
                    "outcome": VERDICT_UNVERIFIABLE,
                    "why": f"{label}: no observed value",
                }
            )
            continue
        metric, rules = found
        tolerance = float(
            (rules.get("tolerances") or {}).get(
                claim["name"], rules.get("tolerance") or 0.0
            )
        )
        precision = rules.get("precision")
        stated = (
            round(claim["value"], precision)
            if isinstance(precision, int)
            else claim["value"]
        )
        difference = abs(stated - float(metric["value"]))
        row.update(
            observed=metric["value"],
            tolerance=tolerance,
            provenance=metric.get("provenance"),
            program=metric.get("program"),
        )
        seen = f"observed {metric['value']:g} ({metric.get('provenance')})"
        if difference > tolerance + 1e-12:
            why = f"{label}, {seen}: off by {difference:g}, above the tolerance {tolerance:g}"
            rows.append({**row, "outcome": VERDICT_REFUTED, "why": why})
            continue
        ok, reason = program_metrics.supports_quality(metric.get("provenance"), tier)
        outcome = PASS if ok else VERDICT_UNVERIFIABLE
        why = f"{label}, {seen}: within {tolerance:g}" + (
            "" if ok else f", but {reason}"
        )
        rows.append({**row, "outcome": outcome, "why": why})
    return _check(CHECK_MATCHES, rows)


def check_thresholds(
    evidence: dict[str, Any] | None, tier: Any
) -> dict[str, Any] | None:
    """``metric_threshold`` (module docstring); ``None`` without a threshold."""
    runs = program_runs(evidence)
    observed = _observed(runs)
    rows = []
    seen: set[tuple[str, str]] = set()
    for run in reversed(runs):  # The latest declaration of each program counts.
        rules = (
            run.get("metrics_declared")
            if isinstance(run.get("metrics_declared"), dict)
            else {}
        )
        for name, rule in (rules.get("thresholds") or {}).items():
            key = (str(run.get("program")), str(name))
            if key in seen or not isinstance(rule, dict):
                continue
            seen.add(key)
            split = rule.get("split")
            label = f"{run.get('program')}: {name}" + (f" [{split}]" if split else "")
            row: dict[str, Any] = {"program": run.get("program"), "name": name, **rule}
            found = _latest(observed, name, split)
            if found is None:
                rows.append(
                    {
                        **row,
                        "outcome": VERDICT_UNVERIFIABLE,
                        "why": f"{label}: no observed value",
                    }
                )
                continue
            metric = found[0]
            value = float(metric["value"])
            row.update(observed=value, provenance=metric.get("provenance"))
            ok, reason = program_metrics.supports_quality(
                metric.get("provenance"), tier
            )
            if not ok:
                rows.append(
                    {
                        **row,
                        "outcome": VERDICT_UNVERIFIABLE,
                        "why": f"{label} = {value:g}: {reason}",
                    }
                )
                continue
            low, high = rule.get("min"), rule.get("max")
            inside = (low is None or value >= low) and (high is None or value <= high)
            bounds = " and ".join(
                t
                for t in (
                    f"at least {low:g}" if low is not None else "",
                    f"at most {high:g}" if high is not None else "",
                )
                if t
            )
            outcome = PASS if inside else VERDICT_REFUTED
            rows.append(
                {
                    **row,
                    "outcome": outcome,
                    "why": f"{label} = {value:g}, needs {bounds}",
                }
            )
    return _check(CHECK_THRESHOLD, rows) if rows else None


def checks(
    claims: dict[str, Any] | None, evidence: dict[str, Any] | None, tier: Any
) -> list[dict[str, Any]]:
    """Both checks, each only when it applies, for the Verifier."""
    return [
        c
        for c in (
            check_matches(claims, evidence, tier),
            check_thresholds(evidence, tier),
        )
        if c
    ]
