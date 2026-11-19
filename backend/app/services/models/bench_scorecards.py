"""The committed bench scorecards, read for the /bench page (issue #685,
split from #316).

``history(root)`` lists every ``*.json`` card in ``<root>/bench/scorecards``
and its ``real/`` and ``ablation/`` folders (an ablation's own arms, under
``ablation/arms/``, are not references and are not read), newest first by
stamp. Each card is checked against the schema (``bench_schema.validate``, a
copy of ``scripts/bench/schema.py``) and listed with its status:

- ``valid``: its numbers are shown, exactly as the card holds them;
- ``invalid``: the schema's problems are listed and no number is shown;
- ``unreadable``: the file could not be read or is not JSON (a symbolic link,
  a file over ``MAX_CARD_BYTES``, broken JSON), with the reason.

A card is never dropped. Every number is what the bench's scorer observed;
nothing here asks a model or reads the database. Where the card itself says a
metric was not measured, the entry says so (``{"measured": false,
"reason": ...}``) and never turns it into a 0:

- the criteria, when the grade could not run (``criteria.unmeasured``, the
  card's own reason) or the spec has no criterion;
- the reported tokens, when no turn reported usage (``turns_reported`` 0;
  the turns that did not report are counted apart either way);
- the cost, unless every routing row carries a ``cost_cents`` (the bench
  prices no turn today, so it is never measured yet), or before schema v2;
- the prompt weight, when no prompt was measured, or before schema v3;
- in v5's ``humaneval`` section, a problem's outcome when its grade could not
  run (the card's own reason), and pass@1 while any problem is unmeasured
  (the card writes null) or when no problem was graded.

The totals' criteria are unmeasured when any spec's grade is; the totals'
tokens when no spec reported any. The other numbers (tasks, rework, blocked
share, first attempts, wall-clock) are the board's and the runner's and are
always present in a valid card.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.models import bench_schema

CARDS_DIR = Path("bench") / "scorecards"
FOLDERS = ("", "real", "ablation")
MAX_CARD_BYTES = 2 * 1024 * 1024
VALID, INVALID, UNREADABLE = "valid", "invalid", "unreadable"
GRADE_UNMEASURED = "grade_unmeasured"
NO_CRITERIA = "no_criteria"
NO_REPORTED_TURNS = "no_reported_turns"
COST_UNKNOWN = "cost_unknown"
NOT_IN_CARD = "not_in_card"
NO_PROMPT_MEASURE = "no_prompt_measure"
NO_PROBLEMS_GRADED = "no_problems_graded"
_SPEC_COPIED = (
    "name",
    "status",
    "error",
    "tasks",
    "rework_requests",
    "rework_per_task",
    "blocked_share",
    "first_attempt_rate",
    "wall_clock_seconds",
    "models",
    "factory_criteria_verified",
)
_TOTALS_COPIED = (
    "specs",
    "specs_completed",
    "tasks",
    "done_first_attempt",
    "first_attempt_rate",
    "rework_per_task",
    "blocked_share",
    "wall_clock_seconds",
)
_PROMPT_COPIED = (
    "prompts",
    "prompt_sections_missing",
    "tokens_estimated",
    "tokens_mean",
    "system_tokens_estimated",
    "cut_prompts",
    "related_code_prompts",
)


def measured(value: Any) -> dict[str, Any]:
    return {"measured": True, "value": value}


def unmeasured(reason: str, detail: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"measured": False, "reason": reason}
    if detail is not None:
        entry["detail"] = detail
    return entry


def _criteria(criteria: dict) -> dict[str, Any]:
    reason = criteria.get("unmeasured")
    if reason:
        return unmeasured(GRADE_UNMEASURED, str(reason))
    total = criteria["total"]
    if total == 0:
        return unmeasured(NO_CRITERIA)
    return measured(
        {
            "passed": criteria["passed"],
            "total": total,
            "rate": round(criteria["passed"] / total, 4),
        }
    )


def _tokens(reported: int, turns_reported: int, turns_unreported: int) -> dict:
    entry = measured(reported) if turns_reported > 0 else unmeasured(NO_REPORTED_TURNS)
    return {
        **entry,
        "turns_reported": turns_reported,
        "turns_unreported": turns_unreported,
    }


def _cost(spec: dict, version: int) -> dict[str, Any]:
    rows = spec.get("routing")
    if version < 2 or not rows:
        return unmeasured(NOT_IN_CARD)
    unknown = sum(
        1
        for r in rows
        if not bench_schema.number(r.get("cost_cents")) or r.get("cost_unknown", 0) > 0
    )
    if unknown:
        return unmeasured(COST_UNKNOWN, unknown)
    return measured(round(sum(r["cost_cents"] for r in rows), 4))


def _prompt(prompt: Any, version: int) -> dict[str, Any]:
    if version < 3 or not isinstance(prompt, dict):
        return unmeasured(NOT_IN_CARD)
    if prompt["prompts"] == 0:
        return unmeasured(NO_PROMPT_MEASURE, prompt["prompt_sections_missing"])
    return measured({k: prompt[k] for k in _PROMPT_COPIED})


def _sections(prompt: Any, version: int) -> list[dict[str, Any]] | None:
    """The totals' prompt sections by weight, heaviest first; None when the
    card has no prompt weight or measured no prompt."""
    if version < 3 or not isinstance(prompt, dict) or prompt["prompts"] == 0:
        return None
    rows = [
        {
            "section": name,
            "tokens": row["tokens"],
            "prompts": row["prompts"],
            "share": row["share"],
            "cut": prompt["cut"].get(name, 0),
        }
        for name, row in prompt["sections"].items()
    ]
    rows.extend(
        {"section": name, "tokens": 0, "prompts": 0, "share": 0, "cut": cut}
        for name, cut in prompt["cut"].items()
        if name not in prompt["sections"]
    )
    return sorted(rows, key=lambda r: (-r["tokens"], r["section"]))


def _spec(spec: dict, version: int) -> dict[str, Any]:
    criteria = spec["criteria"]
    tokens = spec["tokens"]
    return {
        **{k: spec.get(k) for k in _SPEC_COPIED},
        "criteria": _criteria(criteria),
        "results": dict(criteria["results"]),
        "isolation": criteria.get("isolation"),
        "tokens": _tokens(
            tokens["reported"], tokens["turns_reported"], tokens["turns_unreported"]
        ),
        "cost_cents": _cost(spec, version),
        "prompt": _prompt(spec.get("prompt"), version),
    }


def _totals(card: dict, version: int) -> dict[str, Any]:
    totals = card["totals"]
    specs = card["specs"]
    ungraded = [s["name"] for s in specs if s["criteria"].get("unmeasured")]
    if ungraded:
        criteria = unmeasured(GRADE_UNMEASURED, ungraded)
    elif totals["criteria_total"] == 0:
        criteria = unmeasured(NO_CRITERIA)
    else:
        criteria = measured(
            {
                "passed": totals["criteria_passed"],
                "total": totals["criteria_total"],
                "rate": totals["criteria_rate"],
            }
        )
    return {
        **{k: totals[k] for k in _TOTALS_COPIED},
        "criteria": criteria,
        "tokens": _tokens(
            totals["tokens_reported"],
            sum(s["tokens"]["turns_reported"] for s in specs),
            sum(s["tokens"]["turns_unreported"] for s in specs),
        ),
        "prompt": _prompt(totals.get("prompt"), version),
    }


def _problem(row: dict) -> dict[str, Any]:
    tokens = row["tokens"]
    outcome = row["outcome"]
    return {
        "task_id": row["task_id"],
        "entry_point": row["entry_point"],
        "status": row["status"],
        "outcome": (
            unmeasured(GRADE_UNMEASURED, str(row["reason"]))
            if outcome == "unmeasured"
            else measured(outcome)
        ),
        # Not in the schema's row check: shown only when it is a finite number.
        "wall_clock_seconds": (
            row["wall_clock_seconds"]
            if bench_schema.number(row.get("wall_clock_seconds"))
            else None
        ),
        "tokens": _tokens(
            tokens["reported"], tokens["turns_reported"], tokens["turns_unreported"]
        ),
    }


def _humaneval(data: Any) -> dict[str, Any] | None:
    """v5's HumanEval section: pass@1 over the problems graded and each
    problem's outcome from HumanEval's own tests; None without the block."""
    if not isinstance(data, dict):
        return None
    rows = data["problems"]
    ungraded = [r["task_id"] for r in rows if r["outcome"] == "unmeasured"]
    if ungraded:
        rate = unmeasured(GRADE_UNMEASURED, ungraded)
    elif not rows:
        rate = unmeasured(NO_PROBLEMS_GRADED)
    else:
        rate = measured(data["pass_at_1"])
    dataset = data["dataset"]
    return {
        "dataset": {k: dataset[k] for k in ("name", "source", "sha256", "license")},
        "graded": data["graded"],
        "passed": data["passed"],
        "failed": data["failed"],
        "unmeasured": data["unmeasured"],
        "pass_at_1": rate,
        "tokens": _tokens(
            data["tokens_reported"],
            sum(r["tokens"]["turns_reported"] for r in rows),
            data["turns_unreported"],
        ),
        "problems": [_problem(r) for r in rows],
        "skipped": list(data["skipped"]),
    }


def summarize(card: dict) -> dict[str, Any]:
    """A valid card's page entry: its header, totals and specs."""
    version = card["schema_version"]
    run = card.get("run")
    graders = card.get("graders")
    return {
        "stamp": card["stamp"],
        "schema_version": version,
        "commit": card["commit"],
        "llm": card["llm"],
        "policy": card.get("policy"),
        "run": (
            {
                "status": run["status"],
                "budget": run["budget"],
                "tokens_spent": run["tokens_spent"],
                "skipped": [s["name"] for s in run["skipped"]],
            }
            if isinstance(run, dict)
            else None
        ),
        "graders_sha": graders["sha"] if isinstance(graders, dict) else None,
        "sections_off": list(card.get("sections_off") or []),
        "has_ablation": "ablation" in card,
        "totals": _totals(card, version),
        "specs": [_spec(s, version) for s in card["specs"]],
        "prompt_sections": _sections(card["totals"].get("prompt"), version),
        "humaneval": _humaneval(card.get("humaneval")),
    }


