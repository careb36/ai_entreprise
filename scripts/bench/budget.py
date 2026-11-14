#!/usr/bin/env python3
"""The bench's token budget (issue #316): wait for a sprint within it, and
record on the scorecard whether the run was cut.

    budget.py wait --api URL --company N --sprint N --timeout SECONDS \\
        [--budget TOKENS] [--interval SECONDS] --pg HOST PORT USER PASSWORD DB
    budget.py check --budget TOKENS --pg HOST PORT USER PASSWORD DB

``wait`` (used by ``run.sh`` for each specification) polls the sprint until
it closes. Between polls it reads the tokens the whole run has spent so far,
from the audit log, with the reader the scorecard uses
(``readers.reported_tokens``, only turns whose usage the provider reported;
the bench's database is recreated for every run, so every company in it is
this run's). When the reported tokens pass ``--budget``, or ``--timeout``
passes first, it stops the sprint (``POST /v1/sprints/{id}/stop``) so nothing
the agents still do lands between the grade and the numbers. It prints one
JSON object: ``status`` (``completed``, ``timeout``, ``budget_exceeded``, or
``error`` when the budget could not be read, the sprint stopped then too),
``error`` and ``spent``.

``check`` (before each specification) prints what the run has spent and exits
3 when it is past the budget, 4 when it cannot be read, 0 otherwise.

Turns whose usage the provider did not report carry no count, so they cannot
be budgeted: they are counted apart (``turns_unreported``) and the card says
so, never estimated. A budget is a cap on what the provider reported, not on
what it billed.

``run_record`` and ``render_lines`` / ``skipped_rows`` give the scorecard's
run-level block (``run``, schema v3, optional) and its Markdown.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from readers import reported_tokens

COMPLETE, BUDGET_EXCEEDED = "complete", "budget_exceeded"
RUN_STATUSES = (COMPLETE, BUDGET_EXCEEDED)
SKIPPED_BUDGET = "budget"
EXIT_OVER, EXIT_UNREADABLE = 3, 4


def parse_budget(text: str | None) -> int | None:
    """``BENCH_TOKEN_BUDGET``: empty is no budget, else a positive integer."""
    text = (text or "").strip()
    if not text:
        return None
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"the token budget must be a positive integer, not {text!r}")
    return int(text)


def over_budget(spent: dict[str, int], budget: int | None) -> bool:
    """The run has passed its budget: more reported tokens than it allows."""
    return budget is not None and int(spent.get("reported", 0)) > budget


def run_spent(
    dsn: dict[str, str], reader: Callable[..., Any] = reported_tokens
) -> dict[str, int]:
    """The reported tokens and the turns of every company in the run so far."""
    tokens = reader(dsn, None)[0]
    return {
        k: int(tokens.get(k, 0))
        for k in ("reported", "turns_reported", "turns_unreported")
    }


def wait(
    *,
    status_of: Callable[[], str],
    stop: Callable[[], str],
    spent_of: Callable[[], dict[str, int]],
    timeout: float,
    budget: int | None,
    interval: float = 3.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for the sprint to close, within *timeout* and the run's *budget*.

    A completed sprint is never stopped; a budget passed while it ran is
    found by the next ``check``. Past the budget or the timeout, the sprint
    is stopped and the outcome says why."""
    started = clock()
    spent: dict[str, int] = {}
    while True:
        if status_of() == "completed":
            return {"status": "completed", "error": "", "spent": spent}
        if budget is not None:
            try:
                spent = spent_of()
            except Exception as exc:  # noqa: BLE001 - an unread budget stops the spend.
                return {
                    "status": "error",
                    "error": f"the token budget could not be read ({exc}); stopped: {stop()}",
                    "spent": spent,
                }
            if over_budget(spent, budget):
                return {
                    "status": BUDGET_EXCEEDED,
                    "error": (
                        f"the run passed its token budget ({spent['reported']} reported "
                        f"> {budget}); stopped: {stop()}"
                    ),
                    "spent": spent,
                }
        if clock() - started >= timeout:
            return {
                "status": "timeout",
                "error": f"the sprint did not close within {timeout:.0f}s; stopped: {stop()}",
                "spent": spent,
            }
        sleep(interval)


def run_record(
    entries: list[dict[str, Any]],
    *,
    budget: int | None,
    cut: bool,
    skipped: list[str],
) -> dict[str, Any]:
    """The card's ``run`` block: whether the budget cut the run, the budget,
    what the specifications that ran spent, and the ones that did not run."""
    tokens = [e.get("tokens") or {} for e in entries]
    return {
        "status": BUDGET_EXCEEDED if cut else COMPLETE,
        "budget": budget,
        "tokens_spent": sum(int(t.get("reported", 0)) for t in tokens),
        "turns_reported": sum(int(t.get("turns_reported", 0)) for t in tokens),
        "turns_unreported": sum(int(t.get("turns_unreported", 0)) for t in tokens),
        "skipped": [{"name": n, "reason": SKIPPED_BUDGET} for n in skipped],
    }


def is_cut(card: dict[str, Any]) -> bool:
    return (card.get("run") or {}).get("status") == BUDGET_EXCEEDED


