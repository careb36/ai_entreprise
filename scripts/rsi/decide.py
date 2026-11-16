#!/usr/bin/env python3
"""Decide keep, revert or unmeasured for one self-improvement cycle.

    python3 scripts/rsi/decide.py \\
        --baseline-sha B --candidate-sha C \\
        --baseline-cards DIR_OR_CARD... --candidate-cards DIR_OR_CARD... \\
        --baseline-checks B-ci.json --candidate-checks C-ci.json \\
        --target target.json \\
        [--baseline-tests B-tests.json --candidate-tests C-tests.json] \\
        [--baseline-verifier B-verifier.json --candidate-verifier C-verifier.json] \\
        [--fake-card C-fake.json] [--graders graders.json] [--protected protected.json] \\
        [--k 3]

Reads files the cycle's runner saved and calls
``services/evidence/fitness.decide`` (``docs/design/rsi-loop.md`` §2.6). No
network: the check-runs are the JSON of
``GET /repos/<owner>/<repo>/commits/<sha>/check-runs`` as the caller saved it
(``gh api ... > B-ci.json``). Prints ``{decision, reasons, metrics, inputs}``
as JSON, where ``inputs`` holds the sha256 of every file read, computed here
(REQ-27: never taken from a file). Exit status 0 keep, 1 revert, 2 unmeasured
(argparse's usage errors also exit 2).

The files:

- **Scorecards** (``scripts/bench/scorecard.py``): a file or a folder of
  ``*.json`` cards per side, paired in stamp order (the first B card with the
  first C card...). A card off the schema, or one of the fake model (``llm``
  ``fake``), is left out and named. A file named twice, or by its folder and
  by itself, is read once; the same card under two names counts once; two
  different cards with one stamp count for nothing. The card's ``commit`` is
  the side's sha the fitness function checks.
- **Check-runs**: GitHub's answer (``{"total_count", "check_runs"}``) or a
  list of check-runs. A ``total_count`` above the runs listed is partial.
- **Tests collected**: ``{"sha", "suites": {"<suite>": <count>}}`` per side,
  an observation of its own: refused without the side's sha.
- **Target**: ``{"measure", "baseline": {"sha", "result"}, "candidate":
  {"sha", "result"}}`` with results ``pass``, ``fail`` or ``error``; or
  ``{"metric": "<bench metric>"}`` for a change aimed at a bench number.
- **Verifier bench**: ``{"sha", "cases": {"<case>": "pass"|"fail"}}``.
- **Fake card**: the circuit check's scorecard on C; only a card with
  ``llm`` ``fake`` is read.
- **Graders**: ``{"baseline_tree", "candidate_tree"}``, the tree hashes of
  the graders' paths the runner read from git.
- **Protected**: ``{"sha", "touched": [{"path", "pattern"}], "approval":
  {"sha", "by", "paths"} | null}``, matched by the runner from git.

Every file is one the platform's runner saved, so it is read as produced by
the platform, unless it names another producer itself (``produced_by``): the
fitness function then refuses it. The protected match's approval is stamped
the same way, so an approval that names another producer is still refused. A file that is absent or unreadable is a
missing observation, and the decision says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "app"))
sys.path.insert(0, str(ROOT / "scripts" / "bench"))
import schema  # scripts/bench/schema.py, by path
from services.evidence import fitness

EXIT = {fitness.KEEP: 0, fitness.REVERT: 1, fitness.UNMEASURED: 2}
_SPEC_STATUS_DONE = "completed"
FAKE_LLM = "fake"  # the scorecard's llm for the fake model (scripts/bench/run.sh)


class Inputs:
    """The files read, each with the sha256 computed here, and what failed."""

    def __init__(self) -> None:
        self.read: list[dict[str, Any]] = []

    def load(self, path: str | Path | None) -> Any:
        if path is None:
            return None
        path = Path(path)
        entry: dict[str, Any] = {"path": str(path)}
        self.read.append(entry)
        try:
            data = path.read_bytes()
        except OSError as exc:
            entry["error"] = f"unreadable: {exc.strerror or exc}"
            return None
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        try:
            return json.loads(data)
        except ValueError as exc:
            entry["error"] = f"not JSON: {exc}"
            return None

    def note(self, path: Path, problem: str) -> None:
        for entry in self.read:
            if entry["path"] == str(path):
                entry["error"] = problem


def _stamped(obj: Any) -> Any:
    """*obj* as an observation of the platform unless it names its producer."""
    if not isinstance(obj, dict):
        return None
    return {"produced_by": fitness.PLATFORM, **obj}


def _tokens(rows: list[dict]) -> int | None:
    """Reported tokens, or None when a turn did not report (rule 7)."""
    if not rows or any(r["tokens"]["turns_unreported"] for r in rows):
        return None
    if not any(r["tokens"]["turns_reported"] for r in rows):
        return None
    return sum(r["tokens"]["reported"] for r in rows)


def _prompt_mean(prompt: Any) -> float | None:
    if isinstance(prompt, dict) and prompt.get("prompts"):
        return prompt.get("tokens_mean")
    return None


def _spec_metrics(spec: dict) -> dict[str, Any]:
    total = spec["criteria"]["total"]
    return {
        "criteria_rate": spec["criteria"]["passed"] / total if total else None,
        "first_attempt_rate": spec["first_attempt_rate"],
        "blocked_share": spec["blocked_share"],
        "rework_per_task": spec["rework_per_task"],
        "tokens_reported": _tokens([spec]),
        "wall_clock_seconds": spec["wall_clock_seconds"],
        "prompt_tokens_mean": _prompt_mean(spec.get("prompt")),
    }


def card_run(card: dict, pair: int) -> dict[str, Any]:
    """A scorecard as one bench run of ``fitness.decide``."""
    specs = card["specs"]
    totals = card["totals"]
    statuses = {s["status"] for s in specs} - {_SPEC_STATUS_DONE}
    cut = (card.get("run") or {}).get("status") == "budget_exceeded"
    models: dict[str, set[str]] = {}
    for spec in specs:
        for role, names in spec["models"].items():
            models.setdefault(role, set()).update(names)
    return {
        "produced_by": card.get("produced_by", fitness.PLATFORM),
        "sha": card["commit"],
        "pair": pair,
        "status": "budget_exceeded"
        if cut
        else (min(statuses) if statuses else fitness.COMPLETE),
        "models": {role: sorted(names) for role, names in models.items()},
        "totals": {
            "criteria_rate": totals["criteria_rate"],
            "first_attempt_rate": totals["first_attempt_rate"],
            "blocked_share": totals["blocked_share"],
            "rework_per_task": totals["rework_per_task"],
            "tokens_reported": _tokens(specs),
            "wall_clock_seconds": totals["wall_clock_seconds"],
            "prompt_tokens_mean": _prompt_mean(totals.get("prompt")),
        },
        "specs": {s["name"]: _spec_metrics(s) for s in specs},
    }


def _card_files(paths: list[str]) -> list[Path]:
    """Each card file once: a file named twice, or by its folder and by
    itself, is one file (compared by its resolved path)."""
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        for one in sorted(path.glob("*.json")) if path.is_dir() else [path]:
            key = one.resolve()
            if key not in seen:
                seen.add(key)
                found.append(one)
    return found


def read_cards(paths: list[str], inputs: Inputs) -> list[dict]:
    """The sound real-model cards among *paths*, one per stamp, in stamp order.

    A fake-model card measures no factory (rsi-loop §2.5) and is left out.
    The same card under two names counts once; two different cards with one
    stamp are ambiguous, and neither counts.
    """
    by_stamp: dict[str, list[tuple[Path, dict]]] = {}
    for path in _card_files(paths):
        card = inputs.load(path)
        if card is None:
            continue
        problems = schema.validate(card)
        if problems:
            inputs.note(path, "off the schema: " + "; ".join(problems[:3]))
            continue
        if card.get("llm") == FAKE_LLM:
            inputs.note(path, "a fake-model card is no measurement of B or C")
            continue
        by_stamp.setdefault(card["stamp"], []).append((path, card))
    cards = []
    for stamp, found in sorted(by_stamp.items()):
        first_path, first = found[0]
        if any(card != first for _, card in found[1:]):
            for path, _ in found:
                inputs.note(path, f"stamp {stamp} names {len(found)} different cards")
            continue
        for path, _ in found[1:]:
            inputs.note(path, f"the same card as {first_path}, counted once")
        cards.append(first)
    return cards


def read_checks(listing: Any, sha: str, tests: Any) -> dict[str, Any] | None:
    """GitHub's check-run listing as the ``ci`` observation of one side."""
    if isinstance(listing, dict) and isinstance(listing.get("check_runs"), list):
        runs, total = listing["check_runs"], listing.get("total_count")
    elif isinstance(listing, list):
        runs, total = listing, None
    else:
        return None
    return {
        "produced_by": listing.get("produced_by", fitness.PLATFORM)
        if isinstance(listing, dict)
        else fitness.PLATFORM,
        "sha": sha,
        "incomplete": isinstance(total, int) and total > len(runs),
        "check_runs": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "head_sha": r.get("head_sha"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "app": (r.get("app") or {}).get("slug")
                if isinstance(r.get("app"), dict)
                else r.get("app"),
            }
            for r in runs
            if isinstance(r, dict)
        ],
        "tests_collected": _stamped(tests),
    }


