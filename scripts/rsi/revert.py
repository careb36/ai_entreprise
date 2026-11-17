#!/usr/bin/env python3
"""Open the revert pull request of a cycle the watch saw regress (REQ-19).

    python3 scripts/rsi/revert.py --cycle rsi/cycles/<cycle id>.json \\
        [--cards DIR_OR_FILE ...] [--repo DIR] [--base main] [--remote origin]

Runs the same watch as ``scripts/rsi/watch.py`` (which it imports) over the
same cards; unless the watch answers ``regressed``, nothing happens (exit 0).
On a regression it reverts the cycle's ``merged_sha`` on a branch
``rsi/revert-<cycle id>`` cut from ``<remote>/<base>`` in a worktree of its own
(removed on every exit), so the checkout it runs in is never moved and one
pull request reverts exactly one cycle, however many regressed in one night;
it pushes the branch and opens a pull request naming the cycle,
with the numbers and the reasons, through the gh API with the workflow's token
(``GH_TOKEN``, never printed). **It never merges anything: a person reviews and
merges the revert** (``docs/OPERATIONS.md``, "A revert pull request opened").

A revert that does not apply cleanly is aborted and left to a person (exit 2,
nothing pushed); a revert pull request already open for the cycle is reported
and not opened twice (exit 0), and when gh cannot say whether one is open
nothing is opened (exit 2). The git and gh commands go through an injectable
runner, so the tests run them without a network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "scripts" / "rsi",):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
import watch  # scripts/rsi/watch.py, by path: the decision this acts on

Runner = Callable[..., subprocess.CompletedProcess]


def _default_runner(
    cmd: list[str], cwd: Path | None = None, input_text: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, input=input_text, capture_output=True, text=True, check=False
    )


def branch_name(record: dict[str, Any]) -> str:
    return f"rsi/revert-{record['cycle_id']}"


def title(record: dict[str, Any]) -> str:
    return (
        f"revert: cycle {record['cycle_id']} regressed the nightly bench "
        f"({record['merged_sha'][:12]})"
    )


def body(record: dict[str, Any], result: dict[str, Any]) -> str:
    """The pull request's body: the cycle, the nights and the numbers."""
    lines = [
        f"The watch after the merge of cycle **{record['cycle_id']}**"
        " (`docs/design/rsi-loop.md` §1.6 and §7 step 8, REQ-19) found a nightly"
        " real-model run on `main` that regressed against the cycle's baseline B"
        " beyond both the threshold of decision 4 and B's own spread.",
        "",
        "| | |",
        "|---|---|",
        f"| Cycle | `{record['cycle_id']}` |",
        f"| Merged | `{record['merged_sha']}` at {record['merged_at']} |",
    ]
    if record.get("baseline_sha"):
        lines.append(f"| Baseline B | `{record['baseline_sha']}` |")
    if record.get("pull_request"):
        lines.append(f"| Its pull request | #{record['pull_request']} |")
    lines += [
        f"| Graders | `{record['graders_sha']}` |",
        f"| Nights ok | {result.get('nights_ok', 0)} of {result.get('nights_required', watch.NIGHTS_REQUIRED)} |",
        "",
        "## Nights",
        "",
        "| Night | Commit | Decision |",
        "|---|---|---|",
    ]
    regressions: list[str] = []
    for night in result.get("nights") or []:
        for card in night.get("cards") or []:
            lines.append(
                f"| {night['night']} | `{str(card.get('commit'))[:12]}` "
                f"| {card.get('decision')} |"
            )
            if card.get("decision") == watch.REGRESSED:
                regressions += [
                    f"- night {night['night']}: {reason['detail']}"
                    for reason in card.get("reasons") or []
                    if reason.get("outcome") == watch.REGRESSED
                ]
    if regressions:
        lines += ["", "## Why (the numbers)", ""] + regressions
    lines += [
        "",
        "This reverts the cycle's merge commit. **A person reviews and merges"
        " this pull request; nothing here merges it.** Opened by"
        " `scripts/rsi/revert.py` from `scripts/rsi/watch.py`'s observations.",
    ]
    return "\n".join(lines) + "\n"


def _merge_flag(runner: Runner, repo: Path, sha: str) -> list[str]:
    """``-m 1`` when *sha* is a merge commit (its first parent is main's)."""
    done = runner(["git", "-C", str(repo), "rev-list", "--parents", "-n", "1", sha])
    fields = (done.stdout or "").split()
    return ["-m", "1"] if done.returncode == 0 and len(fields) > 2 else []


class LookupFailed(RuntimeError):
    """gh could not say whether a revert pull request is already open."""


