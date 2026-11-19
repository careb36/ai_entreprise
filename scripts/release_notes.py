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
    python3 scripts/release_notes.py unreleased         # [Unreleased], the same
    python3 scripts/release_notes.py collect            # fold changelog.d/ into [Unreleased]
    python3 scripts/release_notes.py release 1.2.0      # collect, then [Unreleased] into [1.2.0]

``cut`` is another name for ``release``.

A pre-release has no section of its own: cutting one would empty
``[Unreleased]`` before the release it previews, so its notes are
``[Unreleased]`` as of the tagged commit, as in Flow.

Changelog fragments (issue #731). A pull request does not write into
``CHANGELOG.md``, where every merge made every other open pull request
conflict; it adds ``changelog.d/<issue>-<slug>.md``, whose first line is a
Keep a Changelog section (``Added``, ``Changed``, ``Deprecated``,
``Removed``, ``Fixed``, ``Security``) and whose body is the entry as it would
read under that section. ``collect`` folds them into ``[Unreleased]`` and
deletes them, and ``release`` collects first. ``[Unreleased]`` is always read
with the fragments folded in (``notes``, ``unreleased``), so a pre-release's
notes leave none out. The design follows towncrier (news fragments in a
directory, collected at release) and changesets (one file per change); no code
is taken from either.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "CHANGELOG.md"
FRAGMENTS = "changelog.d"
UNRELEASED = "Unreleased"
# Keep a Changelog 1.1.0's sections, in its order.
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
FRAGMENT_NAME = re.compile(r"\A[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")
# The files of changelog.d/ that are not fragments.
NOT_FRAGMENTS = frozenset({"README.md", ".gitkeep"})
VERSION = re.compile(r"\A\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
_HEADING = re.compile(r"^## ", re.MULTILINE)
_LINK_REFERENCE = re.compile(r"^\[[^\]]+\]: \S+$")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}(?:\s|$)")


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


# --- fragments (issue #731) ------------------------------------------------------


class Fragment(NamedTuple):
    name: str
    section: str
    body: str


def parse_fragment(name: str, text: str) -> Fragment:
    """One fragment, or a ChangelogError naming the file and the reason."""
    where = f"{FRAGMENTS}/{name}"
    if not FRAGMENT_NAME.match(name):
        raise ChangelogError(
            f"{where}: the name is not <issue>-<slug>.md (the issue number, "
            "a hyphen, then lowercase words joined by hyphens)"
        )
    first, _, rest = text.lstrip("﻿").partition("\n")
    section = first.strip()
    if section not in SECTIONS:
        raise ChangelogError(
            f"{where}: the first line is {section!r}, an unknown section "
            f"(one of {', '.join(SECTIONS)})"
        )
    body = rest.strip()
    if not body:
        raise ChangelogError(f"{where}: the fragment is empty (no {section} entry)")
    if any(_MARKDOWN_HEADING.match(line) for line in body.splitlines()):
        raise ChangelogError(
            f"{where}: the entry has a heading line; the first line is the "
            "section and the rest is the entry"
        )
    return Fragment(name, section, body)


def _fragment_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.name not in NOT_FRAGMENTS)


def fragment_problems(directory: Path) -> list[str]:
    """Why each file of *directory* is no valid fragment; empty when all are."""
    problems = []
    for path in _fragment_paths(directory):
        try:
            parse_fragment(path.name, path.read_text(encoding="utf-8"))
        except ChangelogError as exc:
            problems.append(str(exc))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{FRAGMENTS}/{path.name}: unreadable ({exc})")
    return problems


def read_fragments(directory: Path) -> list[Fragment]:
    """Every fragment, in section order and then by file name.

    A bad fragment raises a ChangelogError that names every bad one, so
    nothing is folded while one is wrong.
    """
    problems = fragment_problems(directory)
    if problems:
        raise ChangelogError("bad changelog fragments:\n  " + "\n  ".join(problems))
    fragments = [
        parse_fragment(p.name, p.read_text(encoding="utf-8"))
        for p in _fragment_paths(directory)
    ]
    return sorted(fragments, key=lambda f: (SECTIONS.index(f.section), f.name))


