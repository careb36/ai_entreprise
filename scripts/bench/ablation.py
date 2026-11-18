#!/usr/bin/env python3
"""What each prompt section is worth: the bench run again with it off (schema v4).

Issues #443 and #438. The backend leaves the optional sections named in
``PROMPT_SECTIONS_OFF`` out of every Developer task prompt (PR #648,
``backend/app/services/loop/worker/prompt_budget.py``). ``ablate.sh`` runs
the specifications once as they are (the baseline) and once per section with
that section off (an arm, ``run.sh`` with ``BENCH_SECTIONS_OFF``), and this
module writes, onto the baseline's card, each arm's numbers beside the
baseline's and the difference, arm less baseline:

- criteria passed, graded by the acceptance tests a person wrote (never the
  factory's own verified criteria); unmeasured when the grade could not run
  on either side, never a drop to zero;
- rework requests on the board, in all and per task;
- the prompt tokens the backend estimated it sent (characters / 4), in all
  and per prompt, and the tokens the provider reported, with the turns whose
  usage was not reported counted apart.

Only the specifications both cards ran in full are compared (one stopped by
the token budget, or on one side only, is left out and the arm says so).
Each arm also records whether the section really was off (``applied``: the
baseline sent it and the arm sent none of it) and whether the same models
served both runs. The numbers are shown, never judged: a single run per arm
is one sample, and choosing budgets or turning a section off is the owner's
decision (#443), not this module's.

Subcommands, used by ``ablate.sh``:

- ``parse --request R`` / ``parse --names R`` check a request (``each``, or
  known optional names, comma-separated) or one run's names, exit 2 on an
  unknown or required name (the backend would ignore it, and the arm would
  be measured as if it had turned something off);
- ``plan --request R --baseline CARD`` prints the sections to run, one per
  line: the names given, or with ``each`` the optional sections the
  baseline's prompts carried (estimated tokens above zero);
- ``write --baseline CARD --request R --arm SECTION=CARD... --out-dir DIR``
  writes the baseline's card with the ``ablation`` block as
  ``<stamp>.json`` and ``<stamp>.md``.

The section names are read from the backend's ``prompt_budget.py`` without
importing it (its ``SECTION_NAMES`` and ``REQUIRED_NAMES``), so the bench and
the backend under test cannot drift apart.
"""

import argparse
import ast
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # the sibling modules, by path
import graders
import repeated

PROMPT_BUDGET = (
    Path(__file__).resolve().parents[2]
    / "backend/app/services/loop/worker/prompt_budget.py"
)
EACH = "each"
NO_WEIGHT = "no weight in the baseline"
ABLATION_VERSION = 4  # the schema version that brought the block
SIDE_COUNTS = (
    "criteria_passed",
    "criteria_total",
    "tasks",
    "rework_requests",
    "prompts",
    "prompt_tokens",
    "tokens_reported",
    "turns_unreported",
    "section_tokens",
)
SIDE_RATES = ("rework_per_task", "prompt_tokens_mean")
DELTA_FIELDS = (
    "criteria_passed",
    "rework_requests",
    "rework_per_task",
    "prompt_tokens",
    "prompt_tokens_mean",
    "tokens_reported",
)
_EXACT = ("criteria_passed", "rework_requests", "prompt_tokens", "tokens_reported")


# ---------------------------------------------------------------------------
# The names
# ---------------------------------------------------------------------------


def known_sections(path: Path = PROMPT_BUDGET) -> tuple[tuple[str, ...], frozenset]:
    """The backend's section names in prompt order, and the required ones."""
    found: dict[str, Any] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name == "SECTION_NAMES":
                found[name] = tuple(ast.literal_eval(node.value))
            elif name == "REQUIRED_NAMES" and isinstance(node.value, ast.Call):
                found[name] = frozenset(ast.literal_eval(node.value.args[0]))
    if set(found) != {"SECTION_NAMES", "REQUIRED_NAMES"}:
        raise ValueError(f"{path} names no SECTION_NAMES and REQUIRED_NAMES")
    return found["SECTION_NAMES"], found["REQUIRED_NAMES"]


