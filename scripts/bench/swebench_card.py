"""SWE-bench Lite's block on the scorecard (issue #687, schema v6).

``scorecard.py write --swebench FILE`` takes the index ``swebench.py
prepare`` wrote; the instances' entries leave ``specs`` (so the reference
specifications' totals do not move) and become the card's ``swebench``
block:

- the dataset: its name, the pinned revision, the split, where the rows were
  read, the licence note and each instance's pinned sha256;
- each instance graded: its outcome from its own tests, run by the scorer
  (``swebench_grade.py``): ``pass`` (resolved: every FAIL_TO_PASS and
  PASS_TO_PASS test passed), ``fail``, or ``unmeasured`` with the reason
  when its environment could not be built or did not reproduce it (never a
  pass and never a fail); the FAIL_TO_PASS and PASS_TO_PASS counts, the
  sprint's status, tasks, rework, wall-clock, reported tokens and models;
- the instances the token budget skipped, listed and never graded;
- the resolve rate over the instances measured (null when none was), beside
  how many were measured, and the tokens reported.

An unmeasured instance is left out of the rate because the grade checks the
environment on the base before the delivery (``swebench_grade.py``): what
makes an instance unmeasured happened before the factory's work was read.

``compare.py`` prints the rate and each instance's outcome before → after,
shown and never judged.
"""

from __future__ import annotations

import re
from typing import Any

VERSION = 6  # the schema version that brought the block
PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"
OUTCOMES = (PASS, FAIL, UNMEASURED)
_ID = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d{1,6}$")
_TOKENS = ("reported", "turns_reported", "turns_unreported")


def outcome(entry: dict[str, Any]) -> tuple[str, str | None]:
    """An instance's outcome from its graded entry: ``unmeasured`` (with
    why) when the grade could not measure it, ``pass`` when REQ-1.1 (the
    instance resolved) passed, ``fail`` otherwise, with the grade's reason."""
    criteria = entry.get("criteria") or {}
    result = (criteria.get("results") or {}).get("REQ-1.1")
    reason = (entry.get("swebench") or {}).get("reason")
    if criteria.get("unmeasured"):
        return UNMEASURED, str(criteria["unmeasured"])
    if result == PASS:
        return PASS, None
    if result == FAIL:
        return FAIL, reason
    return UNMEASURED, "the instance's tests did not run"


