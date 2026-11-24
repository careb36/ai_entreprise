"""What the model sees of each workspace tool's reply.

From SWE-agent's agent-computer interface (MIT): a read says which window of
the file it shows and how to page on; a search or a file lookup over its
bound says how many results there are and asks to refine, instead of cutting
the list silently; an empty answer says it is empty ("the command finished
with no output") rather than being an empty string, which models misread.

Kept apart from ``developer_tools`` (the loop, the record, the wiring) so each
file stays under the repository's size limit. Text, and the fields a read
adds to the observation the toolbox records (``read_fields``).

A read the model sized itself keeps its ``lines``; otherwise the toolbox asks
the daemon for ``READ_ASK_LINES`` (its ceiling) and ``read_text`` keeps as many
whole lines as fit the model's result budget (issue #766: 601 of 1063 reads
stopped at the old 100-line default and the same files were read again). A
daemon older than ``lines`` ignores it and answers its 100.
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence import context_read

NO_OUTPUT = "(the command finished with no output)"


def list_text(reply: dict[str, Any]) -> str:
    lines = [str(e) for e in reply.get("entries") or []]
    if not lines:
        return (
            "(the folder holds no files: hidden files and dependency or build "
            "folders are not listed)"
        )
    if reply.get("truncated"):
        lines.append("[listing cut here; list a sub-folder to see the rest]")
    return "\n".join(lines)


# What a read asks the daemon for when the model named no ``lines``: the
# daemon's READ_LINES_CEILING, still bounded by its READ_MAX_BYTES.
READ_ASK_LINES = 400
_SHOWN = re.compile(r": lines (\d+)–(\d+) of [^\]]*\]$")


def read_fields(reply: dict[str, Any], text: str) -> dict[str, Any]:
    """The read's observation fields; ``last_line`` is the last line the model
    was shown (``read_text`` may stop before the daemon's)."""
    shown = _SHOWN.search(text.split("\n", 1)[0])
    return {
        "path": reply.get("path"),
        "sha256": reply.get("sha256"),
        "bytes": reply.get("bytes"),
        "lines": reply.get("lines"),
        "first_line": reply.get("first_line"),
        "last_line": int(shown.group(2)) if shown else reply.get("last_line"),
        "file_truncated": bool(reply.get("truncated")),
    }


# Room kept for the header and the continuation lines around a window.
WINDOW_FRAME_CHARS = 240


def read_text(path: str, reply: dict[str, Any], max_chars: int | None = None) -> str:
    """The window of numbered lines, headed with where it sits in the file.

    With *max_chars* (the toolbox's result budget) the window is cut at whole
    lines before the range is stated, so the header names the lines shown and
    the continuation points at the first one left out."""
    if reply.get("binary"):
        return f"(binary file, {reply.get('bytes')} bytes: not shown)"
    total = int(reply.get("lines") or 0)
    if not total:
        return f"(the file {path} is empty)"
    first = int(reply.get("first_line") or 1)
    last = int(reply.get("last_line") or first - 1)
    # Past the part of a huge file the daemon indexes, the count is a floor.
    of = f"at least {total}" if reply.get("lines_indexed_to_byte") else str(total)
    if last < first:
        return (
            f"[{path}: start_line {first} is past the {total} lines read of this "
            "large file; search it instead]"
        )
    span = last - first + 1
    rows = context_read.redact_secrets(path, str(reply.get("text") or "")).split("\n")
    if max_chars:
        room, kept = max(max_chars - WINDOW_FRAME_CHARS, 80), []
        for row in rows:
            if sum(len(r) + 1 for r in kept) + len(row) + 1 > room:
                break
            kept.append(row)
        if not kept:  # One line longer than the budget: its head, said so.
            kept = [rows[0][: room - 12] + " [line cut]"]
        rows = kept
        last = first + len(rows) - 1
    out = [f"[{path}: lines {first}–{last} of {of}]"]
    if first > 1:
        out.append(
            f"[lines 1–{first - 1} above; start_line={max(1, first - span)} goes back]"
        )
    out += rows
    if last < total:
        out.append(
            f"[{total - last} more lines below; read on with start_line={last + 1}]"
        )
    elif reply.get("lines_indexed_to_byte"):
        out.append("[the rest of the file is beyond what is read; search it instead]")
    return "\n".join(out)


def search_text(pattern: str, reply: dict[str, Any]) -> str:
    """The matching lines, or how many there are when more than are shown."""
    matches = [
        context_read.redact_secrets(m.split(":", 1)[0], m)
        for m in reply.get("matches") or []
    ]
    count = int(reply.get("match_count") or len(matches))
    scanned = reply.get("files_scanned")
    tail = (
        ["[only part of the project's files were searched; narrow the path]"]
        if reply.get("files_truncated")
        else []
    )
    if not matches:
        return "\n".join(
            [f"No line matches `{pattern}` ({scanned} file(s) searched)."] + tail
        )
    if count > len(matches):
        more = "+" if reply.get("match_count_capped") else ""
        summary = (
            f"{count}{more} matches in {reply.get('files_matched')} file(s), more "
            f"than the {len(matches)} shown at most: refine the pattern or the "
            "path. Matches per file:"
        )
        out = [summary] + [f"  {name}: {n}" for name, n in reply.get("per_file") or []]
        return "\n".join(out + tail)
    return "\n".join(matches + tail)


def find_text(pattern: str, reply: dict[str, Any]) -> str:
    files = [str(f) for f in reply.get("files") or []]
    count = int(reply.get("match_count") or len(files))
    where = reply.get("path") or "."
    tail = (
        ["[only part of the project was walked; narrow the path]"]
        if reply.get("walk_truncated")
        else []
    )
    if not files:
        return "\n".join([f"No file matches `{pattern}` under {where}."] + tail)
    if count > len(files):
        summary = (
            f"{count} files match `{pattern}` under {where}, more than the "
            f"{len(files)} listed at most: refine the name or the path."
        )
        return "\n".join([summary] + tail)
    return "\n".join(files + tail)


def tests_text(
    exit_code: Any,
    output: str,
    max_chars: int,
    asked: Any = None,
    ran: str = "",
    why: str | None = None,
) -> str:
    """The run's status and output's end; first, when the command asked for was
    not the one run, which one ran instead and, for a check script that may
    not run (``check_scripts``), *why*. In sprint 6 (2026-11-23) a QA asked
    for `python scripts/check_file_sizes.py`, was answered the full suite's
    output under "exit status 0", and reported a traceback nothing produced."""
    body = f"exit status {exit_code}\n{output[-max_chars:] if output else NO_OUTPUT}"
    wanted = str(asked or "").strip()
    if not wanted or wanted == ran:
        return body
    reason = (
        f"{why}, so"
        if why
        else "this tool runs only the folder's test runner or a check script "
        "committed under scripts/ (`python3 scripts/<name>.py [args]`), so"
    )
    return (
        f"[`{wanted}` was not run: {reason} it ran `{ran}` instead. The output "
        "below is that run's. The checks every delivery gets are run by the "
        "platform after you deliver.]\n" + body
    )


def edit_text(reply: dict[str, Any]) -> str:
    path = str(reply.get("path") or "")
    checked = reply.get("checked") or "no syntax check for this file type"
    body = context_read.redact_secrets(path, str(reply.get("text") or ""))
    return (
        f"Applied to {path} (checked: {checked}); it has {reply.get('lines')} lines "
        f"now. Lines {reply.get('first_line')}–{reply.get('last_line')}:\n{body}"
    )
