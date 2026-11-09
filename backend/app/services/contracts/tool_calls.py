"""The tool-call observation's shape shared by the tools and the evidence.

The task worker records each tool call as an observation with a ``server``
field; the built-in workspace tools record ``WORKSPACE_SERVER`` and the
research tools ``RESEARCH_SERVER`` with their tool names, and the evidence
(``context_read``, ``sources``) tells their calls apart by them (ADR 0013).
``summarize`` is the one line the Verifier and the task worker write for a
run's MCP tool calls (moved out of ``mcp_tools``, which re-exports it).
"""

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
