#!/usr/bin/env python3
"""The watch after the merge of a self-improvement cycle (REQ-19).

    python3 scripts/rsi/watch.py --cycle rsi/cycles/<cycle id>.json \\
        [--cards DIR_OR_FILE ...] [--repo DIR] [--out FILE]

While the merged change has fewer than two nightly real-model runs on ``main``
after it (``docs/design/rsi-loop.md`` §1.6, §7 step 8), each run's scorecard is
compared with the cycle's baseline B — the medians and spread the cycle record
carries (``scripts/rsi/cycle_record.py``) — with the same rule as
``scripts/bench/repeated.py`` and ``services/evidence/fitness.py``, which this
script imports rather than copies: a regression must exceed both the threshold
of decision 4 (``fitness.THRESHOLDS``: rate 0.05, rework 0.25, tokens 0.15 and
wall-clock 0.25 relative) and B's own spread (``max(threshold, spread_B)``,
relative to B's median for the relative metrics). The wall-clock is judged:
the nightly runs on the same self-hosted runner as the cycle's B runs.

A card counts only when every one of these holds; the rest are excluded and
named, with the reason, in the output:

- it matches the scorecard schema (``scripts/bench/schema.py``);
- its served model is real (``llm`` is not ``fake``: the fake is a circuit
  check, not a fitness signal);
- it does not name a producer other than the platform (``produced_by``);
- its stamp is strictly after the record's ``merged_at``;
- its ``commit`` exists in ``--repo`` and has the record's ``merged_sha`` as
  an ancestor, verified with git — the card's word is not enough;
- it carries no ``graders`` block, or one whose ``sha`` is the record's
  ``graders_sha``: a card graded by another commit's graders does not count.

A counted card is one night (cards of one UTC day are one night). The night is
``regressed`` when a metric regresses, or a specification the baseline
measured in full did not complete (never read as zero); ``unmeasured`` when a
judged metric has no number — a run the budget cut, a pair void because the
models served differ from B's (REQ-7), a card with no served model at all, a
metric B or the card did not measure — and never ``ok``; ``ok`` otherwise.

Prints JSON: the decision per night with its reasons and numbers, the excluded
cards, the overall decision and the sha256 of every file read, computed here
(never taken from a file). The overall decision is ``regressed`` when any
night regressed, ``ok`` when two nights were watched with no regression (the
cycle closes), and ``unmeasured`` otherwise. Exit status 0 ok, 1 regressed
(``scripts/rsi/revert.py`` opens the revert pull request), 2 unmeasured or bad
input, including a record that does not validate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _path in (
    ROOT / "backend" / "app",
    ROOT / "scripts" / "bench",
    ROOT / "scripts" / "rsi",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
import budget  # scripts/bench/budget.py, by path
import cycle_record  # scripts/rsi/cycle_record.py, by path
import decide  # scripts/rsi/decide.py, by path: the file reader and its hashes
import repeated  # scripts/bench/repeated.py, by path: the comparison rule
import schema  # scripts/bench/schema.py, by path
from services.evidence import fitness

OK, REGRESSED, UNMEASURED = "ok", "regressed", "unmeasured"
NIGHTS_REQUIRED = 2  # REQ-19: two nightly runs watched after the merge
EXIT = {OK: 0, REGRESSED: 1, UNMEASURED: 2}
# The night's decision: a regression outweighs a measured night, which
# outweighs an unmeasured one (unknown is not absent, never a pass).
_RANK = {REGRESSED: 2, OK: 1, UNMEASURED: 0}
_STAMP = "%Y%m%dT%H%M%SZ"


def _reason(outcome: str, code: str, detail: str) -> dict[str, str]:
    return {"outcome": outcome, "code": code, "detail": detail}


# --- git: the ancestry the card claims is verified, never believed -------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def commit_exists(repo: Path, sha: str) -> bool:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return done.returncode == 0 and bool(done.stdout.strip())


def has_ancestor(repo: Path, ancestor: str, commit: str) -> bool:
    """Whether *ancestor* is *commit* itself or one of its ancestors, per git."""
    return _git(repo, "merge-base", "--is-ancestor", ancestor, commit).returncode == 0


def stamp_dt(card: dict[str, Any]) -> datetime | None:
    """The card's stamp as an aware UTC datetime (the stamps sort by name)."""
    try:
        return datetime.strptime(card["stamp"], _STAMP).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


# --- which cards count -----------------------------------------------------------


def read_cards(
    paths: list[str], inputs: decide.Inputs
) -> tuple[list[tuple[Path, dict]], list[dict[str, str]]]:
    """The sound real-model cards among *paths*, and the excluded ones named.

    A file named twice is read once (``decide._card_files``); every file read
    is hashed by *inputs* (REQ-27: the hash is never taken from a file).
    """
    counted: list[tuple[Path, dict]] = []
    excluded: list[dict[str, str]] = []
    for path in decide._card_files(paths):
        card = inputs.load(path)
        if card is None:
            excluded.append({"path": str(path), "reason": "unreadable or not JSON"})
            continue
        if problems := schema.validate(card):
            excluded.append(
                {
                    "path": str(path),
                    "reason": "off the schema: " + "; ".join(problems[:3]),
                }
            )
            continue
        if card.get("llm") == decide.FAKE_LLM:
            excluded.append(
                {
                    "path": str(path),
                    "reason": "a fake-model card is a circuit check, no measurement",
                }
            )
            continue
        counted.append((path, card))
    return counted, excluded


