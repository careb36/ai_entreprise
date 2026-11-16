#!/usr/bin/env python3
"""Whether a pull request that changes a protected path has a code owner's approval.

``.github/workflows/protected-paths.yml`` runs this script (PR 4 of
``docs/design/rsi-loop.md``, §3.3 layer 4, REQ-12 and REQ-14). It decides from
three observations, none of which the pull request's author writes:

- the changed paths, from git (``git diff --no-renames --name-only
  <base>...<head>``, as ``scripts/changed_paths.py`` lists them), never from
  the body, a label or a comment;
- the protected list, ``contracts.protected_paths`` with the evidence's tests
  from ``docs/architecture/contexts.json``, and the owners, from
  ``.github/CODEOWNERS``, all read under ``--root``: the workflow checks out
  the *base* commit there and runs this script from it, so a pull request
  cannot weaken the list, the owners or this check for its own run;
- the reviews, read from GitHub's API with the job's token.

The decision: with no protected path changed, pass. Otherwise every matched
path needs an approval by one of its code owners (the last CODEOWNERS line
that matches it, as GitHub reads the file) whose latest decisive review
(approved, changes requested or dismissed; a comment changes nothing) is an
approval of the head sha. An approval of an older commit does not count, nor
one by a reviewer who is not that path's owner. GitHub refuses an author's
approval of their own pull request; the script ignores one too, which changes
nothing while GitHub forbids it. A team owner (``@org/team``) counts when the
API says the reviewer is an active member; a token that may not read the team
counts as not a member, and the summary says so.

The matched paths, each path's owners, the approving reviewer and sha, and the
decision are written to ``$GITHUB_STEP_SUMMARY`` (or ``--summary``) and to
standard output.

Exit status: 0 passes, 1 refuses (a protected path without an owner's
approval of the head), 2 could not decide (git, the API or a file could not be
read), which fails the check: a check that could not run must refuse, never
pass.

    python3 -I scripts/protected_paths_check.py --root "$GITHUB_WORKSPACE" \\
        --base "$BASE_SHA" --head "$HEAD_SHA" --pr "$PR_NUMBER" \\
        --repository "$GITHUB_REPOSITORY" --author "$PR_AUTHOR"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
MODULE = "backend/app/services/contracts/protected_paths.py"
CONTEXTS = "docs/architecture/contexts.json"
CODEOWNERS = ".github/CODEOWNERS"
DEFAULT_API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 30
DECISIVE = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")

PASS, REFUSED, UNDECIDED = 0, 1, 2

# True: an active member; False: not a member; None: the token may not say.
TeamCheck = Callable[[str, str, str], "bool | None"]


class CheckError(RuntimeError):
    """An input the decision needs could not be read."""


# --- the list and the diff -----------------------------------------------------------


def load_protected(root: Path) -> ModuleType:
    """``contracts.protected_paths`` as it is under ``root`` (the base commit)."""
    path = root / MODULE
    if not path.is_file():
        raise CheckError(f"{MODULE} is missing under {root}")
    spec = importlib.util.spec_from_file_location("protected_paths_base", path)
    if spec is None or spec.loader is None:
        raise CheckError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protected_patterns(module: ModuleType, root: Path) -> tuple[str, ...]:
    """The list, with the evidence's tests from the test map under ``root``."""
    try:
        data = json.loads((root / CONTEXTS).read_text(encoding="utf-8"))
        test_files = data["tests"]["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError(f"cannot read the test map in {CONTEXTS}: {exc}") from exc
    return tuple(module.protected_patterns(test_files))


def changed_files(base: str, head: str, repo: Path) -> list[str]:
    """The files ``base...head`` changes, both sides of a rename."""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        reason = (getattr(exc, "stderr", "") or "").strip() or str(exc)
        raise CheckError(f"git diff {base}...{head} failed: {reason}") from exc
    return [path for path in result.stdout.split("\0") if path]


# --- CODEOWNERS ----------------------------------------------------------------------


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    """``[(pattern, owners)]`` in file order; a line without owners keeps ``[]``."""
    rules = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rules.append((pattern, owners))
    return rules


def _glob(body: str) -> str:
    out = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return "".join(out)


def codeowners_regex(pattern: str) -> re.Pattern[str]:
    """A CODEOWNERS pattern as GitHub reads it.

    ``*`` matches everything; a leading or inner ``/`` anchors to the root,
    otherwise the pattern matches at any depth; a trailing ``/`` matches what
    the folder holds; a last segment without a wildcard also matches a folder's
    contents (``/docs`` covers ``docs/x``), one with a wildcard does not
    (``docs/*`` leaves ``docs/a/b`` out).
    """
    if pattern in ("*", "/*", "**", "/**"):
        return re.compile(".*")
    folder = pattern.endswith("/")
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    prefix = "" if anchored else "(?:.*/)?"
    if folder:
        suffix = "/.*"
    elif re.search(r"[*?]", body.rsplit("/", 1)[-1]):
        suffix = ""
    else:
        suffix = "(?:/.*)?"
    return re.compile(prefix + _glob(body) + suffix)


def owners_of(rules: Sequence[tuple[str, list[str]]], path: str) -> list[str]:
    """The owners of ``path``: the last matching line wins, even an ownerless one."""
    owners: list[str] = []
    for pattern, line_owners in rules:
        if codeowners_regex(pattern).fullmatch(path):
            owners = line_owners
    return owners


# --- the reviews ---------------------------------------------------------------------


class Review(NamedTuple):
    login: str
    state: str
    commit_id: str


def parse_reviews(raw: Iterable[dict]) -> list[Review]:
    """The API's reviews, in its (chronological) order; a deleted user is skipped."""
    reviews = []
    for item in raw:
        user = item.get("user") or {}
        login = user.get("login")
        if login:
            reviews.append(
                Review(
                    login, str(item.get("state", "")), str(item.get("commit_id", ""))
                )
            )
    return reviews


def approvals_of_head(
    reviews: Sequence[Review], head: str, author: str
) -> list[Review]:
    """The reviewers whose latest decisive review approves ``head``, author excluded."""
    latest: dict[str, Review] = {}
    for review in reviews:
        if review.state in DECISIVE:
            latest[review.login.lower()] = review
    return [
        review
        for login, review in latest.items()
        if review.state == "APPROVED"
        and review.commit_id == head
        and login != (author or "").lower()
    ]


def owns(
    login: str, owners: Sequence[str], team_check: TeamCheck | None, notes: list[str]
) -> bool:
    """Whether ``login`` is one of ``owners`` (a user, or an active team member)."""
    for owner in owners:
        if not owner.startswith("@"):
            continue  # an email owner cannot be told from a login
        name = owner[1:]
        if "/" not in name:
            if name.lower() == login.lower():
                return True
            continue
        org, team = name.split("/", 1)
        member = team_check(org, team, login) if team_check else None
        if member:
            return True
        if member is None:
            note = f"could not read whether @{login} is in {owner}; counted as not a member"
            if note not in notes:
                notes.append(note)
    return False


# --- the decision --------------------------------------------------------------------


class PathDecision(NamedTuple):
    path: str
    pattern: str
    owners: list[str]
    approved_by: Review | None


class Decision(NamedTuple):
    passed: bool
    changed: int
    paths: list[PathDecision]
    notes: list[str]


def decide(
    changed: Sequence[str],
    patterns: Sequence[str],
    module: ModuleType,
    rules: Sequence[tuple[str, list[str]]],
    reviews: Sequence[Review] | Callable[[], Sequence[Review]],
    head: str,
    author: str,
    team_check: TeamCheck | None = None,
) -> Decision:
    """The decision; ``reviews`` may be a callable, read only when a path matched."""
    matched = module.matches(changed, patterns)
    if not matched:
        return Decision(True, len(changed), [], [])
    notes: list[str] = []
    approvals = approvals_of_head(
        reviews() if callable(reviews) else reviews, head, author
    )
    paths = []
    for match in matched:
        owners = owners_of(rules, match.path)
        if not owners:
            notes.append(f"{match.path} has no owner in {CODEOWNERS}")
        approved_by = next(
            (r for r in approvals if owns(r.login, owners, team_check, notes)), None
        )
        paths.append(PathDecision(match.path, match.pattern, owners, approved_by))
    passed = all(p.approved_by is not None for p in paths)
    return Decision(passed, len(changed), paths, notes)


def render(decision: Decision, base: str, head: str) -> str:
    """The job summary, in Markdown."""
    lines = ["## Protected paths", ""]
    lines.append(
        f"Diff `{base[:12]}...{head[:12]}` (head `{head}`): "
        f"{decision.changed} changed path(s), {len(decision.paths)} protected."
    )
    lines.append("")
    if decision.paths:
        lines += [
            "| Path | Pattern | Owners | Approved on the head by |",
            "|---|---|---|---|",
        ]
        for p in decision.paths:
            who = (
                f"@{p.approved_by.login} at `{p.approved_by.commit_id}`"
                if p.approved_by
                else "nobody"
            )
            owners = " ".join(p.owners) or "none"
            lines.append(f"| `{p.path}` | `{p.pattern}` | {owners} | {who} |")
        lines.append("")
    for note in decision.notes:
        lines.append(f"- {note}")
    if decision.notes:
        lines.append("")
    if not decision.paths:
        verdict = "**Pass**: no protected path changed."
    elif decision.passed:
        verdict = (
            "**Pass**: a code owner approved every protected path on the head sha."
        )
    else:
        verdict = (
            "**Refused**: a protected path changed without a code owner's approval "
            "of the head sha. An approval of an earlier commit does not count; "
            "this check runs again when a review is submitted or dismissed."
        )
    lines.append(verdict)
    return "\n".join(lines) + "\n"


# --- GitHub's API --------------------------------------------------------------------


def _get(url: str, token: str) -> tuple[int, bytes]:
    if not url.startswith("https://"):
        raise CheckError(f"refusing a non-https API URL: {url}")
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError) as exc:
        raise CheckError(f"GET {url} failed: {exc}") from exc


