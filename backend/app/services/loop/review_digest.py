"""What QA is shown of a delivery: every file, within a fixed size.

The board keeps a deliverable comment of at most 4000 characters and the
review prompt used to show the first 3000 of it. A delivery of three files
reached QA as one file and part of the next, so QA asked for the "missing"
files, and the rework regenerated all of them: the most expensive call in the
loop, spent on content that was already written and verified.

``build`` lists every file the delivery wrote (path, lines, bytes), then an
excerpt of each one, sharing ``DIGEST_CHARS`` between them; a file that does
not fit says how many lines were left out, so a cut is never read as a
missing part. Each file keeps its ``# Artifact: <path>`` heading, which the
board and the e2e read. The full files are on disk and in the run's artifact.
The digest never exceeds its limit: a file the limit leaves no room for is
named in the index and says its excerpt was left out, so no later cut can
fall inside a file without saying so.

QA reviews a larger view of the same files (``for_review``): rebuilt from the
run's artifact, where every file is whole, within ``REVIEW_CHARS``. A digest
of 3800 characters shared by five files showed each one about 600, and QA
sent verified work back as "truncated" (sprint 3, tasks 14 and 17). The view
is rebuilt only when the artifact holds every file the board's digest lists;
otherwise (a file written through the workspace edit tool, which is not in
the answer) QA reads the board's digest as before.
"""

from __future__ import annotations

import re

from services.contracts.manifest import MANIFEST_BLOCK, parse_file_manifest

DIGEST_CHARS = 3800
REVIEW_CHARS = 24000
HEADER = "Files written"
MIN_EXCERPT_CHARS = 120
MARKER_RESERVE = 48
NOTE = (
    "\n\nThe files listed above were written to the project and checked by the "
    "Verifier. Long files are shown as excerpts by the system: a file cut here "
    "is not a missing part, so do not ask for content only because this view "
    "leaves it out. Judge whether the work meets the task."
)
EDIT_NOTE = (
    "(delivered as SEARCH/REPLACE edits to the existing file, applied and verified)"
)
_INDEX_LINE = re.compile(r"^- (\S+) \(\d+ lines, \d+ bytes\)$", re.MULTILINE)


def is_digest(text: str) -> bool:
    return (text or "").startswith(HEADER)


def _excerpt(content: str, budget: int) -> str:
    if len(content) <= budget:
        return content
    kept: list[str] = []
    used = 0
    lines = content.splitlines()
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    left = len(lines) - len(kept)
    return "\n".join(kept) + f"\n[... {left} more line(s) not shown here]"


def build(
    targets: list[dict[str, str]], prose: str = "", limit: int = DIGEST_CHARS
) -> str:
    """The digest of a delivery's files, at most about ``limit`` characters."""
    files = [
        (t.get("path") or "artifact", (t.get("content") or "").strip("\n"))
        for t in targets
    ]
    index = [f"{HEADER} ({len(files)}):"]
    for path, content in files:
        lines = content.count("\n") + 1 if content else 0
        index.append(f"- {path} ({lines} lines, {len(content.encode('utf-8'))} bytes)")
    head = "\n".join(index)
    prose = (prose or "").strip()
    if prose:
        prose = prose[:400]
        head += f"\n\nDeveloper's note: {prose}"
    headings = sum(len(f"\n\n# Artifact: {path}\n\n") for path, _ in files)
    room = limit - len(head) - headings
    # Each cut file ends with a one-line marker; keep room for it.
    per_file = room // max(1, len(files)) - MARKER_RESERVE
    sections = [head]
    # Too many files to show each: as many as fit get the minimum.
    per_file = max(per_file, MIN_EXCERPT_CHARS)
    used = len(head)
    for path, content in files:
        heading = f"# Artifact: {path}\n\n"
        excerpt = _excerpt(content, per_file)
        if used + len(heading) + len(excerpt) + 2 > limit:
            lines = content.count("\n") + 1 if content else 0
            excerpt = f"[excerpt left out for room: {lines} line(s)]"
            if used + len(heading) + len(excerpt) + 2 > limit:
                break
        sections.append(heading + excerpt)
        used += len(heading) + len(excerpt) + 2
    return "\n\n".join(sections)


def for_review(
    deliverable: str, artifact: str | None, limit: int = REVIEW_CHARS
) -> str:
    """What QA reads of a delivery: the run's whole files within *limit*, when
    the artifact holds every file the board's digest lists, else the digest."""
    if not is_digest(deliverable) or not artifact:
        return deliverable
    listed = _INDEX_LINE.findall(deliverable.split("\n\n", 1)[0])
    targets = parse_file_manifest(artifact)
    if not listed or not set(listed) <= {t["path"] for t in targets}:
        return deliverable
    shown = []
    for target in targets:
        content = target.get("content") or ""
        if "edits" in target:
            content = f"{EDIT_NOTE}\n{content}"
        shown.append({"path": target["path"], "content": content})
    prose = MANIFEST_BLOCK.sub("", artifact).strip()
    return build(shown, prose, limit=limit)
