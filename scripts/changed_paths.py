#!/usr/bin/env python3
"""The ``changes.yml`` outputs a pull request's files match.

``.github/workflows/changes.yml`` holds the one mapping from a path filter to
a CI job, and passes it to this script unchanged; the script lists the files
the pull request changes (``git diff --name-only <base>...<head>``) and prints
one ``<filter>=true`` or ``<filter>=false`` line per filter, which the step
appends to ``$GITHUB_OUTPUT``. The mapping therefore stays in the workflow,
where its comments explain each path, and lives nowhere else.

The filters arrive in the ``FILTERS`` environment variable (a YAML block
scalar), or in ``--filters-file``. Only the shape the workflow uses is read,
without a YAML parser: a ``name:`` line, then one ``- 'glob'`` line per glob.
Anything else stops the script, since a misread filter would run too few jobs.

``**`` crosses folders and ``*`` does not, as in the filters this replaces:
``backend/**`` matches ``backend/app/main.py``, ``scripts/*`` matches
``scripts/check.sh`` but not ``scripts/e2e/run.sh``. A leading dot matches,
which runs more than less. A rename counts as both its old and its new path
(``--no-renames``), so a file moved out of a filter's folder still runs the
job before the move lands.

Exit status: 0 when the filters were read and printed, 2 when git could not
diff the two commits, in which case none is printed. The step fails either
way and a caller runs its job, since a filter that could not run must run too
much, never too little.

    python3 scripts/changed_paths.py --base "$BASE_SHA" --head "$HEAD_SHA"
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILTERS_ENV = "FILTERS"

_KEY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*):\s*$")
_ITEM = re.compile(r"^\s*-\s+(.+?)\s*$")
_QUOTES = ("'", '"')


class FilterError(ValueError):
    """The filter text is not the shape ``changes.yml`` writes."""


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A filter glob as a regex: ``**`` spans folders, ``*`` does not."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_filters(text: str) -> dict[str, list[str]]:
    """``{filter: [glob, ...]}`` from the block scalar ``changes.yml`` passes."""
    filters: dict[str, list[str]] = {}
    current: list[str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A filter name starts at the left margin; an indented one is a nested
        # mapping (the action this replaces took ``paths:`` there), not a filter.
        if line == stripped and (key := _KEY.match(stripped)):
            name = key.group(1)
            if name in filters:
                raise FilterError(f"line {number}: {name!r} is declared twice")
            current = filters[name] = []
            continue
        if (item := _ITEM.match(line)) is not None and current is not None:
            value = item.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
                value = value[1:-1]
            current.append(value)
            continue
        raise FilterError(
            f"line {number}: expected 'name:' or \"- 'glob'\", found {line!r}"
        )
    if not filters:
        raise FilterError("no filter found")
    empty = [name for name, globs in filters.items() if not globs]
    if empty:
        raise FilterError("filter without a glob: " + ", ".join(empty))
    return filters


def matching(filters: dict[str, list[str]], path: str) -> list[str]:
    """The filters whose globs match ``path``."""
    return [
        name
        for name, globs in filters.items()
        if any(glob_regex(glob).match(path) for glob in globs)
    ]


def outputs(filters: dict[str, list[str]], files: list[str]) -> dict[str, bool]:
    """Each filter, and whether any of ``files`` matches it."""
    hit = {name for path in files for name in matching(filters, path)}
    return {name: name in hit for name in filters}


def changed_files(base: str, head: str, repo: Path = ROOT) -> list[str]:
    """The files ``base...head`` changes, both sides of a rename.

    The three-dot form diffs the pull request against its merge base, so
    commits pushed to the base branch meanwhile are not read as changes of
    the pull request.
    """
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the pull request's base sha")
    parser.add_argument("--head", required=True, help="the pull request's head sha")
    parser.add_argument(
        "--filters-file",
        type=Path,
        help=f"read the filters here, not from ${DEFAULT_FILTERS_ENV}",
    )
    parser.add_argument(
        "--filters-env",
        default=DEFAULT_FILTERS_ENV,
        help=f"the variable holding the filters (default: {DEFAULT_FILTERS_ENV})",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=ROOT,
        help="the git repository to diff (default: this one)",
    )
    args = parser.parse_args(argv)

    if args.filters_file is None:
        text = os.environ.get(args.filters_env)
        if text is None:
            parser.error(f"${args.filters_env} is unset and --filters-file was not given")
    else:
        text = args.filters_file.read_text(encoding="utf-8")
    try:
        filters = parse_filters(text)
    except FilterError as exc:
        print(f"changed_paths: cannot read the filters: {exc}", file=sys.stderr)
        return 2

    try:
        files = changed_files(args.base, args.head, args.repo)
    except subprocess.CalledProcessError as exc:
        reason = (exc.stderr or "").strip() or f"exit status {exc.returncode}"
        print(
            f"changed_paths: git diff {args.base}...{args.head} failed: {reason}",
            file=sys.stderr,
        )
        return 2

    for name, hit in outputs(filters, files).items():
        print(f"{name}={'true' if hit else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
