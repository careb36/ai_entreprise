#!/usr/bin/env python3
"""The best edit format per model: the bench run once per format (schema v7).

Issue #689 (#359, A.8). The method is Aider's edit-format benchmark (no code
taken): the same tasks, run once per edit format per model, graded by the
same tests, with how often the model used the format correctly beside the
result. The backend asks each model for the format ``EDIT_FORMAT_BY_MODEL``
names (``backend/app/services/models/edit_format.py``: ``auto``, ``whole``,
``search_replace``) and records on each run's evidence the format asked for
and what it read in the answer (``edit_format``). ``edit_formats.sh`` runs
``run.sh`` once per model and format (``BENCH_MODELS``, ``BENCH_EDIT_FORMAT``)
and this module writes, onto the first run's card, one row per model and
format:

- the outcome: ``pass`` (every criterion passed, graded by the acceptance
  tests a person wrote), ``fail``, ``unusable`` (deliveries were observed in
  the format and none was well formed: every one held a malformed edit
  block, a block off the format, or an edit the daemon refused; never a pass,
  whatever the tests said) or ``unmeasured`` with the reason (a grade that
  could not run, a run the budget cut or that failed, no delivery observed,
  or deliveries the backend ran under another format);
- criteria passed and total, rework requests, reported tokens;
- the deliveries observed, the well-formed ones and their share, and the
  malformed blocks, blocks off the format, edits the daemon refused, whole
  files, edit blocks and edit-tool calls.

Per spec, ``scorecard.py collect`` sums each delivery's observation
(``spec_summary``); a run without one (another producer, a backend from
before #689) is counted apart (``missing``), never as a delivery in some
format. The numbers inform the routing's choice later, made on real-model
runs; nothing here chooses a default, and ``compare.py`` shows them without
judging them.

Subcommands, used by ``edit_formats.sh`` and ``run.sh``:

- ``parse --formats F`` checks a comma-separated list of formats (or
  ``each``) against the backend's and prints them, one per line; exit 2 on
  an unknown one;
- ``parse --models M`` checks a comma-separated list of model ids and prints
  them, one per line; exit 2 on an empty or malformed one;
- ``write --arm MODEL FORMAT CARD... --out-dir DIR`` writes the first arm's
  card with the ``edit_formats`` block as ``<stamp>.json`` and ``.md``.

The formats are read from the backend's module without importing it, so the
bench and the backend under test cannot drift apart.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # the sibling modules, by path

EDIT_FORMAT_MODULE = (
    Path(__file__).resolve().parents[2] / "backend/app/services/models/edit_format.py"
)
VERSION = 7  # the schema version that brought the fields
EACH = "each"
DEFAULT_MODEL = "default"
OUTCOMES = ("pass", "fail", "unusable", "unmeasured")
SUMMARY_COUNTS = (
    "deliveries",
    "observed",
    "missing",
    "well_formed",
    "whole_files",
    "edit_blocks",
    "malformed_edit_blocks",
    "off_format",
    "rejected_edits",
    "tool_edits",
    "tool_edit_errors",
)
ROW_COUNTS = (
    "criteria_passed",
    "criteria_total",
    "rework_requests",
    "tokens_reported",
    *SUMMARY_COUNTS,
)
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


# ---------------------------------------------------------------------------
# The names
# ---------------------------------------------------------------------------


def known_formats(path: Path = EDIT_FORMAT_MODULE) -> tuple[str, ...]:
    """The backend's ``FORMATS``, in its order, read without importing it."""
    strings: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        name = getattr(node.targets[0], "id", None)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            strings[name] = node.value.value
        elif name == "FORMATS" and isinstance(node.value, ast.Tuple):
            return tuple(
                strings[e.id] if isinstance(e, ast.Name) else ast.literal_eval(e)
                for e in node.value.elts
            )
    raise ValueError(f"{path} names no FORMATS")


def parse_formats(text: str) -> list[str]:
    """The formats asked for, in the backend's order with ``each``; ValueError otherwise."""
    known = known_formats()
    names = [n.strip().lower() for n in str(text or "").split(",")]
    names = list(dict.fromkeys(n for n in names if n))
    if names == [EACH]:
        return list(known)
    unknown = [n for n in names if n not in known]
    if not names or unknown:
        raise ValueError(
            f"unknown edit format(s) {', '.join(unknown) or '(none given)'}; "
            f"known: {', '.join(known)}, or {EACH}"
        )
    return names