def fetch_reviews(api: str, repository: str, number: int, token: str) -> list[dict]:
    """Every review of the pull request, page by page."""
    found: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{api}/repos/{repository}/pulls/{number}/reviews"
            f"?per_page={PER_PAGE}&page={page}"
        )
        status, body = _get(url, token)
        if status != 200:
            raise CheckError(f"GET {url} answered {status}")
        try:
            batch = json.loads(body)
        except ValueError as exc:
            raise CheckError(f"GET {url} did not answer JSON") from exc
        if not isinstance(batch, list):
            raise CheckError(f"GET {url} did not answer a list")
        found.extend(batch)
        if len(batch) < PER_PAGE:
            return found
    raise CheckError(f"more than {MAX_PAGES * PER_PAGE} reviews")


def team_checker(api: str, token: str) -> TeamCheck:
    def check(org: str, team: str, login: str) -> bool | None:
        quote = urllib.parse.quote
        url = f"{api}/orgs/{quote(org)}/teams/{quote(team)}/memberships/{quote(login)}"
        status, body = _get(url, token)
        if status == 404:
            return False
        if status != 200:
            return None
        try:
            return json.loads(body).get("state") == "active"
        except (ValueError, AttributeError):
            return None

    return check


# --- the command ---------------------------------------------------------------------