def counts(
    record: dict[str, Any], merged_at: datetime, repo: Path, card: dict[str, Any]
) -> str | None:
    """Why *card* does not count against *record*; None when it does."""
    producer = card.get("produced_by", fitness.PLATFORM)
    if producer != fitness.PLATFORM:
        return f"produced by {producer!r}, not by the platform; refused"
    when = stamp_dt(card)
    if when is None:
        return "the stamp is not a UTC bench stamp"
    if when <= merged_at:
        return f"made at {card['stamp']}, not after the merge"
    graders = card.get("graders")
    if isinstance(graders, dict) and graders.get("sha") != record["graders_sha"]:
        return f"graded by another commit's graders ({str(graders.get('sha'))[:12]})"
    commit = card["commit"]
    if not commit_exists(repo, commit):
        return f"commit {commit[:12]} is not in the repository at --repo"
    if not has_ancestor(repo, record["merged_sha"], commit):
        return f"commit {commit[:12]} does not contain the merged sha"
    return None


# --- judging one card against the record's baseline -------------------------------


def _limits(record: dict[str, Any]) -> dict[str, Any]:
    """Decision 4's thresholds; the prompt's weight only when targeted."""
    limits: dict[str, Any] = dict(fitness.THRESHOLDS)
    if not record.get("prompt_targeted"):
        limits["prompt_tokens"] = None
    return limits


