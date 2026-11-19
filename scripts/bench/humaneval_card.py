"""HumanEval's block on the scorecard (issue #316, schema v5).

``scorecard.py write --humaneval FILE`` takes the index ``humaneval.py
prepare`` wrote; the problems' entries leave ``specs`` (so the reference
specifications' totals do not move) and become the card's ``humaneval``
block:

- the dataset: its source, the sha256 the bench computed from the bytes it
  checked against the pin, the licence and the number of problems;
- each problem graded: its outcome from HumanEval's own tests, run in the
  bench's sandbox (``pass``; ``fail``; or ``unmeasured`` with the reason
  when the grade could not run, never a pass and never a fail), the sprint's
  status, the tasks, rework, wall-clock, the tokens the provider reported
  and the models served;
- the problems the token budget skipped, listed and never graded;
- pass@1 over the problems graded, one delivery each (null while any
  problem is unmeasured), and the tokens reported.

``compare.py`` prints pass@1 and each problem's outcome before → after,
shown and never judged.
"""

from __future__ import annotations

import re
from typing import Any

VERSION = 5  # the schema version that brought the block
PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"
OUTCOMES = (PASS, FAIL, UNMEASURED)
_ID = re.compile(r"^HumanEval/\d{1,4}$")


def outcome(entry: dict[str, Any]) -> tuple[str, str | None]:
    """A problem's outcome from its graded entry: ``pass`` when REQ-1.1
    passed; ``unmeasured`` (with why) when the grade could not run or the
    test did not; ``fail`` otherwise."""
    criteria = entry.get("criteria") or {}
    result = (criteria.get("results") or {}).get("REQ-1.1")
    if criteria.get("unmeasured"):
        return UNMEASURED, str(criteria["unmeasured"])
    if result == PASS:
        return PASS, None
    if result == "fail":
        return FAIL, None
    return UNMEASURED, "HumanEval's test did not run"


