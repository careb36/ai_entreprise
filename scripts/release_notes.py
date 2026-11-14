#!/usr/bin/env python3
"""Read and cut CHANGELOG.md, the source of a GitHub Release's notes (issue #512).

Adapted from AixleHQ/flow (https://github.com/AixleHQ/flow, commit f9efa22),
``lib/changelog.rb`` and ``bin/changelog``, Copyright 2026 Dualboot Partners,
LLC, licensed under the Apache License 2.0. Ported from Ruby to Python with the
standard library only, since ``.github/workflows/release.yml`` runs it on the
runner's bare ``python3``. See NOTICE.

CHANGELOG.md is in Keep a Changelog form: a preamble, ``## [Unreleased]``,
then ``## [X.Y.Z] - YYYY-MM-DD`` sections newest first. Any other ``## ``
heading (this repository's ``## Versioning``) ends the section before it.

    python3 scripts/release_notes.py notes v1.2.0       # the [1.2.0] section
    python3 scripts/release_notes.py notes v1.2.0-rc.1  # [Unreleased]: a pre-release
    python3 scripts/release_notes.py notes              # [Unreleased]
    python3 scripts/release_notes.py release 1.2.0      # move [Unreleased] into [1.2.0]

A pre-release has no section of its own: cutting one would empty
``[Unreleased]`` before the release it previews, so its notes are
``[Unreleased]`` as of the tagged commit, as in Flow.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "CHANGELOG.md"
UNRELEASED = "Unreleased"
VERSION = re.compile(r"\A\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
_HEADING = re.compile(r"^## ", re.MULTILINE)
_LINK_REFERENCE = re.compile(r"^\[[^\]]+\]: \S+$")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


class ChangelogError(Exception):
    """The changelog cannot answer the request; the message says why."""


def section_for(ref: str) -> str:
    """The changelog section a tag or version reads its notes from.

    ``v1.2.0`` and ``1.2.0`` read ``[1.2.0]``; a pre-release (``v1.2.0-rc.1``)
    reads ``[Unreleased]``; ``Unreleased`` (any case) or nothing reads it too.
    """
    ref = ref.strip()
    if not ref or ref.lower() == UNRELEASED.lower():
        return UNRELEASED
    version = ref.removeprefix("v")
    if not VERSION.match(version):
        raise ChangelogError(f"{ref!r} is not a vX.Y.Z or vX.Y.Z-pre release tag")
    return UNRELEASED if "-" in version else version


def _sections(text: str) -> list[tuple[str, str]]:
    """Each ``## `` heading line with the body up to the next one."""
    chunks = _HEADING.split(text)
    out = []
    for chunk in chunks[1:]:
        title, _, body = chunk.partition("\n")
        out.append((f"## {title.rstrip()}", body))
    return out


def _clean(body: str) -> str:
    """The body without the link references and rules that close it."""
    lines = body.strip().splitlines()
    while lines and (
        not lines[-1].strip()
        or _RULE.match(lines[-1].strip())
        or _LINK_REFERENCE.match(lines[-1].strip())
    ):
        lines.pop()
    return "\n".join(lines).strip()


def released_versions(text: str) -> list[str]:
    """The versions with a section of their own, newest first."""
    found = []
    for title, _ in _sections(text):
        match = re.match(r"\A## \[(\d[^\]]*)\]", title)
        if match:
            found.append(match.group(1))
    return found


def notes(text: str, section: str) -> str:
    """The body of ``## [section]``, stripped; an error if absent or empty."""
    heading = f"## [{section}]"
    for title, body in _sections(text):
        if title == heading or title.startswith(f"{heading} "):
            cleaned = _clean(body)
            if not cleaned:
                raise ChangelogError(f"CHANGELOG.md's {heading} section is empty")
            return cleaned
    raise ChangelogError(f"CHANGELOG.md has no {heading} section")


def release(text: str, version: str, *, date: str) -> str:
    """Move everything under ``[Unreleased]`` into a dated ``[version]``.

    The link references at the end are left as they are; the person cutting
    the release updates them in the same pull request.
    """
    if not VERSION.match(version) or "-" in version:
        raise ChangelogError(
            f"{version!r} is not an X.Y.Z version (a pre-release gets no section)"
        )
    if version in released_versions(text):
        raise ChangelogError(f"CHANGELOG.md already has a [{version}] section")
    notes(text, UNRELEASED)  # raises when [Unreleased] is missing or empty
    marker = f"## [{UNRELEASED}]\n"
    if marker not in text:
        raise ChangelogError(
            f"CHANGELOG.md has no {marker.strip()} heading on a line of its own"
        )
    return text.replace(marker, f"{marker}\n## [{version}] - {date}\n", 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    sub = parser.add_subparsers(dest="command", required=True)
    p_notes = sub.add_parser(
        "notes", help="print a section, as the GitHub Release shows it"
    )
    p_notes.add_argument(
        "ref", nargs="?", default=UNRELEASED, help="a tag, a version or Unreleased"
    )
    p_release = sub.add_parser("release", help="move [Unreleased] into a dated section")
    p_release.add_argument("version")
    p_release.add_argument("--date", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text(encoding="utf-8")
        if args.command == "notes":
            print(notes(text, section_for(args.ref)))
        else:
            version = args.version.removeprefix("v")
            args.changelog.write_text(
                release(text, version, date=args.date), encoding="utf-8"
            )
            print(f"CHANGELOG.md: [Unreleased] moved into [{version}] - {args.date}")
    except (ChangelogError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