def parse_models(text: str) -> list[str]:
    """The model ids asked for, each run once per format; ValueError otherwise."""
    names = list(dict.fromkeys(n.strip() for n in str(text or "").split(",")))
    if not names or any(not _MODEL.match(n) for n in names):
        raise ValueError(f"malformed model list {text!r}: model ids, comma-separated")
    return names


# ---------------------------------------------------------------------------
# Per spec, from each delivery's observation
# ---------------------------------------------------------------------------


def empty_summary() -> dict[str, Any]:
    return {**{k: 0 for k in SUMMARY_COUNTS}, "requested": {}}


def spec_summary(deliveries: list[dict[str, Any]]) -> dict[str, Any]:
    """The spec's deliveries summed (``readers.edit_formats`` rows). A
    delivery is well formed when the backend found it so and the daemon
    refused none of its edits."""
    out = empty_summary()
    for row in deliveries:
        out["deliveries"] += 1
        seen = row.get("edit_format")
        if not isinstance(seen, dict):
            out["missing"] += 1
            continue
        out["observed"] += 1
        fmt = str(seen.get("requested") or "")
        out["requested"][fmt] = out["requested"].get(fmt, 0) + 1
        rejected = int(row.get("rejected_edits") or 0)
        out["rejected_edits"] += rejected
        for key in (
            "whole_files",
            "edit_blocks",
            "malformed_edit_blocks",
            "off_format",
            "tool_edits",
            "tool_edit_errors",
        ):
            out[key] += int(seen.get(key) or 0)
        if seen.get("well_formed") is True and rejected == 0:
            out["well_formed"] += 1
    return out


def merge(summaries: list[Any]) -> dict[str, Any]:
    out = empty_summary()
    for s in summaries:
        if not isinstance(s, dict):
            continue
        for key in SUMMARY_COUNTS:
            out[key] += int(s.get(key) or 0)
        for fmt, n in (s.get("requested") or {}).items():
            out["requested"][fmt] = out["requested"].get(fmt, 0) + int(n)
    return out


# ---------------------------------------------------------------------------
# One row per model and format
# ---------------------------------------------------------------------------


def _outcome(
    card: dict[str, Any], fmt: str, seen: dict[str, Any], passed: int, total: int
):
    specs = card.get("specs") or []
    unmeasured = [
        s["name"] for s in specs if (s.get("criteria") or {}).get("unmeasured")
    ]
    failed = [s["name"] for s in specs if s.get("status") == "error"]
    if budget.is_cut(card):
        return "unmeasured", "the token budget cut the run"
    if failed or unmeasured:
        return "unmeasured", "not graded in full: " + ", ".join(failed + unmeasured)
    if not seen["observed"]:
        return "unmeasured", "no delivery observed in any format"
    if seen["requested"].get(fmt, 0) != seen["observed"]:
        return (
            "unmeasured",
            f"the backend ran deliveries under another format than {fmt}",
        )
    if not seen["well_formed"]:
        return "unusable", (
            f"no delivery well formed: {seen['malformed_edit_blocks']} malformed edit "
            f"block(s), {seen['off_format']} off the format, {seen['rejected_edits']} "
            "edit(s) refused by the daemon"
        )
    return ("pass" if total and passed == total else "fail"), None


def row(model: str, fmt: str, card: dict[str, Any]) -> dict[str, Any]:
    specs = card.get("specs") or []
    seen = merge([s.get("edit_format") for s in specs])
    passed = sum(s["criteria"]["passed"] for s in specs)
    total = sum(s["criteria"]["total"] for s in specs)
    outcome, reason = _outcome(card, fmt, seen, passed, total)
    served = sorted(
        {m for s in specs for ms in (s.get("models") or {}).values() for m in ms}
    )
    return {
        "model": model,
        "format": fmt,
        "card": card["stamp"],
        "served_models": served,
        "outcome": outcome,
        "reason": reason,
        "criteria_passed": passed,
        "criteria_total": total,
        "rework_requests": sum(s["rework_requests"] for s in specs),
        "tokens_reported": sum(s["tokens"]["reported"] for s in specs),
        **{k: seen[k] for k in SUMMARY_COUNTS},
        "well_formed_rate": (
            round(seen["well_formed"] / seen["observed"], 4)
            if seen["observed"]
            else None
        ),
    }


