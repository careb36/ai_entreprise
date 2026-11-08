"""What the model sees of each workspace tool's reply.

From SWE-agent's agent-computer interface (MIT): a read says which window of
the file it shows and how to page on; a search or a file lookup over its
bound says how many results there are and asks to refine, instead of cutting
the list silently; an empty answer says it is empty ("the command finished
with no output") rather than being an empty string, which models misread.

Kept apart from ``developer_tools`` (the loop, the record, the wiring) so each
file stays under the repository's size limit. Text only: the observation the
toolbox records is built there.
"""

from __future__ import annotations

from typing import Any

from services import context_read

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


def read_text(path: str, reply: dict[str, Any]) -> str:
    """The window of numbered lines, headed with where it sits in the file."""
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
    body = context_read.redact_secrets(path, str(reply.get("text") or ""))
    out = [f"[{path}: lines {first}–{last} of {of}]"]
    if first > 1:
        back = max(1, first - (last - first + 1))
        out.append(f"[lines 1–{first - 1} above; start_line={back} goes back]")
    out.append(body)
    if last < total or reply.get("lines_indexed_to_byte"):
        out.append(
            f"[{total - last} more lines below; read on with start_line={last + 1}]"
            if last < total
            else "[the rest of the file is beyond what is read; search it instead]"
        )
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


def tests_text(exit_code: Any, output: str, max_chars: int) -> str:
    return f"exit status {exit_code}\n{output[-max_chars:] if output else NO_OUTPUT}"


def edit_text(reply: dict[str, Any]) -> str:
    path = str(reply.get("path") or "")
    checked = reply.get("checked") or "no syntax check for this file type"
    body = context_read.redact_secrets(path, str(reply.get("text") or ""))
    return (
        f"Applied to {path} (checked: {checked}); it has {reply.get('lines')} lines "
        f"now. Lines {reply.get('first_line')}–{reply.get('last_line')}:\n{body}"
    )
