"""Tool demand: the tools a sprint's agents asked for and did not have.

Pure, so the retrospective (``services/learning/retrospective.py``) counts it
from the records it reads, deterministically, and a test counts it from
fixtures. Every input is something the platform observed itself, never an
agent's report (``docs/POSITIONING.md``):

- an ``unknown tool``: a call of a tool not offered, as the toolbox answered
  it (``reason`` and ``requested`` on the call's observation, from
  ``mcp_tools.unknown_tool_note``'s path);
- a final answer that was only a tool call written as text: the loop's own
  reading (``tool_loop.not_a_deliverable``) on a run, or the worker's text on
  the task's retry or block (``TOOL_CALL_AS_TEXT``);
- a refused call: ``refused`` on the observation (a fetch to a host not
  allowed, a ``run_tests`` command not run as asked, a record outside the
  turn's scope) or on its ``check_script`` (a check script that may not run);
- a run that spent its tool rounds (``rounds_exhausted`` on its evidence).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

UNKNOWN_TOOL = "unknown tool"
# The words of ``mcp_tools.TOOL_CALL_AS_TEXT`` (a test keeps the two equal).
TOOL_CALL_AS_TEXT = "the model's last answer was a tool call written as text"
SEP = "__"


def _name(observation: dict[str, Any]) -> str:
    server, tool = observation.get("server") or "", observation.get("tool") or ""
    return f"{server}{SEP}{tool}" if server else str(tool)


def count(
    observations: Iterable[Any],
    runs: Iterable[Any] = (),
    notes: Iterable[Any] = (),
) -> dict[str, Any]:
    """The demand in *observations* (tool calls), *runs* (each run's tool-loop
    record: ``rounds_exhausted``, ``not_a_deliverable``) and *notes* (texts the
    platform wrote on the tasks). Names are sorted, so the same records always
    give the same answer."""
    unknown: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if observation.get("reason") == UNKNOWN_TOOL:
            asked = observation.get("requested") or _name(observation)
            unknown[str(asked)[:100] or "(no name)"] += 1
        script = observation.get("check_script")
        if observation.get("refused") or (
            isinstance(script, dict) and script.get("refused")
        ):
            refused[_name(observation)] += 1
    typed, exhausted = 0, 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        typed += 1 if run.get("not_a_deliverable") else 0
        exhausted += 1 if run.get("rounds_exhausted") is True else 0
    typed += sum(1 for text in notes if TOOL_CALL_AS_TEXT in str(text or ""))
    return {
        "unknown_tools": dict(sorted(unknown.items())),
        "unknown_tool_calls": sum(unknown.values()),
        "typed_tool_calls": typed,
        "refused": dict(sorted(refused.items())),
        "refused_calls": sum(refused.values()),
        "rounds_exhausted": exhausted,
    }


def _by_name(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} x{n}" for name, n in counts.items())


def summary_line(demand: dict[str, Any]) -> str:
    """The retrospective's line for *demand*, labelled as observations."""
    unknown, refused = demand.get("unknown_tools") or {}, demand.get("refused") or {}
    parts = [
        f"{demand.get('unknown_tool_calls', 0)} unknown tool name(s) asked"
        + (f" ({_by_name(unknown)})" if unknown else ""),
        f"{demand.get('typed_tool_calls', 0)} final answer(s) that were a "
        + "tool call written as text",
        f"{demand.get('refused_calls', 0)} refused call(s)"
        + (f" ({_by_name(refused)})" if refused else ""),
        f"{demand.get('rounds_exhausted', 0)} run(s) that spent their tool rounds",
    ]
    return "- Tool demand (observed by the platform): " + "; ".join(parts)
