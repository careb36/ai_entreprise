"""The record tools (family E): read a task's history and a run's evidence.

Two read-only tools, offered as the ``board`` server beside the workspace and
research ones (``developer_tools.CombinedToolbox``, through ``attach``):

- ``board__task``: a task's history from its events (status moves, comments,
  attempts, rework requests, dependencies);
- ``board__evidence``: a run of the task: the Verifier's checks and their
  observations, the delivered files with the sha256 the backend or the daemon
  computed, the tools the run was offered, and what the producer claimed.

The scope is the backend's, never the arguments': the turn's company and, for
a task turn, the task and the tasks it depends on (``Scope``, built by the
caller from the database). A task outside it is refused with a reply that says
which tasks may be read, and the refusal is an observation (``refused``).
The records are read by functions the caller gives (``Reader``), so this
module needs no context it may not import (``services/loop/worker/
board_record.py`` reads the tasks and the verifications).

Every record keeps claims and observations apart (``docs/POSITIONING.md``):
the reader returns ``observations`` (what the platform recorded or computed)
and ``claims`` (what an agent or a person wrote), and the reply prints them
under two headings, never in one entry. The reply is redacted like a read
(``context_read.redact_secrets``) and cut at ``result_max_chars``. Each call is
an observation like a workspace read: tool, arguments, duration, the sha256 of
the reply, ``read_only`` true.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config import settings

from services.evidence.context_read import redact_secrets
from services.tools import developer_tools, mcp_tools

SERVER = "board"
TASK = "task"
EVIDENCE = "evidence"
NAMES = tuple(f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{t}" for t in (TASK, EVIDENCE))
OUTSIDE_SCOPE = "outside the turn's scope"
OBSERVATIONS_HEADING = (
    "OBSERVATIONS (recorded or computed by the platform itself, not by an agent):"
)
CLAIMS_HEADING = (
    "CLAIMS (what agents or people wrote or reported; the platform has not "
    "verified them):"
)
NOTE = (
    "You may also read the task's record with the board tools: "
    "board__task (its history: status moves, comments, attempts, rework "
    "requests, dependencies) and board__evidence (a run's Verifier checks, the "
    "delivered files with their sha256, the tools it was offered). Their "
    "replies keep the platform's observations apart from agents' claims."
)
# A reader: (company id, task id, the call's arguments) to the record, or
# None when the task is not the company's.
Reader = Callable[[int, int, dict[str, Any]], dict[str, Any] | None]

_TASK_ID = {
    "type": "integer",
    "description": "The task's id. Only this turn's task and the tasks it "
    "depends on may be read.",
}
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": NAMES[0],
            "description": (
                "A task's history, read-only: its status moves, comments, "
                "attempts, the rework requested and its dependencies. The "
                "platform's observations and the agents' claims come apart."
            ),
            "parameters": {
                "type": "object",
                "properties": {"task_id": _TASK_ID},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": NAMES[1],
            "description": (
                "A run of a task, read-only: the Verifier's checks and their "
                "observations, the delivered files with the sha256 the "
                "platform computed, the tools the run was offered, and what "
                "the producer claimed. The latest run unless `attempt` names one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": _TASK_ID,
                    "attempt": {
                        "type": "integer",
                        "description": "The run's attempt number (1, 2...); "
                        "the latest when left out.",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
]


@dataclass(frozen=True)
class Scope:
    """What a turn may read: the company's tasks, and only *task_ids* among
    them when given (a task turn: the task and its dependencies)."""

    company_id: int
    task_ids: frozenset[int] | None = None

    def allows(self, task_id: int) -> bool:
        return self.task_ids is None or task_id in self.task_ids

    def describe(self) -> str:
        if self.task_ids is None:
            return "the tasks of this company"
        return "task(s) " + ", ".join(str(t) for t in sorted(self.task_ids))


def _entry(entry: Any) -> str:
    return "- " + json.dumps(entry, ensure_ascii=False, default=str, sort_keys=True)


def render(tool: str, task_id: int, record: dict[str, Any]) -> str:
    """The reply for *record*: its subject, then its observations and its
    claims under their own headings; an empty section says so."""
    observations = record.get("observations") or []
    claims = record.get("claims") or []
    lines = [f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{tool} for task {task_id}."]
    if record.get("subject"):
        lines.append(str(record["subject"]))
    lines += ["", OBSERVATIONS_HEADING]
    lines += [_entry(o) for o in observations] or ["- none recorded"]
    lines += ["", CLAIMS_HEADING]
    lines += [_entry(c) for c in claims] or ["- none recorded"]
    if record.get("omitted"):
        lines += ["", str(record["omitted"])]
    return "\n".join(lines)


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class BoardToolbox:
    """The record tools for one turn, in the ``mcp_tools.Toolbox`` shape:
    ``tools``, ``schemas``, ``call``, ``close``, ``max_rounds``."""

    def __init__(
        self,
        scope: Scope,
        read_task: Reader,
        read_evidence: Reader,
        *,
        rounds: int,
        result_max_chars: int | None = None,
    ) -> None:
        self.scope = scope
        self.readers = {TASK: read_task, EVIDENCE: read_evidence}
        self.max_rounds = rounds
        self.result_max_chars = result_max_chars or settings.MCP_RESULT_MAX_CHARS
        self.unavailable: dict[str, str] = {}
        self.edited_files: list[dict[str, Any]] = []

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return {name: {"server": SERVER, "read_only": True} for name in NAMES}

    def schemas(self) -> list[dict[str, Any]]:
        return [dict(s) for s in SCHEMAS]

    def call(
        self, tool_call: dict[str, Any], round_no: int
    ) -> tuple[dict[str, Any], str]:
        """Run one call: the observation to record and the text the model sees."""
        function = tool_call.get("function") or {}
        full_name = str(function.get("name") or "")
        tool = full_name.partition(mcp_tools.TOOL_NAME_SEP)[2]
        raw = function.get("arguments")
        try:
            arguments = (json.loads(raw) if isinstance(raw, str) else raw) or {}
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
        except (ValueError, TypeError) as exc:
            observation = self._observation(tool, {"_raw": str(raw)[:500]}, round_no)
            observation.update(is_error=True, reason=f"malformed arguments: {exc}")
            return observation, f"Error: malformed arguments ({exc})."
        if full_name not in self.tools:
            observation = self._observation(tool, arguments, round_no)
            observation.update(
                is_error=True, reason="unknown tool", requested=full_name[:200]
            )
            return observation, mcp_tools.unknown_tool_note(full_name, self.tools)
        started = time.monotonic()
        observation = self._observation(tool, arguments, round_no)
        text = self._run(tool, arguments, observation)
        observation["duration_ms"] = int((time.monotonic() - started) * 1000)
        text = redact_secrets("board record", text)
        observation.update(result_sha256=_sha256(text), result_chars=len(text))
        if len(text) > self.result_max_chars:
            observation["truncated"] = True
            text = text[: self.result_max_chars] + "\n[result cut here]"
        return observation, text

    def _run(
        self, tool: str, arguments: dict[str, Any], observation: dict[str, Any]
    ) -> str:
        try:
            task_id = int(arguments.get("task_id"))
        except (TypeError, ValueError):
            observation.update(is_error=True, reason="no task_id")
            return "Error: give the task's id as `task_id` (an integer)."
        observation["task_id"] = task_id
        refusal = (
            f"Refused: task {task_id} is {OUTSIDE_SCOPE}. This turn may read "
            f"{self.scope.describe()}."
        )
        if not self.scope.allows(task_id):
            observation.update(is_error=True, refused=OUTSIDE_SCOPE)
            return refusal
        try:
            record = self.readers[tool](self.scope.company_id, task_id, arguments)
        except Exception as exc:  # noqa: BLE001 - The failure is the reply the model sees.
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            observation.update(is_error=True)
            return f"Error: the record could not be read ({reason[:300]})."
        if record is None:  # Not a task of this company: the same refusal.
            observation.update(is_error=True, refused=OUTSIDE_SCOPE)
            return refusal
        return render(tool, task_id, record)

    def _observation(
        self, tool: str, arguments: dict[str, Any], round_no: int
    ) -> dict[str, Any]:
        return {
            "server": SERVER,
            "tool": tool,
            "arguments": json.loads(json.dumps(arguments, default=str))
            if arguments
            else {},
            "round": round_no,
            "is_error": False,
            "duration_ms": 0,
            "result_sha256": _sha256(""),
            "result_chars": 0,
            "truncated": False,
            "read_only": True,
        }

    def close(self) -> None:
        return None


def has_board(toolbox: Any) -> bool:
    """Whether *toolbox* offers the record tools."""
    boxes = getattr(toolbox, "builtin", None) or [toolbox]
    return any(isinstance(box, BoardToolbox) for box in boxes)


def attach(toolbox: Any, board: Any) -> Any:
    """*toolbox* with *board* (the record tools, or another built-in box such
    as the research one) beside its own, as one toolbox; the added box never
    raises the turn's round limit. Either alone when the other is ``None``."""
    if board is None:
        return toolbox
    if toolbox is None:
        return board
    rounds = getattr(toolbox, "max_rounds", None)
    if isinstance(rounds, int) and rounds > 0:
        board.max_rounds = min(board.max_rounds, rounds)
    if isinstance(toolbox, developer_tools.CombinedToolbox):
        combined = toolbox
    elif isinstance(toolbox, mcp_tools.Toolbox):
        combined = developer_tools.CombinedToolbox(None, toolbox, board)
    elif isinstance(toolbox, developer_tools.WorkspaceToolbox):
        combined = developer_tools.CombinedToolbox(toolbox, None, board)
    else:  # Another built-in box (the research one).
        combined = developer_tools.CombinedToolbox(None, None, toolbox)
    if board not in combined.builtin:
        combined.builtin.append(board)
    combined.max_rounds = max(box.max_rounds for box in combined.builtin)
    return combined