def _write_summary(text: str, target: str | None) -> None:
    print(text, end="")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the pull request's base sha")
    parser.add_argument("--head", required=True, help="the pull request's head sha")
    parser.add_argument("--pr", type=int, help="the pull request's number")
    parser.add_argument("--repository", help="owner/name, for the API")
    parser.add_argument("--author", default="", help="the pull request's author")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the base commit's tree: the list, the test map and CODEOWNERS",
    )
    parser.add_argument(
        "--repo", type=Path, help="the git repository to diff (default: --root)"
    )
    parser.add_argument(
        "--reviews-file",
        type=Path,
        help="read the reviews' JSON here instead of from the API",
    )
    parser.add_argument(
        "--summary",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="append the summary here (default: $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    api = os.environ.get("GITHUB_API_URL", DEFAULT_API).rstrip("/")
    token = os.environ.get("GITHUB_TOKEN", "")

    def reviews() -> list[Review]:
        if args.reviews_file is not None:
            try:
                raw = json.loads(args.reviews_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CheckError(f"cannot read {args.reviews_file}: {exc}") from exc
        else:
            if args.pr is None or not args.repository:
                raise CheckError("--pr and --repository are needed to read the reviews")
            raw = fetch_reviews(api, args.repository, args.pr, token)
        return parse_reviews(raw)

    try:
        module = load_protected(root)
        patterns = protected_patterns(module, root)
        try:
            rules = parse_codeowners((root / CODEOWNERS).read_text(encoding="utf-8"))
        except OSError as exc:
            raise CheckError(f"cannot read {CODEOWNERS}: {exc}") from exc
        changed = changed_files(args.base, args.head, args.repo or root)
        decision = decide(
            changed,
            patterns,
            module,
            rules,
            reviews,
            args.head,
            args.author,
            None if args.reviews_file else team_checker(api, token),
        )
    except CheckError as exc:
        _write_summary(
            f"## Protected paths\n\n**Refused**: the check could not decide: {exc}\n",
            args.summary,
        )
        return UNDECIDED
    _write_summary(render(decision, args.base, args.head), args.summary)
    return PASS if decision.passed else REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
