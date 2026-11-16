"""The guards of the fitness decision that read one observation each (rsi-loop §2.6).

``fitness.decide`` calls these: the protected-path match, the graders' trees,
CI read by sha with the tests collected, the Verifier bench and the fake
bench. Each appends its reasons and fails closed: an observation that is
missing, malformed, produced by anyone but the platform or read at another
commit is ``unmeasured``, never a pass (rules 6 and 7 of
``docs/POSITIONING.md``). Pure, like ``fitness``; it imports no other context.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

KEEP, REVERT, UNMEASURED, PASS = "keep", "revert", "unmeasured", "pass"
IGNORED = "ignored"  # an input left out of the count, named; never decides
PLATFORM = "platform"
CI_PASSING = ("success", "neutral", "skipped")
CI_FAILING = ("failure", "timed_out", "cancelled", "action_required", "stale")
TRUSTED_CI_APPS = ("github-actions",)
OUTCOMES = ("pass", "fail")


def _reason(
    reasons: list[dict], guard: str, outcome: str, code: str, detail: str
) -> None:
    reasons.append({"guard": guard, "outcome": outcome, "code": code, "detail": detail})


def _accepted(
    obs: Any, guard: str, reasons: list[dict], sha: str | None = None
) -> bool:
    """Whether *obs* is an observation the platform made, at *sha* if given."""
    if not isinstance(obs, Mapping):
        _reason(reasons, guard, UNMEASURED, "missing", f"no {guard} observation")
        return False
    producer = obs.get("produced_by")
    if producer != PLATFORM:
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "not_produced_by_platform",
            f"{guard} was produced by {producer!r}, not by the platform; refused",
        )
        return False
    if sha is not None and obs.get("sha") != sha:
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "wrong_sha",
            f"{guard} was read at {obs.get('sha')!r}, expected {sha}; refused",
        )
        return False
    return True


def _protected(protected: Any, sha: str, reasons: list[dict]) -> None:
    guard = "protected_paths"
    if not _accepted(protected, guard, reasons, sha):
        return
    raw = protected.get("touched")
    # Only an explicit list of {path: <text>} is a match; [] alone means none.
    if not isinstance(raw, list) or not all(
        isinstance(t, Mapping) and isinstance(t.get("path"), str) and t["path"]
        for t in raw
    ):
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "malformed",
            f"touched must be a list of paths, got {raw!r}",
        )
        return
    touched = sorted({t["path"] for t in raw})
    if not touched:
        _reason(reasons, guard, PASS, "none", "C's diff touches no protected path")
        return
    approval = protected.get("approval")
    approved: set[str] = set()
    why = "no approval recorded"
    if isinstance(approval, Mapping):
        if approval.get("produced_by") != PLATFORM:
            why = f"the approval was produced by {approval.get('produced_by')!r}"
        elif approval.get("sha") != sha:
            why = f"the approval names {approval.get('sha')!r}, not C's sha"
        elif not approval.get("by"):
            why = "the approval names no person"
        else:
            approved = {str(p) for p in approval.get("paths") or []}
            why = "the approval does not name every path"
    unapproved = [p for p in touched if p not in approved]
    if unapproved:
        _reason(
            reasons,
            guard,
            REVERT,
            "protected_path",
            f"{why}: {', '.join(unapproved)}",
        )
    else:
        _reason(
            reasons,
            guard,
            PASS,
            "approved",
            f"approved by {approval['by']}: {', '.join(touched)}",
        )


def _graders(graders: Any, reasons: list[dict]) -> None:
    if not _accepted(graders, "graders", reasons):
        return
    b, c = graders.get("baseline_tree"), graders.get("candidate_tree")
    if not b or not c:
        _reason(reasons, "graders", UNMEASURED, "missing", "a graders tree is missing")
    elif b != c:
        _reason(reasons, "graders", UNMEASURED, "graders_differ", f"B's {b} != C's {c}")
    else:
        _reason(reasons, "graders", PASS, "equal", f"graders tree {b} on both sides")


def _latest_runs(
    ci: Mapping, sha: str, side: str, reasons: list[dict], apps: Iterable[str]
) -> dict[str, Mapping] | None:
    """The latest trusted check-run per name, or None when refused or partial."""
    guard = f"ci_{side}"
    if not _accepted(ci, guard, reasons, sha):
        return None
    runs = ci.get("check_runs")
    if not isinstance(runs, list) or ci.get("incomplete"):
        _reason(
            reasons, guard, UNMEASURED, "missing", "the check-run listing is partial"
        )
        return None
    wrong = [r.get("name") for r in runs if r.get("head_sha") != sha]
    if wrong:
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "wrong_sha",
            f"check-runs not on {sha}: {', '.join(map(str, wrong))}; refused",
        )
        return None
    latest: dict[str, Mapping] = {}
    ignored = []
    for run in runs:
        if run.get("app") not in apps:
            ignored.append(f"{run.get('name')} ({run.get('app')})")
            continue
        name = str(run.get("name"))
        if name not in latest or (run.get("id") or 0) > (latest[name].get("id") or 0):
            latest[name] = run
    if ignored:
        _reason(
            reasons, guard, IGNORED, "untrusted_app", f"ignored: {', '.join(ignored)}"
        )
    if not latest:
        _reason(reasons, guard, UNMEASURED, "missing", f"no CI check-run on {sha}")
        return None
    return latest


def _ci(
    b: Mapping, c: Mapping, b_sha: str, c_sha: str, reasons: list[dict], apps
) -> None:
    b_runs = _latest_runs(b.get("ci"), b_sha, "baseline", reasons, apps)
    c_runs = _latest_runs(c.get("ci"), c_sha, "candidate", reasons, apps)
    if b_runs is None or c_runs is None:
        return
    red_b = sorted(
        n for n, r in b_runs.items() if r.get("conclusion") not in CI_PASSING
    )
    if red_b:
        _reason(
            reasons,
            "ci",
            UNMEASURED,
            "baseline_not_green",
            f"B is not green: {', '.join(red_b)}",
        )
    pending = sorted(
        n
        for n, r in c_runs.items()
        if r.get("status") != "completed"
        or r.get("conclusion") not in (*CI_PASSING, *CI_FAILING)
    )
    if pending:
        _reason(
            reasons,
            "ci",
            UNMEASURED,
            "ci_pending",
            f"not concluded: {', '.join(pending)}",
        )
    failed = sorted(
        f"{n}: {r.get('conclusion')}"
        for n, r in c_runs.items()
        if r.get("conclusion") in CI_FAILING
    )
    if failed:
        _reason(reasons, "ci", REVERT, "ci_failed", ", ".join(failed))
    absent = sorted(
        n
        for n, r in b_runs.items()
        if r.get("conclusion") in CI_PASSING and n not in c_runs
    )
    if absent:
        _reason(reasons, "ci", REVERT, "ci_check_missing", ", ".join(absent))
    if not (red_b or pending or failed or absent):
        _reason(reasons, "ci", PASS, "green", f"{len(c_runs)} check-runs concluded")
    _tests_collected(
        b["ci"].get("tests_collected"),
        c["ci"].get("tests_collected"),
        b_sha,
        c_sha,
        reasons,
    )


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _suites(obs: Any, sha: str, side: str, reasons: list[dict]) -> dict | None:
    """One side's tests collected per suite, an observation of its own."""
    guard = f"tests_collected_{side}"
    if not _accepted(obs, guard, reasons, sha):
        return None
    suites = obs.get("suites")
    if (
        not isinstance(suites, Mapping)
        or not suites
        or not all(isinstance(k, str) and _count(n) for k, n in suites.items())
    ):
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "malformed",
            f"suites must map suite names to counts, got {suites!r}",
        )
        return None
    return dict(suites)