def optional_sections(path: Path = PROMPT_BUDGET) -> tuple[str, ...]:
    names, required = known_sections(path)
    return tuple(n for n in names if n not in required)


def parse_names(text: str) -> list[str]:
    """One run's sections off, in prompt order; ValueError naming what is wrong."""
    optional = optional_sections()
    names = [n.strip().lower() for n in str(text or "").split(",")]
    names = list(dict.fromkeys(n for n in names if n))
    if not names:
        raise ValueError("no section named")
    if EACH in names:
        raise ValueError(f"'{EACH}' is a request for ablate.sh, not one run's names")
    wrong = [n for n in names if n not in optional]
    if wrong:
        required = set(known_sections()[1])
        kind = "required (never off)" if set(wrong) <= required else "unknown"
        raise ValueError(
            f"{kind} prompt sections: {', '.join(wrong)}. "
            f"The optional sections: {', '.join(optional)}."
        )
    return [n for n in optional if n in names]


def parse_request(text: str) -> str | list[str]:
    """``each``, or the named sections in prompt order."""
    if str(text or "").strip().lower() == EACH:
        return EACH
    return parse_names(text)


def _request_label(request: str | list[str]) -> str:
    return request if request == EACH else ",".join(request)


def weighted(card: dict[str, Any]) -> list[str]:
    """The optional sections the card's prompts carried, in prompt order."""
    sections = ((card.get("totals") or {}).get("prompt") or {}).get("sections") or {}
    return [n for n in optional_sections() if (sections.get(n) or {}).get("tokens")]


def plan(
    request: str | list[str], baseline: dict[str, Any]
) -> tuple[list[str], list[dict[str, str]]]:
    """The sections to run, and those ``each`` leaves out with the reason."""
    if request != EACH:
        return list(request), []
    run = weighted(baseline)
    return run, [
        {"section": n, "reason": NO_WEIGHT} for n in optional_sections() if n not in run
    ]


# ---------------------------------------------------------------------------
# The deltas
# ---------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def side(card: dict[str, Any], specs: list[str], section: str) -> dict[str, Any]:
    """One card's numbers over *specs*, and the tokens *section* took in it."""
    rows = [s for s in card["specs"] if s["name"] in specs]
    prompts = [s.get("prompt") or {} for s in rows]
    out = {
        "criteria_passed": sum(s["criteria"]["passed"] for s in rows),
        "criteria_total": sum(s["criteria"]["total"] for s in rows),
        "tasks": sum(s["tasks"]["total"] for s in rows),
        "rework_requests": sum(s["rework_requests"] for s in rows),
        "prompts": sum(p.get("prompts", 0) for p in prompts),
        "prompt_tokens": sum(p.get("tokens_estimated", 0) for p in prompts),
        "tokens_reported": sum(s["tokens"]["reported"] for s in rows),
        "turns_unreported": sum(s["tokens"]["turns_unreported"] for s in rows),
        "section_tokens": sum(
            ((p.get("sections") or {}).get(section) or {}).get("tokens", 0)
            for p in prompts
        ),
    }
    out["rework_per_task"] = _ratio(out["rework_requests"], out["tasks"])
    out["prompt_tokens_mean"] = (
        round(out["prompt_tokens"] / out["prompts"], 1) if out["prompts"] else None
    )
    return out


def _delta(before: dict[str, Any], after: dict[str, Any], graded: bool) -> dict:
    out: dict[str, Any] = {}
    for key in DELTA_FIELDS:
        b, a = before[key], after[key]
        if b is None or a is None or (key == "criteria_passed" and not graded):
            out[key] = None
        elif key == "rework_per_task":
            out[key] = round(a - b, 4)
        elif key == "prompt_tokens_mean":
            out[key] = round(a - b, 1)
        else:
            out[key] = a - b
    return out


def _in_full(card: dict[str, Any]) -> set[str]:
    return {s["name"] for s in card["specs"] if s["status"] != budget.BUDGET_EXCEEDED}