def _scope_state(
    card: dict[str, Any], scope: str
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """(values, unmeasured, regressed) of one scope of *card*.

    *values* are the scope's metrics (``repeated.numbers``) when the scope was
    measured; *unmeasured* says why it was not (never read as zero);
    *regressed* names a specification the baseline measured in full and that
    did not complete (``compare.py``'s rule for a single card).
    """
    if scope == "totals":
        if budget.is_cut(card):
            return None, "the token budget cut the run: its totals are not judged", None
        if not repeated._complete_card(card):
            bad = sorted(
                {s["status"] for s in card["specs"] if s["status"] != "completed"}
            )
            return None, f"the run did not complete ({', '.join(bad)})", None
        return repeated.numbers(card["totals"], is_spec=False), None, None
    spec = next((s for s in card["specs"] if s["name"] == scope), None)
    if spec is None:
        return None, "the card does not hold the specification", None
    status = spec["status"]
    if status == repeated.COMPLETED:
        return repeated.numbers(spec, is_spec=True), None, None
    if status == budget.BUDGET_EXCEEDED:
        return None, "the token budget stopped the specification", None
    return None, None, f"{scope}: completed on B, {status} now"


def judge_card(record: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """One counted card against B's medians: reasons and the numbers."""
    reasons: list[dict[str, str]] = []
    metrics: list[dict[str, Any]] = []
    served = repeated.served_models(card)
    if not served:
        reasons.append(
            _reason(UNMEASURED, "no_producer", "the card names no model served")
        )
        return {"decision": UNMEASURED, "reasons": reasons, "metrics": metrics}
    baseline_models = {
        str(role): sorted(map(str, names)) for role, names in record["models"].items()
    }
    if served != baseline_models:
        roles = sorted(
            r
            for r in set(served) | set(baseline_models)
            if served.get(r) != baseline_models.get(r)
        )
        reasons.append(
            _reason(
                UNMEASURED,
                "void_pair",
                f"the models served differ from B's ({', '.join(roles)}); "
                "the pair is void and counts for nothing (REQ-7)",
            )
        )
        return {"decision": UNMEASURED, "reasons": reasons, "metrics": metrics}

    limits = _limits(record)
    baseline = record["baseline"]
    scopes = ["totals", *sorted(baseline.get("specs") or {})]
    for scope in scopes:
        where = "total" if scope == "totals" else scope
        summaries = (
            baseline["totals"] if scope == "totals" else baseline["specs"][scope]
        )
        values, unmeasured, regressed = _scope_state(card, scope)
        if regressed:
            reasons.append(_reason(REGRESSED, "completion", regressed))
            continue
        if unmeasured:
            reasons.append(
                _reason(UNMEASURED, "not_complete", f"{where}: {unmeasured}")
            )
            continue
        for field, label in repeated._judged(limits, where):
            summary = summaries.get(field)
            value = (values or {}).get(field)
            if not isinstance(summary, dict):
                reasons.append(
                    _reason(UNMEASURED, "no_baseline", f"{where}: B has no {label}")
                )
                continue
            if value is None:
                reasons.append(
                    _reason(
                        UNMEASURED,
                        "not_measured",
                        f"{where}: {label} not measured by the card",
                    )
                )
                metrics.append(
                    {
                        "scope": where,
                        "metric": field,
                        "baseline": summary,
                        "value": None,
                    }
                )
                continue
            detail = repeated._judge(
                where,
                field,
                label,
                {"median": summary["median"], "spread": summary["spread"]},
                {"median": value, "spread": 0.0, "runs": 1},
                limits,
                1,
            )
            metrics.append(
                {
                    "scope": where,
                    "metric": field,
                    "baseline": summary,
                    "value": value,
                    "regressed": bool(detail),
                }
            )
            if detail:
                reasons.append(_reason(REGRESSED, "regression", detail))
    outcomes = {r["outcome"] for r in reasons}
    decision = (
        REGRESSED
        if REGRESSED in outcomes
        else UNMEASURED
        if UNMEASURED in outcomes
        else OK
    )
    return {"decision": decision, "reasons": reasons, "metrics": metrics}


# --- the watch --------------------------------------------------------------------


def _decide_night(cards: list[dict[str, Any]]) -> str:
    decisions = {card["decision"] for card in cards}
    for outcome in (REGRESSED, OK, UNMEASURED):
        if outcome in decisions:
            return outcome
    return UNMEASURED


def watch_cycle(
    record: dict[str, Any],
    card_paths: list[str],
    repo: Path,
    inputs: decide.Inputs,
) -> dict[str, Any]:
    """The watch of one validated record against the cards (module docstring)."""
    result: dict[str, Any] = {
        "cycle_id": record["cycle_id"],
        "merged_sha": record["merged_sha"],
        "nights_required": NIGHTS_REQUIRED,
    }
    merged_at = cycle_record.merged_at_dt(record)
    if merged_at is None or not commit_exists(repo, record["merged_sha"]):
        result.update(
            decision=UNMEASURED,
            nights=[],
            nights_ok=0,
            excluded=[],
            reasons=[
                _reason(
                    UNMEASURED,
                    "merged_sha_unknown",
                    f"the merged sha {record['merged_sha'][:12]} is not a commit "
                    f"of the repository at {repo}",
                )
            ],
        )
        return result
    counted, excluded = read_cards(card_paths, inputs)
    nights: dict[str, dict[str, Any]] = {}
    for path, card in counted:
        if why := counts(record, merged_at, repo, card):
            excluded.append({"path": str(path), "reason": why})
            continue
        judged = judge_card(record, card)
        night = nights.setdefault(card["stamp"][:8], {"cards": []})
        night["cards"].append(
            {
                "path": str(path),
                "stamp": card["stamp"],
                "commit": card["commit"],
                **judged,
            }
        )
    night_list = []
    for key in sorted(nights):
        cards = nights[key]["cards"]
        night_list.append(
            {"night": key, "decision": _decide_night(cards), "cards": cards}
        )
    ok = sum(1 for night in night_list if night["decision"] == OK)
    regressed = [n for n in night_list if n["decision"] == REGRESSED]
    reasons: list[dict[str, str]] = []
    if regressed:
        decision = REGRESSED
        for night in regressed:
            for card in night["cards"]:
                reasons += [
                    _reason(
                        REGRESSED, r["code"], f"night {night['night']}: {r['detail']}"
                    )
                    for r in card["reasons"]
                    if r["outcome"] == REGRESSED
                ]
    elif ok >= NIGHTS_REQUIRED:
        decision = OK
        reasons.append(
            _reason(
                OK, "closed", f"{ok} nights watched, no regression: the cycle closes"
            )
        )
    else:
        decision = UNMEASURED
        reasons.append(
            _reason(
                UNMEASURED,
                "watching",
                f"{ok} of {NIGHTS_REQUIRED} nights watched; the watch continues",
            )
        )
    result.update(
        decision=decision,
        nights=night_list,
        nights_ok=ok,
        excluded=excluded,
        reasons=reasons,
    )
    return result


def run(cycle: str | Path, card_paths: list[str], repo: str | Path = ROOT) -> dict:
    """Read the record and the cards, and return the watch's JSON answer."""
    inputs = decide.Inputs()
    record = inputs.load(cycle)
    result: dict[str, Any] = {"cycle": str(cycle)}
    if problems := cycle_record.validate(record):
        result.update(
            decision=UNMEASURED,
            nights=[],
            nights_ok=0,
            excluded=[],
            reasons=[
                _reason(UNMEASURED, "invalid_record", problem) for problem in problems
            ],
        )
    else:
        result.update(watch_cycle(record, card_paths, Path(repo), inputs))
    result["inputs"] = inputs.read
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cycle", required=True, metavar="FILE", help="the cycle record")
    p.add_argument(
        "--cards",
        nargs="+",
        action="append",
        default=[],
        metavar="PATH",
        help="scorecards, or folders of scorecards; repeatable",
    )
    p.add_argument(
        "--repo",
        type=Path,
        default=ROOT,
        help="the repository the ancestry is verified in (default: this one)",
    )
    p.add_argument("--out", type=Path, metavar="FILE", help="also write the JSON here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    cards = [path for group in args.cards for path in group]
    result = run(args.cycle, cards, args.repo)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return EXIT[result["decision"]]


if __name__ == "__main__":
    sys.exit(main())