def _fake(card: Any, path: str | None, inputs: Inputs) -> dict[str, Any] | None:
    """The circuit check on C: a sound fake-model card only (rsi-loop §2.5)."""
    if not isinstance(card, dict) or schema.validate(card):
        return None
    if card.get("llm") != FAKE_LLM:
        inputs.note(Path(path), "the circuit check needs a fake-model card")
        return None
    return {
        "produced_by": card.get("produced_by", fitness.PLATFORM),
        "sha": card["commit"],
        "criteria_passed": card["totals"]["criteria_passed"],
        "criteria_total": card["totals"]["criteria_total"],
    }


def _protected(obj: Any) -> Any:
    """The protected match, its approval stamped as the platform's record
    unless the approval names its own producer (then refused downstream)."""
    found = _stamped(obj)
    if found is not None and isinstance(found.get("approval"), dict):
        found["approval"] = _stamped(found["approval"])
    return found


def build(args: argparse.Namespace) -> dict[str, Any]:
    """Read every file and return the decision with the inputs' sha256."""
    inputs = Inputs()
    sides = {}
    for side, sha in (
        ("baseline", args.baseline_sha),
        ("candidate", args.candidate_sha),
    ):
        cards = read_cards(getattr(args, f"{side}_cards"), inputs)
        listing = inputs.load(getattr(args, f"{side}_checks"))
        tests = inputs.load(getattr(args, f"{side}_tests"))
        sides[side] = {
            "sha": sha,
            "bench": [card_run(card, pair) for pair, card in enumerate(cards)],
            "ci": read_checks(listing, sha, tests),
            "verifier_bench": _stamped(inputs.load(getattr(args, f"{side}_verifier"))),
        }
    sides["candidate"]["fake_bench"] = _fake(
        inputs.load(args.fake_card), args.fake_card, inputs
    )
    result = fitness.decide(
        sides["baseline"],
        sides["candidate"],
        graders=_stamped(inputs.load(args.graders)),
        target=_stamped(inputs.load(args.target)),
        protected=_protected(inputs.load(args.protected)),
        k=args.k,
    )
    result["inputs"] = inputs.read
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--baseline-sha", required=True)
    p.add_argument("--candidate-sha", required=True)
    for side in ("baseline", "candidate"):
        p.add_argument(f"--{side}-cards", nargs="+", default=[], metavar="PATH")
        p.add_argument(f"--{side}-checks", metavar="FILE")
        p.add_argument(f"--{side}-tests", metavar="FILE")
        p.add_argument(f"--{side}-verifier", metavar="FILE")
    p.add_argument("--target", metavar="FILE")
    p.add_argument("--fake-card", metavar="FILE")
    p.add_argument("--graders", metavar="FILE")
    p.add_argument("--protected", metavar="FILE")
    p.add_argument(
        "--k", type=int, default=fitness.K, choices=range(1, 100), metavar="K"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    result = build(parser().parse_args(argv))
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT[result["decision"]]


if __name__ == "__main__":
    sys.exit(main())