def fold(text: str, fragments: list[Fragment]) -> str:
    """*text* with each fragment's entry under its section of ``[Unreleased]``.

    In a section the fragments come first, in file-name order, above the
    entries already there; a missing section is added where Keep a Changelog
    puts it among the ones present (after them all when none comes later). An
    entry already under ``[Unreleased]`` is not added again, so folding twice
    changes nothing.
    """
    marker = f"## [{UNRELEASED}]\n"
    start = text.find(marker)
    if start < 0:
        raise ChangelogError(
            f"CHANGELOG.md has no {marker.strip()} heading on a line of its own"
        )
    start += len(marker)
    following = _HEADING.search(text, start)
    end = following.start() if following else len(text)
    lines = text[start:end].splitlines(keepends=True)
    for section in SECTIONS:
        present = "\n" + "".join(lines)
        entries = [
            f.body
            for f in fragments
            if f.section == section and f"\n{f.body}\n" not in present
        ]
        if entries:
            lines = _add_to_section(lines, section, entries)
    return text[:start] + "".join(lines) + text[end:]


def _add_to_section(lines: list[str], section: str, entries: list[str]) -> list[str]:
    """The lines of ``[Unreleased]`` with *entries* at the top of *section*."""
    block = [f"{entry}\n\n" for entry in entries]
    headings = [
        (i, line[4:].strip()) for i, line in enumerate(lines) if line.startswith("### ")
    ]
    for i, title in headings:
        if title == section:
            at = i + 1
            while at < len(lines) and not lines[at].strip():
                at += 1
            return [*lines[: i + 1], "\n", *block, *lines[at:]]
    rank = SECTIONS.index(section)
    for i, title in headings:
        if title in SECTIONS and SECTIONS.index(title) > rank:
            return [*lines[:i], f"### {section}\n", "\n", *block, *lines[i:]]
    # After everything in [Unreleased], before the blank lines and rule closing it.
    at = len(lines)
    while at and (not lines[at - 1].strip() or _RULE.match(lines[at - 1].strip())):
        at -= 1
    block[-1] = block[-1][:-1]
    return [*lines[:at], "\n", f"### {section}\n", "\n", *block, *lines[at:]]


def unreleased(text: str, fragments: list[Fragment]) -> str:
    """``[Unreleased]`` as it reads once the fragments are collected."""
    return notes(fold(text, fragments), UNRELEASED)


def collect(changelog: Path, directory: Path) -> list[str]:
    """Fold the fragments into *changelog*, delete them, and return their names.

    Every fragment is read and checked before anything is written, and the
    changelog is written before any fragment is deleted, so a run that stops
    half-way is finished by running it again.
    """
    fragments = read_fragments(directory)
    if not fragments:
        return []
    text = changelog.read_text(encoding="utf-8")
    folded = fold(text, fragments)
    if folded != text:
        changelog.write_text(folded, encoding="utf-8")
    for fragment in fragments:
        (directory / fragment.name).unlink()
    return [f.name for f in fragments]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--changelog", type=Path, default=CHANGELOG)
    parser.add_argument(
        "--fragments",
        type=Path,
        default=None,
        help=f"the fragments' folder (default: {FRAGMENTS}/ beside the changelog)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_notes = sub.add_parser(
        "notes", help="print a section, as the GitHub Release shows it"
    )
    p_notes.add_argument(
        "ref", nargs="?", default=UNRELEASED, help="a tag, a version or Unreleased"
    )
    sub.add_parser("unreleased", help="print [Unreleased] with the fragments folded in")
    sub.add_parser("collect", help=f"fold {FRAGMENTS}/ into [Unreleased] and delete it")
    p_release = sub.add_parser(
        "release",
        aliases=["cut"],
        help="collect, then move [Unreleased] into a dated section",
    )
    p_release.add_argument("version")
    p_release.add_argument("--date", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)
    directory = args.fragments or args.changelog.parent / FRAGMENTS

    try:
        if args.command == "collect":
            names = collect(args.changelog, directory)
            print(f"CHANGELOG.md: {len(names)} fragment(s) folded into [Unreleased]")
            for name in names:
                print(f"  {FRAGMENTS}/{name}")
            return 0
        text = args.changelog.read_text(encoding="utf-8")
        if args.command == "unreleased":
            print(unreleased(text, read_fragments(directory)))
        elif args.command == "notes":
            section = section_for(args.ref)
            if section == UNRELEASED:
                text = fold(text, read_fragments(directory))
            print(notes(text, section))
        else:
            version = args.version.removeprefix("v")
            fragments = read_fragments(directory)
            # Checked before collecting, so a refused release changes nothing.
            cut = release(fold(text, fragments), version, date=args.date)
            names = collect(args.changelog, directory)
            args.changelog.write_text(cut, encoding="utf-8")
            print(
                f"CHANGELOG.md: {len(names)} fragment(s) collected, "
                f"[Unreleased] moved into [{version}] - {args.date}"
            )
    except (ChangelogError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