def skipped_names(card: dict[str, Any]) -> list[str]:
    return [s["name"] for s in (card.get("run") or {}).get("skipped") or []]


def skipped_rows(card: dict[str, Any], columns: int = 8) -> list[str]:
    """One row of the card's table per specification the budget skipped."""
    return [
        f"| {name} | skipped: {SKIPPED_BUDGET} |" + " – |" * (columns - 2)
        for name in skipped_names(card)
    ]


def render_lines(card: dict[str, Any]) -> list[str]:
    """The run's budget, under the card's header; nothing for an older card."""
    run = card.get("run")
    if not run:
        return []
    budget = run.get("budget")
    lines = [""]
    if run["status"] == BUDGET_EXCEEDED:
        lines.append(
            f"**Run cut: token budget exceeded.** {run['tokens_spent']} reported tokens "
            f"spent against a budget of {budget}; the sprint running then was stopped "
            f"and {len(run['skipped'])} specification(s) did not run (skipped: budget, never graded)."
        )
    else:
        cap = f"a budget of {budget}" if budget is not None else "no budget"
        lines.append(f"Run complete: {run['tokens_spent']} reported tokens, {cap}.")
    if run["turns_unreported"]:
        lines.append(
            f"{run['turns_unreported']} turn(s) had no usage reported: they carry no "
            "count, so the budget could not include them."
        )
    return lines


def run_problems(card: dict[str, Any], count: Callable[[Any], bool]) -> list[str]:
    """What is wrong with the optional ``run`` block of *card*."""
    if "run" not in card:
        return []
    run = card["run"]
    if not isinstance(run, dict):
        return ["run must be an object"]
    problems = []
    if run.get("status") not in RUN_STATUSES:
        problems.append(f"run.status must be one of {', '.join(RUN_STATUSES)}")
    budget = run.get("budget")
    if budget is not None and not (count(budget) and budget > 0):
        problems.append("run.budget must be a positive integer or null")
    if run.get("status") == BUDGET_EXCEEDED and budget is None:
        problems.append("run.budget is required when the budget cut the run")
    problems.extend(
        f"run.{k} must be a count"
        for k in ("tokens_spent", "turns_reported", "turns_unreported")
        if not count(run.get(k))
    )
    skipped = run.get("skipped")
    if not isinstance(skipped, list) or not all(
        isinstance(s, dict)
        and isinstance(s.get("name"), str)
        and s.get("reason") == SKIPPED_BUDGET
        for s in skipped
    ):
        return [*problems, "run.skipped must list {name, reason: budget}"]
    if skipped and run.get("status") != BUDGET_EXCEEDED:
        problems.append("run.skipped must be empty unless the budget cut the run")
    ran = {s.get("name") for s in card.get("specs") or [] if isinstance(s, dict)}
    problems.extend(
        f"run.skipped names {s['name']}, which has an entry"
        for s in skipped
        if s["name"] in ran
    )
    return problems


# ---------------------------------------------------------------------------
# The runner's side: the API and the audit log
# ---------------------------------------------------------------------------


def _request(url: str, method: str = "GET") -> Any:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read() or b"{}")
        except ValueError:
            return {"detail": f"HTTP {exc.code}"}


def _status_of(api: str, company: int, sprint: int) -> str:
    try:
        sprints = _request(f"{api}/v1/sprints?company_id={company}")
    except (OSError, ValueError):
        return ""
    if not isinstance(sprints, list):
        return ""
    return next((s.get("status", "") for s in sprints if s.get("id") == sprint), "")


def _stop(api: str, sprint: int) -> str:
    try:
        answer = _request(f"{api}/v1/sprints/{sprint}/stop", method="POST")
    except (OSError, ValueError) as exc:
        return f"stop failed: {exc}"
    return str(answer.get("status") or answer.get("detail", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("wait", "check"):
        p = sub.add_parser(name)
        p.add_argument("--budget", default="")
        p.add_argument(
            "--pg",
            nargs=5,
            required=True,
            metavar=("HOST", "PORT", "USER", "PASSWORD", "DB"),
        )
        if name == "wait":
            p.add_argument("--api", required=True)
            p.add_argument("--company", type=int, required=True)
            p.add_argument("--sprint", type=int, required=True)
            p.add_argument("--timeout", type=float, required=True)
            p.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)
    budget = parse_budget(args.budget)
    dsn = dict(
        zip(("host", "port", "user", "password", "dbname"), args.pg, strict=True)
    )
    if args.command == "check":
        try:
            spent = run_spent(dsn)
        except Exception as exc:  # noqa: BLE001 - said, with the exit status.
            print(f"the token budget could not be read: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE
        print(json.dumps(spent))
        return EXIT_OVER if over_budget(spent, budget) else 0
    outcome = wait(
        status_of=lambda: _status_of(args.api, args.company, args.sprint),
        stop=lambda: _stop(args.api, args.sprint),
        spent_of=lambda: run_spent(dsn),
        timeout=args.timeout,
        budget=budget,
        interval=args.interval,
    )
    print(json.dumps(outcome))
    return 0


if __name__ == "__main__":
    sys.exit(main())