def _tests_collected(
    b: Any, c: Any, b_sha: str, c_sha: str, reasons: list[dict]
) -> None:
    sb = _suites(b, b_sha, "baseline", reasons)
    sc = _suites(c, c_sha, "candidate", reasons)
    if sb is None or sc is None:
        return
    guard = "tests_collected"
    fewer = [
        f"{suite}: {n} on B, {sc.get(suite)} on C"
        for suite, n in sorted(sb.items())
        if suite not in sc or sc[suite] < n
    ]
    if fewer:
        _reason(reasons, guard, REVERT, "fewer_tests", "; ".join(fewer))
    else:
        _reason(reasons, guard, PASS, "not_fewer", f"{len(sb)} suites, none fewer")


def _verifier(
    b: Mapping, c: Mapping, b_sha: str, c_sha: str, reasons: list[dict]
) -> None:
    guard = "verifier_bench"
    vb, vc = b.get("verifier_bench"), c.get("verifier_bench")
    ok_b = _accepted(vb, f"{guard}_baseline", reasons, b_sha)
    ok_c = _accepted(vc, f"{guard}_candidate", reasons, c_sha)
    if not (ok_b and ok_c):
        return
    cb, cc = vb.get("cases"), vc.get("cases")
    odd = [
        f"{side} {name}: {result!r}"
        for side, cases in (("B", cb), ("C", cc))
        if isinstance(cases, Mapping)
        for name, result in sorted(cases.items())
        if result not in OUTCOMES
    ]
    if not isinstance(cb, Mapping) or not isinstance(cc, Mapping) or odd:
        _reason(
            reasons,
            guard,
            UNMEASURED,
            "malformed",
            "outcomes must be pass or fail: " + (", ".join(odd) or "no cases object"),
        )
        return
    if not cb:
        _reason(reasons, guard, UNMEASURED, "missing", "B's Verifier bench has no case")
        return
    unknown = sorted(n for n in cb if n not in cc)
    regressed = sorted(n for n, r in cb.items() if r == "pass" and cc.get(n) == "fail")
    if unknown:
        _reason(
            reasons, guard, UNMEASURED, "missing", f"not run on C: {', '.join(unknown)}"
        )
    if regressed:
        _reason(reasons, guard, REVERT, "verifier_regressed", ", ".join(regressed))
    if not (unknown or regressed):
        _reason(reasons, guard, PASS, "no_regression", f"{len(cb)} cases")


def _fake(c: Mapping, c_sha: str, reasons: list[dict]) -> None:
    fake = c.get("fake_bench")
    if not _accepted(fake, "fake_bench", reasons, c_sha):
        return
    passed, total = fake.get("criteria_passed"), fake.get("criteria_total")
    if not isinstance(total, int) or not isinstance(passed, int) or total <= 0:
        _reason(reasons, "fake_bench", UNMEASURED, "missing", "no criteria graded")
    elif passed < total:
        _reason(reasons, "fake_bench", REVERT, "fake_below_full", f"{passed}/{total}")
    else:
        _reason(reasons, "fake_bench", PASS, "full", f"{passed}/{total}")