def split(
    entries: list[dict[str, Any]], index: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the other entries, the instances' entries)."""
    names = {i["spec"] for i in (index or {}).get("instances") or []}
    return (
        [e for e in entries if e.get("name") not in names],
        [e for e in entries if e.get("name") in names],
    )


def _counts(group: Any) -> dict[str, int] | None:
    if not isinstance(group, dict):
        return None
    return {"passed": int(group.get("passed", 0)), "total": int(group.get("total", 0))}


def _rate(resolved: int, measured: int) -> float | None:
    return round(resolved / measured, 4) if measured else None


def block(
    entries: list[dict[str, Any]], index: dict[str, Any], skipped: list[str] = ()
) -> dict[str, Any]:
    """The card's ``swebench`` block from the instances' entries; the
    instances the token budget *skipped* (by spec name) are listed, never graded."""
    by_name = {e["name"]: e for e in entries}
    rows = []
    for instance in index["instances"]:
        entry = by_name.get(instance["spec"])
        if entry is None:
            continue
        found, why = outcome(entry)
        tokens = entry.get("tokens") or {}
        detail = entry.get("swebench") or {}
        rows.append(
            {
                "instance_id": instance["instance_id"],
                "repo": instance["repo"],
                "spec": instance["spec"],
                "status": entry["status"],
                "outcome": found,
                "reason": why,
                "isolation": (entry.get("criteria") or {}).get("isolation"),
                "error": entry.get("error"),
                "fail_to_pass": _counts(detail.get("fail_to_pass")),
                "pass_to_pass": _counts(detail.get("pass_to_pass")),
                "grade_seconds": float(detail.get("seconds", 0.0)),
                "tasks": dict(entry.get("tasks") or {}),
                "rework_requests": int(entry.get("rework_requests", 0)),
                "wall_clock_seconds": float(entry.get("wall_clock_seconds", 0.0)),
                "tokens": {k: int(tokens.get(k, 0)) for k in _TOKENS},
                "models": dict(entry.get("models") or {}),
            }
        )
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in OUTCOMES}
    measured = counts[PASS] + counts[FAIL]
    return {
        "dataset": dict(index["dataset"]),
        "subset": list(index["subset"]),
        "instances": rows,
        "skipped": [
            i["instance_id"] for i in index["instances"] if i["spec"] in skipped
        ],
        "graded": len(rows),
        "measured": measured,
        "resolved": counts[PASS],
        "failed": counts[FAIL],
        "unmeasured": counts[UNMEASURED],
        "resolved_rate": _rate(counts[PASS], measured),
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _row_problems(at: str, row: Any, statuses: tuple[str, ...]) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    if not isinstance(row.get("instance_id"), str) or not _ID.match(row["instance_id"]):
        found.append(f"{at}.instance_id must be a SWE-bench instance id")
    for key in ("spec", "repo"):
        if not isinstance(row.get(key), str) or not row.get(key):
            found.append(f"{at}.{key} must be a non-empty string")
    if row.get("outcome") not in OUTCOMES:
        found.append(f"{at}.outcome must be one of {', '.join(OUTCOMES)}")
    if row.get("status") not in statuses:
        found.append(f"{at}.status must be one of {', '.join(statuses)}")
    if row.get("outcome") == UNMEASURED and not row.get("reason"):
        found.append(f"{at}: an unmeasured instance says why (reason)")
    for key in ("fail_to_pass", "pass_to_pass"):
        group = row.get(key)
        if group is not None and not (
            isinstance(group, dict)
            and _count(group.get("passed"))
            and _count(group.get("total"))
            and group["passed"] <= group["total"]
        ):
            found.append(f"{at}.{key} must be null or passed <= total counts")
    if row.get("outcome") == PASS and not all(
        isinstance(row.get(k), dict) and row[k].get("passed") == row[k].get("total")
        for k in ("fail_to_pass", "pass_to_pass")
    ):
        found.append(
            f"{at}: a resolved instance passed every FAIL_TO_PASS and PASS_TO_PASS test"
        )
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
    found += [
        f"{at}.tokens.{k} must be a count" for k in _TOKENS if not _count(tokens.get(k))
    ]
    return found


def problems(card: dict[str, Any], statuses: tuple[str, ...]) -> list[str]:
    """What is wrong with the optional ``swebench`` block of *card*."""
    if "swebench" not in card:
        return []
    version = card.get("schema_version")
    found = []
    if not (isinstance(version, int) and version >= VERSION):
        found.append(f"swebench needs schema v{VERSION}")
    data = card["swebench"]
    if not isinstance(data, dict):
        return [*found, "swebench must be an object"]
    dataset = data.get("dataset") if isinstance(data.get("dataset"), dict) else {}
    if not re.fullmatch(r"[0-9a-f]{40}", str(dataset.get("revision", ""))):
        found.append("swebench.dataset.revision must be a commit sha")
    for key in ("name", "source", "license", "split"):
        if not isinstance(dataset.get(key), str) or not dataset.get(key):
            found.append(f"swebench.dataset.{key} must be a non-empty string")
    pins = dataset.get("instances")
    if not isinstance(pins, dict) or not all(
        re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in pins.values()
    ):
        found.append("swebench.dataset.instances must map each id to its pinned sha256")
    rows = data.get("instances")
    if not isinstance(rows, list):
        return [*found, "swebench.instances must be a list"]
    for index, row in enumerate(rows):
        found += _row_problems(f"swebench.instances[{index}]", row, statuses)
    if found:
        return found
    for key in ("subset", "skipped"):
        if not isinstance(data.get(key), list):
            found.append(f"swebench.{key} must be a list")
    ids = [r["instance_id"] for r in rows]
    if len(set(ids)) != len(ids):
        found.append("swebench.instances names an instance twice")
    ran = {s.get("name") for s in card.get("specs") or [] if isinstance(s, dict)}
    found += [
        f"swebench: {r['spec']} is also a reference specification's entry"
        for r in rows
        if r["spec"] in ran
    ]
    resolved = sum(r["outcome"] == PASS for r in rows)
    failed = sum(r["outcome"] == FAIL for r in rows)
    expected = {
        "graded": len(rows),
        "measured": resolved + failed,
        "resolved": resolved,
        "failed": failed,
        "unmeasured": sum(r["outcome"] == UNMEASURED for r in rows),
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }
    found += [
        f"swebench.{k} does not match its instances"
        for k, v in expected.items()
        if data.get(k) != v
    ]
    if data.get("resolved_rate") != _rate(resolved, resolved + failed):
        found.append(
            "swebench.resolved_rate must be resolved / measured, or null when none was measured"
        )
    return found


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _shown_rate(data: dict[str, Any]) -> str:
    rate = data.get("resolved_rate")
    return "unmeasured" if rate is None else f"{rate * 100:.0f}%"


def _tests(group: dict[str, int] | None) -> str:
    return "–" if not group else f"{group['passed']}/{group['total']}"


def render_lines(card: dict[str, Any], level: str = "##") -> list[str]:
    """The card's SWE-bench section; nothing without the block."""
    data = card.get("swebench")
    if not data:
        return []
    d = data["dataset"]
    lines = [
        "",
        f"{level} SWE-bench Lite",
        "",
        (
            f"Resolved {_shown_rate(data)}: {data['resolved']} of {data['measured']} instances "
            f"measured ({data['failed']} failed; {data['unmeasured']} unmeasured, left out), "
            "graded by their own FAIL_TO_PASS and PASS_TO_PASS tests run by the scorer, "
            f"never by the factory's report. Dataset `{d['name']}` at revision `{d['revision']}` "
            f"({d['split']}), each instance checked against its pinned sha256. "
            f"Tokens reported: {data['tokens_reported']}"
            + (
                f" ({data['turns_unreported']} turns without a reported count)."
                if data["turns_unreported"]
                else "."
            )
        ),
        "",
        "| Instance | Outcome | FAIL_TO_PASS | PASS_TO_PASS | Status | Tokens | Wall-clock |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in data["instances"]:
        shown = r["outcome"] + (f" ({r['reason']})" if r.get("reason") else "")
        lines.append(
            f"| {r['instance_id']} | {shown} | {_tests(r['fail_to_pass'])} "
            f"| {_tests(r['pass_to_pass'])} | {r['status']} | {r['tokens']['reported']} "
            f"| {r['wall_clock_seconds']:.0f} s |"
        )
    lines += [f"| {i} | skipped: budget | – | – | – | – | – |" for i in data["skipped"]]
    return lines


def render_compare(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """The resolve rate and each instance's outcome, before → after; shown,
    never judged."""
    before, after = previous.get("swebench"), current.get("swebench")
    if not before and not after:
        return []
    if not before:
        return render_lines(current, level="###")
    after = after or {"instances": [], "resolved_rate": None, "measured": 0}
    lines = [
        "",
        "### SWE-bench Lite",
        "",
        (
            f"Resolved {_shown_rate(before)} → {_shown_rate(after)} "
            f"(measured {before.get('measured', 0)} → {after.get('measured', 0)}); "
            "shown, never judged."
        ),
        "",
        "| Instance | Before | After |",
        "|---|---|---|",
    ]
    was = {r["instance_id"]: r["outcome"] for r in before["instances"]}
    now = {r["instance_id"]: r["outcome"] for r in after["instances"]}
    for iid in sorted({*was, *now}):
        lines.append(f"| {iid} | {was.get(iid, '–')} | {now.get(iid, '–')} |")
    return lines
