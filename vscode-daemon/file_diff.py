"""What a write changed: the unified diff of a file's bytes before and after.

The daemon reads a file just before writing it and reads it back just after
(the bytes it already hashes), so the diff is an observation of the disk,
like the sha256: never the producer's account of its own change.  The header
is git's (``diff --git a/... b/...``), so the backend reads these diffs and a
pull request's with one parser.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A file larger than this is reported as changed without a diff, and is
# hashed in chunks instead of read whole.
MAX_DIFF_SOURCE_BYTES = 512 * 1024
# One file's diff, and every diff in one reply: a NATS message holds 1 MB by
# default, and the reply also carries the per-file report.
MAX_FILE_DIFF_CHARS = 200_000
MAX_REPLY_DIFF_CHARS = 600_000


@dataclass(frozen=True)
class Previous:
    """A file as it was before a write: its sha256, and its bytes when small."""

    sha256: str | None
    data: bytes | None
    omitted: str | None = None


def read_previous(path: Path) -> Previous | None:
    """The file at *path* before a write, or ``None`` when there was none."""
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_DIFF_SOURCE_BYTES:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            return Previous(digest.hexdigest(), None, "too_large")
        data = path.read_bytes()
    except OSError:
        return Previous(None, None, "unreadable")
    return Previous(hashlib.sha256(data).hexdigest(), data)


def _text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def describe_change(
    relative: str, previous: Previous | None, after: bytes
) -> dict[str, Any]:
    """``change``, line counts and the diff of one write.

    ``change`` is ``added``, ``modified`` or ``unchanged``; ``diff_omitted``
    says why a changed file has no diff (``binary``, ``too_large`` or
    ``unreadable``).
    """
    after_sha = hashlib.sha256(after).hexdigest()
    if previous is None:
        change = "added"
    else:
        change = "unchanged" if previous.sha256 == after_sha else "modified"
    report: dict[str, Any] = {
        "change": change,
        "previous_sha256": None if previous is None else previous.sha256,
        "additions": 0,
        "deletions": 0,
        "diff": None,
        "diff_omitted": None,
    }
    if change == "unchanged":
        return report
    if previous is not None and previous.data is None:
        report["diff_omitted"] = previous.omitted
        return report
    if len(after) > MAX_DIFF_SOURCE_BYTES:
        report["diff_omitted"] = "too_large"
        return report
    old_text = "" if previous is None else _text(previous.data or b"")
    new_text = _text(after)
    if old_text is None or new_text is None:
        report["diff_omitted"] = "binary"
        return report
    hunks = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="/dev/null" if previous is None else f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="",
        )
    )
    body = hunks[2:]  # the ---/+++ lines difflib writes come first
    report["additions"] = sum(1 for line in body if line.startswith("+"))
    report["deletions"] = sum(1 for line in body if line.startswith("-"))
    header = [f"diff --git a/{relative} b/{relative}"]
    if previous is None:
        header.append("new file mode 100644")
    header += hunks[:2] if hunks else [f"--- a/{relative}", f"+++ b/{relative}"]
    diff = "\n".join(header + body) + "\n"
    if len(diff) > MAX_FILE_DIFF_CHARS:
        report["diff_omitted"] = "too_large"
    else:
        report["diff"] = diff
    return report


def cap_reply(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the diffs past ``MAX_REPLY_DIFF_CHARS``, in order, as ``reply_size``."""
    total = 0
    for result in results:
        diff = result.get("diff")
        if not diff:
            continue
        if total + len(diff) > MAX_REPLY_DIFF_CHARS:
            result["diff"] = None
            result["diff_omitted"] = "reply_size"
        else:
            total += len(diff)
    return results