def arm_record(
    baseline: dict[str, Any], arm: dict[str, Any], section: str
) -> dict[str, Any]:
    """One arm: its numbers and the baseline's over the specs both ran in full."""
    specs = sorted(_in_full(baseline) & _in_full(arm))
    unmeasured = {
        s["name"]
        for card in (baseline, arm)
        for s in card["specs"]
        if s["name"] in specs and repeated.grade_unavailable(s)
    }
    before, after = side(baseline, specs, section), side(arm, specs, section)
    return {
        "section": section,
        "stamp": arm["stamp"],
        "status": budget.BUDGET_EXCEEDED if budget.is_cut(arm) else budget.COMPLETE,
        "specs": specs,
        "criteria_unmeasured": sorted(unmeasured),
        "applied": bool(
            before["section_tokens"]
            and after["prompts"]
            and not after["section_tokens"]
        ),
        "same_models": repeated.served_models(baseline) == repeated.served_models(arm),
        "baseline": before,
        "arm": after,
        "delta": _delta(before, after, graded=not unmeasured),
    }


def _belongs(baseline: dict[str, Any], arm: dict[str, Any], section: str) -> None:
    off = arm.get("sections_off") or []
    if off != [section]:
        raise ValueError(
            f"the card for {section} ran with sections off: {', '.join(off) or 'none'}"
        )
    for key in ("commit", "llm", "policy"):
        if baseline.get(key) != arm.get(key):
            raise ValueError(
                f"the card for {section} differs from the baseline's {key}: "
                f"{arm.get(key)} against {baseline.get(key)}"
            )
    if graders.label(baseline) != graders.label(arm):
        raise ValueError(f"the card for {section} was graded by other graders")


def build(
    baseline: dict[str, Any],
    arms: dict[str, dict[str, Any]],
    request: str | list[str],
) -> dict[str, Any]:
    """The ``ablation`` block; ValueError when the cards do not belong together."""
    if baseline.get("sections_off"):
        raise ValueError(
            "the baseline ran with sections off: " + ", ".join(baseline["sections_off"])
        )
    run, not_run = plan(request, baseline)
    missing, extra = (
        [n for n in run if n not in arms],
        [n for n in arms if n not in run],
    )
    if missing or extra:
        raise ValueError(
            "the arms do not match the plan: "
            + "; ".join(
                part
                for part in (
                    missing and "no card for " + ", ".join(missing),
                    extra and "not planned: " + ", ".join(extra),
                )
                if part
            )
        )
    rows = []
    for section in run:
        _belongs(baseline, arms[section], section)
        rows.append(arm_record(baseline, arms[section], section))
    return {
        "request": _request_label(request),
        "baseline": baseline["stamp"],
        "arms": rows,
        "not_run": not_run,
    }


def attach(baseline: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    """A copy of the baseline's card carrying *block*."""
    return {**copy.deepcopy(baseline), "ablation": block}


# ---------------------------------------------------------------------------
# The schema (called by ``schema.validate``)
# ---------------------------------------------------------------------------


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _names(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(n, str) and n for n in value)
        and len(set(value)) == len(value)
    )