def block(arms: list[tuple[str, str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "formats": list(dict.fromkeys(fmt for _m, fmt, _c in arms)),
        "models": list(dict.fromkeys(m for m, _f, _c in arms)),
        "rows": [row(m, fmt, card) for m, fmt, card in arms],
    }


def arm_problems(arms: list[tuple[str, str, dict[str, Any]]]) -> list[str]:
    """Why these cards are not one comparison: another commit, policy or
    graders, or a card not run under its arm's format."""
    found = []
    first = arms[0][2]
    for model, fmt, card in arms:
        for key in ("commit", "policy", "graders"):
            if card.get(key) != first.get(key):
                found.append(f"{model}/{fmt}: another {key} than the first arm's")
        if card.get("edit_format") != fmt:
            found.append(
                f"{model}/{fmt}: its card was run under {card.get('edit_format')!r}"
            )
    pairs = [(m, f) for m, f, _c in arms]
    if len(set(pairs)) != len(pairs):
        found.append("a model and format given twice")
    return found


# ---------------------------------------------------------------------------
# The schema (v7)
# ---------------------------------------------------------------------------


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _summary_problems(where: str, obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where} must be an object"]
    found = [
        f"{where}.{k} must be a count" for k in SUMMARY_COUNTS if not _count(obj.get(k))
    ]
    requested = obj.get("requested")
    if not isinstance(requested, dict) or not all(
        _count(v) for v in requested.values()
    ):
        found.append(f"{where}.requested must map formats to counts")
    if not found and not (
        obj["observed"] + obj["missing"] == obj["deliveries"]
        and obj["well_formed"] <= obj["observed"]
    ):
        found.append(
            f"{where}: observed + missing = deliveries, well_formed <= observed"
        )
    return found


def problems(card: dict[str, Any]) -> list[str]:
    """What is wrong with the card's edit-format fields; none before v7."""
    version = card.get("schema_version")
    if not (isinstance(version, int) and version >= VERSION):
        return [
            f"{k} needs schema v{VERSION}"
            for k in ("edit_format", "edit_formats")
            if k in card
        ] + [
            f"specs[{i}].edit_format needs schema v{VERSION}"
            for i, s in enumerate(card.get("specs") or [])
            if isinstance(s, dict) and "edit_format" in s
        ]
    found = []
    known = known_formats()
    if "edit_format" in card and card["edit_format"] not in known:
        found.append(f"edit_format must be one of {', '.join(known)}")
    for index, spec in enumerate(card.get("specs") or []):
        if isinstance(spec, dict) and "edit_format" in spec:
            found += _summary_problems(
                f"specs[{index}].edit_format", spec["edit_format"]
            )
    data = card.get("edit_formats")
    if data is None:
        return found
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return [*found, "edit_formats.rows must be a list"]
    for index, r in enumerate(data["rows"]):
        at = f"edit_formats.rows[{index}]"
        if not isinstance(r, dict):
            found.append(f"{at} is not an object")
            continue
        if r.get("format") not in known:
            found.append(f"{at}.format must be one of {', '.join(known)}")
        if not isinstance(r.get("model"), str) or not r.get("model"):
            found.append(f"{at}.model must be a non-empty string")
        if r.get("outcome") not in OUTCOMES:
            found.append(f"{at}.outcome must be one of {', '.join(OUTCOMES)}")
        found += [
            f"{at}.{k} must be a count" for k in ROW_COUNTS if not _count(r.get(k))
        ]
        if r.get("outcome") in ("unusable", "unmeasured") and not r.get("reason"):
            found.append(f"{at}: an {r.get('outcome')} row needs its reason")
        if (
            r.get("outcome") == "pass"
            and _count(r.get("well_formed"))
            and r["well_formed"] == 0
        ):
            found.append(f"{at}: a format no delivery used well cannot pass")
        rate = r.get("well_formed_rate")
        if rate is not None and not (
            isinstance(rate, (int, float))
            and not isinstance(rate, bool)
            and 0 <= rate <= 1
        ):
            found.append(f"{at}.well_formed_rate must be from 0 to 1, or null")
    return found


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _rate(value: Any) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def render_lines(card: dict[str, Any], level: str = "##") -> list[str]:
    """The card's per-format table; nothing without the block."""
    data = card.get("edit_formats")
    if not data:
        return []
    lines = [
        "",
        f"{level} Edit format per model",
        "",
        (
            "Each model run once per edit format on the same specifications, graded by "
            "the acceptance tests a person wrote; the format's use is what the backend "
            "read in each answer and the daemon applied, never the model's account. "
            "Shown, never judged: one run per row is one sample."
        ),
        "",
        (
            "| Model | Format | Outcome | Criteria | Well formed | Malformed "
            "| Off format | Refused | Rework | Tokens |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in data["rows"]:
        shown = r["outcome"] + (f" ({r['reason']})" if r.get("reason") else "")
        lines.append(
            f"| {r['model']} | {r['format']} | {shown} "
            f"| {r['criteria_passed']}/{r['criteria_total']} "
            f"| {r['well_formed']}/{r['observed']} ({_rate(r['well_formed_rate'])}) "
            f"| {r['malformed_edit_blocks']} | {r['off_format']} | {r['rejected_edits']} "
            f"| {r['rework_requests']} | {r['tokens_reported']} |"
        )
    return lines


def run_line(card: dict[str, Any]) -> list[str]:
    """The run's own format, on a card run under one."""
    if "edit_format" not in card:
        return []
    seen = merge([s.get("edit_format") for s in card.get("specs") or []])
    return [
        "",
        (
            f"Edit format asked for: `{card['edit_format']}`; deliveries well formed "
            f"{seen['well_formed']} of {seen['observed']} observed ({seen['missing']} "
            "without an observation)."
        ),
    ]


def render_compare(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Each model and format's outcome and criteria, before → after; shown,
    never judged."""
    before, after = previous.get("edit_formats"), current.get("edit_formats")
    if not before and not after:
        return []
    if not before:
        return render_lines(current, level="###")

    def cells(data: Any) -> dict[tuple[str, str], str]:
        return {
            (
                r["model"],
                r["format"],
            ): f"{r['outcome']} {r['criteria_passed']}/{r['criteria_total']}"
            for r in (data or {}).get("rows") or []
        }

    was, now = cells(before), cells(after)
    lines = [
        "",
        "### Edit format per model",
        "",
        "Outcome and criteria per model and format, before → after; shown, never judged.",
        "",
        "| Model | Format | Before | After |",
        "|---|---|---|---|",
    ]
    for key in sorted({*was, *now}):
        lines.append(
            f"| {key[0]} | {key[1]} | {was.get(key, '–')} | {now.get(key, '–')} |"
        )
    return lines


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def write(arms: list[tuple[str, str, dict[str, Any]]], out_dir: Path) -> Path:
    import scorecard  # by path; it imports this module through the schema

    base = copy.deepcopy(arms[0][2])
    base["schema_version"] = scorecard.SCHEMA_VERSION
    base["edit_formats"] = block(arms)
    found = scorecard.validate(base)
    if found:
        raise ValueError("the card does not match its schema:\n  " + "\n  ".join(found))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{base['stamp']}.json"
    path.write_text(json.dumps(base, indent=2) + "\n")
    path.with_suffix(".md").write_text(scorecard.render_markdown(base))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--formats")
    group.add_argument("--models")
    w = sub.add_parser("write")
    w.add_argument(
        "--arm",
        nargs=3,
        action="append",
        required=True,
        metavar=("MODEL", "FORMAT", "CARD"),
    )
    w.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "parse":
            names = (
                parse_formats(args.formats)
                if args.formats is not None
                else parse_models(args.models)
            )
            print("\n".join(names))
            return 0
        arms = []
        for model, fmt, path in args.arm:
            (fmt,) = parse_formats(fmt)
            arms.append(
                (model, fmt, json.loads(Path(path).read_text(encoding="utf-8")))
            )
        found = arm_problems(arms)
        if found:
            raise ValueError(
                "the runs are not one comparison:\n  " + "\n  ".join(found)
            )
        print(write(arms, args.out_dir))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"edit formats: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
