"""Compressed history in the tool loop: old results as one line each.

Every round of ``mcp_tools.chat_with_tools`` sends the whole conversation
again, so a file read in the first round is paid for in every round after
it. Here the results of rounds older than the last
``TOOL_HISTORY_KEEP_ROUNDS`` are replaced, in the messages sent to the
model, by one line built from the call's observation ("read src/x.py lines
1–100 of 240 (sha256 3f2a…)", "searched 'foo': 12 matches"). The
observation itself, on the run's evidence as ``tool_calls``, is unchanged:
the tokens are saved, the evidence is not touched. The model is told it can
call the tool again for the whole result. Taken from SWE-agent's history
processors (MIT; the idea, no code), which keep the last observations whole
and collapse the older ones.

A ``workspace__read`` is the exception while it is still the latest view
of its lines (issue #766): an audit of 1063 reads found 16% re-reading the
very window that had been summarized, so an old read stays whole while all
the tool results together fit ``TOOL_HISTORY_MAX_CHARS``. Every other old
result is summarized first, and so is a read a later call made stale (an
edit, move or delete of that file) or repeated (a later read of the same
file covering its lines). Past the budget the oldest reads are summarized,
each line naming the path and lines and the call that shows them again.
Whole reads also keep the history's prefix stable for a provider's prompt
cache.

The assistant messages (the calls, and any provider's signature on them)
are never changed; only the content of the old ``tool`` messages.
"""

from __future__ import annotations

from typing import Any

# The workspace tools that change a file, so an earlier read of it is stale.
CHANGES = ("edit", "move", "delete")
PREFIX = (
    "[Earlier result, summarized to save space; call the tool again for all of it] "
)
# How the tool loop's budget note opens (``mcp_tools.round_note``): stale once
# its round is old, so an old result loses it whether or not it is summarized.
ROUND_NOTE = "\n\n[Tool round "


def _short(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hash(value: Any) -> str:
    return f" (sha256 {str(value)[:12]}…)" if value else ""


def summary_line(observation: dict[str, Any]) -> str:
    """One line for a call, from its observation alone."""
    tool = str(observation.get("tool") or "")
    server = str(observation.get("server") or "")
    args = observation.get("arguments") or {}
    if observation.get("is_error"):
        reason = observation.get("reason")
        return f"{server}.{tool} returned an error" + (
            f": {_short(reason)}" if reason else ""
        )
    if server == "workspace":
        if tool == "read":
            path, first = _path(observation), observation.get("first_line")
            last = observation.get("last_line")
            return (
                f"read {path} lines {first}–{last} of {observation.get('lines')}"
                f"{_hash(observation.get('sha256'))}; workspace__read path={path} "
                f"start_line={first} lines={_span(first, last)} shows it again"
            )
        if tool == "search":
            return f"searched '{_short(args.get('pattern'), 60)}': {observation.get('match_count')} matches"
        if tool == "find_file":
            return f"looked for files '{_short(args.get('pattern'), 60)}': {observation.get('match_count')} found"
        if tool == "list":
            return f"listed {args.get('path') or '.'}"
        if tool == "edit":
            line = (
                f"edited {observation.get('path') or args.get('path')}"
                f"{_hash(observation.get('sha256_before'))} → {str(observation.get('sha256_after') or '')[:12]}…"
            )
            lint = observation.get("lint") or {}
            if lint.get("ran"):
                line += f"; lint exited {lint.get('exit_code')}"
            return line
        if tool == "run_tests":
            return f"ran `{_short(observation.get('command'), 60)}`: exit {observation.get('exit_code')}"
    if server == "research":
        what = observation.get("url") or observation.get("package") or args
        return f"research.{tool} {_short(what)}: status {observation.get('status')}{_hash(observation.get('sha256'))}"
    return (
        f"{server}.{tool}({_short(args, 60)}): {observation.get('result_chars')} characters"
        f"{_hash(observation.get('result_sha256'))}"
    )


def _path(observation: dict[str, Any], key: str = "path") -> str:
    args = observation.get("arguments") or {}
    return str(observation.get(key) or args.get(key) or "").removeprefix("./")


def _span(first: Any, last: Any) -> Any:
    try:
        return max(int(last) - int(first) + 1, 1)
    except (TypeError, ValueError):
        return "?"


def _is_read(observation: dict[str, Any]) -> bool:
    return (
        observation.get("server") == "workspace"
        and observation.get("tool") == "read"
        and not observation.get("is_error")
    )


def _covers(later: dict[str, Any], earlier: dict[str, Any]) -> bool:
    try:
        return int(later["first_line"]) <= int(earlier["first_line"]) and int(
            later["last_line"]
        ) >= int(earlier["last_line"])
    except (KeyError, TypeError, ValueError):
        return False


def _not_latest(results: list[tuple[int, int, dict[str, Any]]]) -> dict[int, str]:
    """The reads a later call made stale or repeated: history index → why."""
    notes: dict[int, str] = {}
    for n, (index, _round, read) in enumerate(results):
        if not _is_read(read):
            continue
        path = _path(read)
        for _index, _later_round, later in results[n + 1 :]:
            if later.get("server") != "workspace" or later.get("is_error"):
                continue
            tool = later.get("tool")
            if tool in CHANGES and path in (_path(later), _path(later, "to_path")):
                notes[index] = " (the file changed after this read)"
                break
            if tool == "read" and _path(later) == path and _covers(later, read):
                notes[index] = " (a later read shows these lines)"
                break
    return notes


def _shrink(
    history: list[dict[str, Any]], index: int, observation: dict[str, Any], note: str
) -> int:
    message = history[index]
    whole = str(message.get("content") or "")
    if whole.startswith(PREFIX):
        return 0
    content = whole.split(ROUND_NOTE, 1)[0] if ROUND_NOTE in whole else whole
    line = PREFIX + summary_line(observation) + note
    if len(line) >= len(content):
        if content != whole:  # Short enough to stay; only the stale note goes.
            history[index] = {**message, "content": content}
        return len(whole) - len(content)
    history[index] = {**message, "content": line}
    return len(whole) - len(line)


def compress(
    history: list[dict[str, Any]],
    results: list[tuple[int, int, dict[str, Any]]],
    current_round: int,
    keep: int,
    max_chars: int = 0,
) -> int:
    """Replace, in place, the ``tool`` messages of rounds older than the last
    *keep* before *current_round* with their summary line. *results* holds
    ``(index in history, round, observation)`` for each tool message.

    An old read that is still the latest view of its lines stays whole while
    every tool message together is at most *max_chars* characters (0: no
    bound); past it the oldest such reads are summarized until it fits.
    Returns how many characters were taken out."""
    if keep <= 0:
        return 0
    notes, saved, live = _not_latest(results), 0, []
    for index, round_no, observation in results:
        if round_no > current_round - 1 - keep:
            continue
        if _is_read(observation) and index not in notes:
            live.append((index, observation))
            continue
        saved += _shrink(history, index, observation, notes.get(index, ""))
    if max_chars <= 0:
        return saved
    total = sum(len(str(history[i].get("content") or "")) for i, _r, _o in results)
    for index, observation in live:
        if total <= max_chars:
            break
        cut = _shrink(history, index, observation, "")
        total, saved = total - cut, saved + cut
    return saved