def _arm_problems(at: str, row: Any) -> list[str]:
    if not isinstance(row, dict):
        return [f"{at} is not an object"]
    found = []
    for key in ("section", "stamp"):
        if not isinstance(row.get(key), str) or not row.get(key):
            found.append(f"{at}.{key} must be a non-empty string")
    if row.get("status") not in budget.RUN_STATUSES:
        found.append(f"{at}.status must be one of {', '.join(budget.RUN_STATUSES)}")
    for key in ("specs", "criteria_unmeasured"):
        if not _names(row.get(key)):
            found.append(f"{at}.{key} must list distinct names")
    for key in ("applied", "same_models"):
        if not isinstance(row.get(key), bool):
            found.append(f"{at}.{key} must be true or false")
    sides = {}
    for name in ("baseline", "arm"):
        values = row.get(name)
        if not isinstance(values, dict):
            found.append(f"{at}.{name} must be an object")
            continue
        found += [
            f"{at}.{name}.{k} must be a count"
            for k in SIDE_COUNTS
            if not _count(values.get(k))
        ]
        found += [
            f"{at}.{name}.{k} must be a finite number or null"
            for k in SIDE_RATES
            if values.get(k) is not None and not _finite(values.get(k))
        ]
        sides[name] = values
    delta = row.get("delta")
    if not isinstance(delta, dict):
        return [*found, f"{at}.delta must be an object"]
    for key in DELTA_FIELDS:
        value = delta.get(key)
        if value is not None and not _finite(value):
            found.append(f"{at}.delta.{key} must be a finite number or null")
        elif (
            key in _EXACT
            and value is not None
            and len(sides) == 2
            and all(_count(s.get(key)) for s in sides.values())
            and value != sides["arm"][key] - sides["baseline"][key]
        ):
            found.append(f"{at}.delta.{key} must be the arm's less the baseline's")
    return found


def problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the optional ``sections_off`` and ``ablation`` of *card*."""
    found = []
    version = card.get("schema_version")
    newer = isinstance(version, int) and version >= ABLATION_VERSION
    if "sections_off" in card:
        if not newer:
            found.append("sections_off needs schema v4")
        if not _names(card["sections_off"]):
            found.append("sections_off must list distinct section names")
    if "ablation" not in card:
        return found
    if not newer:
        found.append("ablation needs schema v4")
    if card.get("sections_off"):
        found.append("sections_off must be empty on the baseline of an ablation")
    block = card["ablation"]
    if not isinstance(block, dict):
        return [*found, "ablation must be an object"]
    if not isinstance(block.get("request"), str) or not block.get("request"):
        found.append("ablation.request must be a non-empty string")
    if block.get("baseline") != card.get("stamp"):
        found.append("ablation.baseline must be this card's stamp")
    arms = block.get("arms")
    if not isinstance(arms, list):
        found.append("ablation.arms must be a list")
    else:
        for index, row in enumerate(arms):
            found += _arm_problems(f"ablation.arms[{index}]", row)
        sections = [r.get("section") for r in arms if isinstance(r, dict)]
        if len(set(sections)) != len(sections):
            found.append("ablation.arms names a section twice")
    not_run = block.get("not_run")
    if not isinstance(not_run, list) or not all(
        isinstance(r, dict)
        and isinstance(r.get("section"), str)
        and isinstance(r.get("reason"), str)
        for r in not_run
    ):
        found.append("ablation.not_run must list {section, reason}")
    return found


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _cell(before: Any, after: Any, delta: Any, digits: int = 0) -> str:
    if before is None or after is None:
        return "–"
    shown = f"{before:.{digits}f} → {after:.{digits}f}"
    return f"{shown} ({delta:+.{digits}f})" if delta is not None else f"{shown} (–)"


def render_lines(card: dict[str, Any], level: str = "##") -> list[str]:
    """The card's ablation table, arm less baseline; nothing without a block."""
    block = card.get("ablation")
    if not block:
        return []
    lines = [
        "",
        f"{level} Section ablation",
        "",
        (
            f"Request `{block['request']}`. Each arm ran the specifications again with one "
            "Developer prompt section off (`PROMPT_SECTIONS_OFF`), against this card's run "
            "as the baseline. Deltas are the arm's less the baseline's, over the "
            "specifications both ran in full: criteria from the acceptance tests a person "
            "wrote, rework requests from the board, prompt tokens estimated by the backend "
            "(characters / 4), reported tokens from the provider. Shown, never judged: one "
            "run per arm is one sample, and what to do with a section is the owner's "
            "decision (#443)."
        ),
        "",
        (
            "| Section off | Applied | Specs | Criteria passed | Rework requests "
            "| Rework / task | Prompt tokens (est.) | Per prompt | Reported tokens |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    notes = []
    for row in block["arms"]:
        b, a, d = row["baseline"], row["arm"], row["delta"]
        cells = [
            _cell(b["criteria_passed"], a["criteria_passed"], d["criteria_passed"]),
            _cell(b["rework_requests"], a["rework_requests"], d["rework_requests"]),
            _cell(b["rework_per_task"], a["rework_per_task"], d["rework_per_task"], 2),
            _cell(b["prompt_tokens"], a["prompt_tokens"], d["prompt_tokens"]),
            _cell(
                b["prompt_tokens_mean"],
                a["prompt_tokens_mean"],
                d["prompt_tokens_mean"],
            ),
            _cell(b["tokens_reported"], a["tokens_reported"], d["tokens_reported"]),
        ]
        lines.append(
            f"| {row['section']} | {'yes' if row['applied'] else 'no'} "
            f"| {len(row['specs'])} | " + " | ".join(cells) + " |"
        )
        notes += _notes(row)
    if block.get("not_run"):
        names = ", ".join(r["section"] for r in block["not_run"])
        notes.append(f"Not run: {names} ({block['not_run'][0]['reason']}).")
    return lines + ([""] + [f"- {n}" for n in notes] if notes else [])


def _notes(row: dict[str, Any]) -> list[str]:
    s, out = row["section"], []
    if not row["applied"]:
        out.append(
            f"{s}: not applied (the baseline sent {row['baseline']['section_tokens']} "
            f"estimated tokens of it, the arm {row['arm']['section_tokens']}): "
            "the delta does not measure the section."
        )
    if row["status"] == budget.BUDGET_EXCEEDED:
        out.append(
            f"{s}: the token budget cut the arm; only {len(row['specs'])} specs compared."
        )
    if row["criteria_unmeasured"]:
        out.append(
            f"{s}: grade unmeasured for {', '.join(row['criteria_unmeasured'])}; "
            "no criteria delta."
        )
    if row["baseline"]["turns_unreported"] or row["arm"]["turns_unreported"]:
        out.append(
            f"{s}: turns without reported usage, not counted: "
            f"{row['baseline']['turns_unreported']} → {row['arm']['turns_unreported']}."
        )
    if not row["same_models"]:
        out.append(f"{s}: other models served the arm than the baseline.")
    return out


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    from schema import validate  # schema imports this module

    card = json.loads(path.read_text(encoding="utf-8"))
    problems_found = validate(card)
    if problems_found:
        raise ValueError(
            f"{path} does not match the schema: {'; '.join(problems_found)}"
        )
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--request")
    which.add_argument("--names")
    pl = sub.add_parser("plan")
    pl.add_argument("--request", required=True)
    pl.add_argument("--baseline", required=True, type=Path)
    w = sub.add_parser("write")
    w.add_argument("--baseline", required=True, type=Path)
    w.add_argument("--request", required=True)
    w.add_argument("--arm", action="append", default=[], help="SECTION=CARD")
    w.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "parse":
            if args.names is not None:
                print(",".join(parse_names(args.names)))
            else:
                print(_request_label(parse_request(args.request)))
            return 0
        request = parse_request(args.request)
        baseline = _read(args.baseline)
        if args.command == "plan":
            print("\n".join(plan(request, baseline)[0]))
            return 0
        arms = {}
        for item in args.arm:
            section, _, path = item.partition("=")
            arms[section.strip().lower()] = _read(Path(path))
        card = attach(baseline, build(baseline, arms, request))
        _write(card, args.out_dir)
    except (OSError, ValueError) as exc:
        print(f"ablation: {exc}", file=sys.stderr)
        return 2
    return 0


def _write(card: dict[str, Any], out_dir: Path) -> None:
    import scorecard  # imports schema, which imports this module
    from schema import validate

    found = validate(card)
    if found:
        raise ValueError("the card does not match its schema: " + "; ".join(found))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{card['stamp']}.json").write_text(json.dumps(card, indent=2) + "\n")
    (out_dir / f"{card['stamp']}.md").write_text(scorecard.render_markdown(card))
    print(out_dir / f"{card['stamp']}.json")


if __name__ == "__main__":
    sys.exit(main())