def _read(path: Path) -> tuple[Any, str | None]:
    """The card's JSON, or None and why it could not be read."""
    if path.is_symlink():
        return None, "a symbolic link is not read"
    try:
        size = path.stat().st_size
        if size > MAX_CARD_BYTES:
            return None, f"larger than {MAX_CARD_BYTES} bytes"
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not be read: {type(exc).__name__}"
    except json.JSONDecodeError as exc:
        return None, f"not JSON: {exc.msg} at line {exc.lineno}"


def entry(path: Path, folder: str) -> dict[str, Any]:
    """One card's entry in the history, whatever state it is in."""
    base = {"file": path.name, "folder": folder, "stamp": path.stem}
    card, why = _read(path)
    if why is not None:
        return {**base, "status": UNREADABLE, "problems": [why]}
    problems = bench_schema.validate(card)
    if problems:
        return {**base, "status": INVALID, "problems": problems}
    return {**base, "status": VALID, "problems": [], **summarize(card)}


def history(root: Path) -> dict[str, Any]:
    """Every committed card under *root*'s ``bench/scorecards``, newest first;
    ``available`` is false when the folder is not there."""
    cards_dir = root / CARDS_DIR
    if not cards_dir.is_dir():
        return {"available": False, "cards": []}
    entries = []
    for folder in FOLDERS:
        where = cards_dir / folder if folder else cards_dir
        if not where.is_dir() or where.is_symlink():
            continue
        entries.extend(entry(p, folder) for p in sorted(where.glob("*.json")))
    entries.sort(key=lambda e: (e["stamp"], e["folder"], e["file"]), reverse=True)
    return {"available": True, "cards": entries}
