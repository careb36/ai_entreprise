"""The tool-call observation's shape shared by the tools and the evidence.

The task worker records each tool call as an observation with a ``server``
field; the built-in workspace tools record ``WORKSPACE_SERVER`` and the
research tools ``RESEARCH_SERVER`` with their tool names, and the evidence
(``context_read``, ``sources``) tells their calls apart by them (ADR 0013).
``summarize`` is the one line the Verifier and the task worker write for a
run's MCP tool calls (moved out of ``mcp_tools``, which re-exports it).
``written_as_text`` tells an answer that is only a tool call typed out as
text, by a model that was offered no tools, from an answer.
"""

import re
from typing import Any

WORKSPACE_SERVER = "workspace"
RESEARCH_SERVER = "research"
RESEARCH_FETCH, RESEARCH_PACKAGE, RESEARCH_ADVISORIES = "fetch", "package", "advisories"


def summarize(observations: list[dict[str, Any]]) -> str:
    """One line for the Verifier: how many calls, to what, how many erred."""
    if not observations:
        return "no MCP tool calls"
    counts: dict[str, int] = {}
    for obs in observations:
        key = f"{obs.get('server')}.{obs.get('tool')}"
        counts[key] = counts.get(key, 0) + 1
    errors = sum(1 for obs in observations if obs.get("is_error"))
    named = ", ".join(
        f"{key} ×{n}" if n > 1 else key for key, n in sorted(counts.items())
    )
    line = f"{len(observations)} MCP tool call(s): {named}"
    if errors:
        line += f"; {errors} returned an error"
    return line


# Tool-call markup a model types into its text when it has no tools: the XML
# style (``<tool_calls>``, ``<invoke name=...>``, ``<function_calls>``) and the
# special tokens some models emit (``<|tool_call_begin|>``, ``<｜tool▁calls...``).
_TOOL_CALL_TEXT = re.compile(
    r"<\s*/?\s*(tool_calls?|function_calls?|invoke\s+name)\b"
    r"|<[|｜]\s*tool[_▁]?call",
    re.IGNORECASE,
)


def written_as_text(text: str) -> bool:
    """True when *text* holds a tool call typed out as text instead of an answer.

    In sprint 7 (2026-11-24) the Architect, asked for a brief without tools,
    answered only ``<tool_calls><invoke name="exec_command">find .github
    ...</invoke></tool_calls>``; that markup went to the Developer as its brief.
    """
    return bool(_TOOL_CALL_TEXT.search(text or ""))