def _open_pr_already(runner: Runner, repo: Path, branch: str) -> str | None:
    """The URL of an open revert pull request of *branch*, when there is one.

    Raises ``LookupFailed`` when gh does not answer: not knowing is not "none
    open", and a second revert of one cycle is not opened on a guess.
    """
    done = runner(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ],
        cwd=repo,
    )
    if done.returncode != 0:
        raise LookupFailed((done.stderr or done.stdout or "").strip())
    try:
        found = json.loads(done.stdout or "[]")
    except ValueError as error:
        raise LookupFailed(f"gh answered no JSON: {error}") from error
    if not isinstance(found, list):
        raise LookupFailed("gh answered no list of pull requests")
    if found:
        return str(found[0].get("url") or branch)
    return None


def open_revert(
    record: dict[str, Any],
    result: dict[str, Any],
    repo: Path,
    runner: Runner = _default_runner,
    *,
    base: str = "main",
    remote: str = "origin",
) -> int:
    """Push the revert branch and open the pull request; the exit status."""
    branch = branch_name(record)
    try:
        existing = _open_pr_already(runner, repo, branch)
    except LookupFailed as error:
        print(
            f"could not tell whether a revert pull request of {branch} is open; "
            f"nothing opened ({error})",
            file=sys.stderr,
        )
        return 2
    if existing is not None:
        print(f"a revert pull request is already open: {existing}")
        return 0
    sha = record["merged_sha"]
    start = f"{remote}/{base}"
    if not watch.commit_exists(repo, sha) or not watch.has_ancestor(repo, sha, start):
        print(
            f"the merged sha {sha[:12]} is not on {start} in the repository at {repo}",
            file=sys.stderr,
        )
        return 2
    flag = _merge_flag(runner, repo, sha)
    with tempfile.TemporaryDirectory(prefix="rsi-revert-") as scratch:
        tree = Path(scratch) / "tree"
        added = runner(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(tree), start]
        )
        if added.returncode != 0:
            print(
                f"the branch {branch} could not be created from {start}: "
                f"{added.stderr.strip() or added.stdout.strip()}",
                file=sys.stderr,
            )
            return 2
        try:
            return _revert_and_open(
                record, result, repo, tree, runner, sha, flag, base, remote
            )
        finally:
            runner(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)])


def _revert_and_open(
    record: dict[str, Any],
    result: dict[str, Any],
    repo: Path,
    tree: Path,
    runner: Runner,
    sha: str,
    flag: list[str],
    base: str,
    remote: str,
) -> int:
    """In the worktree *tree*: revert, push, open the pull request."""
    branch = branch_name(record)
    done = runner(["git", "-C", str(tree), "revert", "--no-edit", *flag, sha], cwd=tree)
    if done.returncode != 0:
        runner(["git", "-C", str(tree), "revert", "--abort"], cwd=tree)
        print(
            f"the revert of {sha[:12]} does not apply cleanly; a person must "
            f"prepare it ({done.stderr.strip() or done.stdout.strip()})",
            file=sys.stderr,
        )
        return 2
    pushed = runner(["git", "-C", str(tree), "push", "-u", remote, branch], cwd=tree)
    if pushed.returncode != 0:
        print(
            f"the branch {branch} could not be pushed: "
            f"{pushed.stderr.strip() or pushed.stdout.strip()}",
            file=sys.stderr,
        )
        return 2
    created = runner(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title(record),
            "--body-file",
            "-",
        ],
        cwd=repo,
        input_text=body(record, result),
    )
    if created.returncode != 0:
        print(
            f"the pull request could not be opened: "
            f"{created.stderr.strip() or created.stdout.strip()}",
            file=sys.stderr,
        )
        return 2
    print(
        (created.stdout or "").strip()
        or f"the revert pull request was opened on {branch}"
    )
    return 0


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
    p.add_argument("--repo", type=Path, default=ROOT)
    p.add_argument("--base", default="main")
    p.add_argument("--remote", default="origin")
    return p


def main(argv: list[str] | None = None, runner: Runner = _default_runner) -> int:
    args = parser().parse_args(argv)
    cards = [path for group in args.cards for path in group]
    # The watch trusts the same tip the revert is cut from.
    result = watch.run(args.cycle, cards, args.repo, f"{args.remote}/{args.base}")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["decision"] != watch.REGRESSED:
        print(f"the watch answered {result['decision']}: no revert pull request")
        return watch.EXIT[result["decision"]]
    record = json.loads(Path(args.cycle).read_text(encoding="utf-8"))
    return open_revert(
        record, result, args.repo, runner, base=args.base, remote=args.remote
    )


if __name__ == "__main__":
    sys.exit(main())
