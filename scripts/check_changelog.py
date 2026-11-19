#!/usr/bin/env python3
"""Check the changelog fragments and the instructions about them (issue #731).

A pull request records its change as ``changelog.d/<issue>-<slug>.md``, not
as a line in ``CHANGELOG.md`` (``changelog.d/README.md``). This check fails:

- on a fragment with a bad file name, an unknown section on its first line,
  an empty entry or a heading inside the entry (the rules are
  ``scripts/release_notes.py``'s, which folds the fragments at release);
- when ``CONTRIBUTING.md`` or ``.github/PULL_REQUEST_TEMPLATE.md`` still asks
  for an entry under ``[Unreleased]`` in ``CHANGELOG.md``, or does not name
  ``changelog.d/``.

    python3 scripts/check_changelog.py            # the repository
    python3 scripts/check_changelog.py --root DIR # another tree, for the tests

It runs in ``scripts/check.sh`` (quick and full) and in the backend tests
(``backend/tests/test_check_changelog.py``). Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# The documents that tell a contributor how to record a change.
INSTRUCTIONS = ("CONTRIBUTING.md", ".github/PULL_REQUEST_TEMPLATE.md")
# The old rule, in any word order: a line naming CHANGELOG.md and [Unreleased]
# with a verb that asks for an edit.
_EDIT_VERB = re.compile(
    r"\b(?:add|adds|added|adding|edit|edits|edited|write|writes|update|updates)\b",
    re.IGNORECASE,
)


def _stale(line: str) -> bool:
    return (
        "CHANGELOG.md" in line
        and "[Unreleased]" in line
        and _EDIT_VERB.search(line) is not None
    )


def _release_notes():
    """``scripts/release_notes.py``, loaded by path (``python3 -I`` keeps the
    script's folder off ``sys.path``)."""
    spec = importlib.util.spec_from_file_location(
        "release_notes", HERE / "release_notes.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instruction_problems(root: Path) -> list[str]:
    """Each place an instruction still asks for a CHANGELOG.md edit."""
    problems = []
    for rel in INSTRUCTIONS:
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if _stale(line.replace("`", "")):
                problems.append(
                    f"{rel}:{number}: still asks for a line under [Unreleased] "
                    "in CHANGELOG.md; ask for a fragment in changelog.d/ instead"
                )
        if "changelog.d/" not in text:
            problems.append(
                f"{rel}: does not name changelog.d/, where a change is recorded"
            )
    return problems


def problems(root: Path) -> list[str]:
    """Everything wrong under *root*; empty when the check passes."""
    release_notes = _release_notes()
    found = list(release_notes.fragment_problems(root / release_notes.FRAGMENTS))
    found += instruction_problems(root)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    found = problems(args.root)
    if found:
        print("check_changelog: failed", file=sys.stderr)
        for problem in found:
            print(f"  {problem}", file=sys.stderr)
        return 1
    release_notes = _release_notes()
    count = len(release_notes.read_fragments(args.root / release_notes.FRAGMENTS))
    print(f"check_changelog: ok ({count} fragment(s), instructions current)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
