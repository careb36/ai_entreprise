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

The assistant messages (the calls, and any provider's signature on them)
are never changed; only the content of the old ``tool`` messages.
"""

from __future__ import annotations

from typing import Any

PREFIX = (
    "[Earlier result, summarized to save space; call the tool again for all of it] "
)


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
            return (
                f"read {observation.get('path') or args.get('path')} lines "
                f"{observation.get('first_line')}–{observation.get('last_line')} of "
                f"{observation.get('lines')}{_hash(observation.get('sha256'))}"
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


def compress(
    history: list[dict[str, Any]],
    results: list[tuple[int, int, dict[str, Any]]],
    current_round: int,
    keep: int,
) -> int:
    """Replace, in place, the ``tool`` messages of rounds older than the last
    *keep* before *current_round* with their summary line. *results* holds
    ``(index in history, round, observation)`` for each tool message.
    Returns how many characters were taken out."""
    if keep <= 0:
        return 0
    saved = 0
    for index, round_no, observation in results:
        if round_no > current_round - 1 - keep:
            continue
        message = history[index]
        content = str(message.get("content") or "")
        if content.startswith(PREFIX):
            continue
        line = PREFIX + summary_line(observation)
        if len(line) < len(content):
            history[index] = {**message, "content": line}
            saved += len(content) - len(line)
    return saved