def split(
    entries: list[dict[str, Any]], index: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the reference specifications' entries, the problems' entries)."""
    names = {p["spec"] for p in (index or {}).get("problems") or []}
    return (
        [e for e in entries if e.get("name") not in names],
        [e for e in entries if e.get("name") in names],
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4)


def block(
    entries: list[dict[str, Any]], index: dict[str, Any], skipped: list[str] = ()
) -> dict[str, Any]:
    """The card's ``humaneval`` block from the problems' entries; the
    problems the token budget *skipped* (by spec name) are listed, never graded."""
    by_name = {e["name"]: e for e in entries}
    rows = []
    for problem in index["problems"]:
        entry = by_name.get(problem["spec"])
        if entry is None:
            continue
        found, why = outcome(entry)
        tokens = entry.get("tokens") or {}
        rows.append(
            {
                "task_id": problem["task_id"],
                "spec": problem["spec"],
                "entry_point": problem["entry_point"],
                "status": entry["status"],
                "outcome": found,
                "reason": why,
                "isolation": (entry.get("criteria") or {}).get("isolation"),
                "error": entry.get("error"),
                "tasks": dict(entry.get("tasks") or {}),
                "rework_requests": int(entry.get("rework_requests", 0)),
                "wall_clock_seconds": float(entry.get("wall_clock_seconds", 0.0)),
                "tokens": {
                    k: int(tokens.get(k, 0))
                    for k in ("reported", "turns_reported", "turns_unreported")
                },
                "models": dict(entry.get("models") or {}),
            }
        )
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in OUTCOMES}
    graded = len(rows)
    return {
        "dataset": dict(index["dataset"]),
        "subset": list(index["subset"]),
        "problems": rows,
        "skipped": [p["task_id"] for p in index["problems"] if p["spec"] in skipped],
        "graded": graded,
        "passed": counts[PASS],
        "failed": counts[FAIL],
        "unmeasured": counts[UNMEASURED],
        "pass_at_1": (
            _ratio(counts[PASS], graded) if graded and not counts[UNMEASURED] else None
        ),
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _row_problems(at: str, row: Any, statuses: tuple[str, ...]) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    if not isinstance(row.get("task_id"), str) or not _ID.match(row["task_id"]):
        found.append(f"{at}.task_id must be a HumanEval task id")
    for key in ("spec", "entry_point"):
        if not isinstance(row.get(key), str) or not row.get(key):
            found.append(f"{at}.{key} must be a non-empty string")
    if row.get("outcome") not in OUTCOMES:
        found.append(f"{at}.outcome must be one of {', '.join(OUTCOMES)}")
    if row.get("status") not in statuses:
        found.append(f"{at}.status must be one of {', '.join(statuses)}")
    if row.get("outcome") == UNMEASURED and not row.get("reason"):
        found.append(f"{at}: an unmeasured problem says why (reason)")
    tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
    found += [
        f"{at}.tokens.{k} must be a count"
        for k in ("reported", "turns_reported", "turns_unreported")
        if not _count(tokens.get(k))
    ]
    return found


def problems(card: dict[str, Any], statuses: tuple[str, ...]) -> list[str]:
    """What is wrong with the optional ``humaneval`` block of *card*."""
    if "humaneval" not in card:
        return []
    version = card.get("schema_version")
    found = []
    if not (isinstance(version, int) and version >= VERSION):
        found.append(f"humaneval needs schema v{VERSION}")
    data = card["humaneval"]
    if not isinstance(data, dict):
        return [*found, "humaneval must be an object"]
    dataset = data.get("dataset") if isinstance(data.get("dataset"), dict) else {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(dataset.get("sha256", ""))):
        found.append("humaneval.dataset.sha256 must be a sha256")
    for key in ("name", "source", "license"):
        if not isinstance(dataset.get(key), str) or not dataset.get(key):
            found.append(f"humaneval.dataset.{key} must be a non-empty string")
    rows = data.get("problems")
    if not isinstance(rows, list):
        return [*found, "humaneval.problems must be a list"]
    for index, row in enumerate(rows):
        found += _row_problems(f"humaneval.problems[{index}]", row, statuses)
    if found:
        return found
    for key in ("subset", "skipped"):
        if not isinstance(data.get(key), list):
            found.append(f"humaneval.{key} must be a list")
    ids = [r["task_id"] for r in rows]
    if len(set(ids)) != len(ids):
        found.append("humaneval.problems names a problem twice")
    ran = {s.get("name") for s in card.get("specs") or [] if isinstance(s, dict)}
    found += [
        f"humaneval: {r['spec']} is also a reference specification's entry"
        for r in rows
        if r["spec"] in ran
    ]
    expected = {
        "graded": len(rows),
        "passed": sum(r["outcome"] == PASS for r in rows),
        "failed": sum(r["outcome"] == FAIL for r in rows),
        "unmeasured": sum(r["outcome"] == UNMEASURED for r in rows),
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }
    found += [
        f"humaneval.{k} does not match its problems"
        for k, v in expected.items()
        if data.get(k) != v
    ]
    rate = (
        _ratio(expected["passed"], len(rows))
        if rows and not expected["unmeasured"]
        else None
    )
    if data.get("pass_at_1") != rate:
        found.append(
            "humaneval.pass_at_1 must be passed / graded, or null while a problem is unmeasured"
        )
    return found


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _rate(data: dict[str, Any]) -> str:
    rate = data.get("pass_at_1")
    return "unmeasured" if rate is None else f"{rate * 100:.0f}%"


def render_lines(card: dict[str, Any], level: str = "##") -> list[str]:
    """The card's HumanEval section; nothing without the block."""
    data = card.get("humaneval")
    if not data:
        return []
    d = data["dataset"]
    lines = [
        "",
        f"{level} HumanEval",
        "",
        (
            f"pass@1 {_rate(data)}: {data['passed']} of {data['graded']} problems passed "
            f"HumanEval's own tests ({data['failed']} failed, {data['unmeasured']} unmeasured), "
            f"one delivery each, graded in the bench's sandbox, never by the factory's report. "
            f"Dataset `{d['source']}`, sha256 `{d['sha256']}` (computed by the bench), "
            f"{d['license']} licence. Tokens reported: {data['tokens_reported']}"
            + (
                f" ({data['turns_unreported']} turns without a reported count)."
                if data["turns_unreported"]
                else "."
            )
        ),
        "",
        "| Problem | Entry point | Outcome | Status | Tokens | Wall-clock |",
        "|---|---|---|---|---|---|",
    ]
    for r in data["problems"]:
        shown = r["outcome"] + (f" ({r['reason']})" if r.get("reason") else "")
        lines.append(
            f"| {r['task_id']} | {r['entry_point']} | {shown} | {r['status']} "
            f"| {r['tokens']['reported']} | {r['wall_clock_seconds']:.0f} s |"
        )
    lines += [f"| {t} | – | skipped: budget | – | – | – |" for t in data["skipped"]]
    return lines


def render_compare(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """pass@1 and each problem's outcome, before → after; shown, never judged."""
    before, after = previous.get("humaneval"), current.get("humaneval")
    if not before and not after:
        return []
    if not before:
        return render_lines(current, level="###")
    after = after or {"problems": [], "pass_at_1": None, "skipped": []}
    lines = [
        "",
        "### HumanEval",
        "",
        f"pass@1 {_rate(before)} → {_rate(after)}; shown, never judged.",
        "",
        "| Problem | Before | After |",
        "|---|---|---|",
    ]
    was = {r["task_id"]: r["outcome"] for r in before["problems"]}
    now = {r["task_id"]: r["outcome"] for r in after["problems"]}
    for tid in sorted({*was, *now}, key=lambda t: int(t.split("/")[1])):
        lines.append(f"| {tid} | {was.get(tid, '–')} | {now.get(tid, '–')} |")
    return lines
