"""MCP tools in a task: what the model may call, the bounded loop, the record.

A ``Toolbox`` holds the company's servers a role may use. ``schemas`` lists
their tools in the OpenAI ``tools`` shape, each named ``<server>__<tool>`` so
two servers may both offer ``search``; a server that cannot be reached is
left out and named in ``unavailable``, and the task goes on without it.

``chat_with_tools`` runs the loop: ask the model, run every tool call it
makes, give it the results, ask again, at most ``max_rounds`` times; the last
request carries no tools, so the loop always ends in an answer. Each call is
recorded as an observation the backend made itself — which tool, with which
arguments, whether it erred, how long it took, the sha256 of what came back
— and the Verifier shows them (``tools_used``); a tool without a read-only
annotation raises the delivery's risk tier. What the model says it learned
from a tool stays a claim. Tool results are masked for the server's own
secrets and cut at ``result_max_chars`` before the model sees them. A failed
call's observation keeps the start of the error text it got (``error``,
secret-looking lines redacted), and the answer carries ``tool_loop``: the
rounds used, the limit, whether it was reached, and ``not_a_deliverable``
when the final answer is only a tool call typed out as text.

A server the operator does not allow (``mcp_config.start_refusal``: a stdio
command off ``MCP_STDIO_ALLOWED_COMMANDS``, a URL that is not public https)
is never started: ``load`` names it in ``unavailable`` with the reason. Every
start of a stdio server (its discovery and each call, since a connection
lasts one exchange) is audited before it happens through ``audit``; without
a way to audit, a stdio server is not started.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from config import settings
from database import engine as default_engine
from sqlmodel import Session

from services.contracts.tool_calls import (
    summarize,  # noqa: F401 - re-exported
    written_as_text,
)
from services.evidence.context_read import redact_secrets
from services.tools import mcp_client, mcp_config, tool_history

logger = logging.getLogger("fabrique-task-worker")

TOOL_NAME_SEP = "__"
ROUNDS_EXHAUSTED_NOTE = (
    "The tool budget for this task is spent; answer now with the complete "
    "deliverable from what you already know."
)
# How many rounds before the end the budget note warns that it is closing.
ROUNDS_CLOSING = 2


def round_note(round_no: int, rounds: int) -> str:
    """The line the last tool result of a round ends with: where the model is
    in its tool budget. Without it the model never knew it had one, read on
    until the budget was spent and answered from compressed memory (every
    run of sprints 2 to 5 used all 15 rounds)."""
    left = rounds - round_no
    if left <= 0:
        return (
            f"{tool_history.ROUND_NOTE}{round_no} of {rounds}: this was the last one; "
            "answer now with the complete deliverable.]"
        )
    if left <= ROUNDS_CLOSING:
        return (
            f"{tool_history.ROUND_NOTE}{round_no} of {rounds}: {left} left. Read only what "
            "the answer still needs, then give the complete deliverable.]"
        )
    return (
        f"{tool_history.ROUND_NOTE}{round_no} of {rounds}. When you have what you need, "
        "stop calling tools and give the complete deliverable.]"
    )


# How much of a failed call's text its observation keeps as ``error``.
ERROR_EXCERPT_CHARS = 300
TOOL_CALL_AS_TEXT = "the model's last answer was a tool call written as text"
BUDGET_SPENT = "the tool budget had run out"


def error_excerpt(text: str) -> str:
    """The start of a failed call's text, secret-looking lines redacted: what
    the backend produced or received, kept on the observation as ``error``."""
    redacted = redact_secrets("tool error", str(text or ""))
    return redacted.strip()[:ERROR_EXCERPT_CHARS]


def loop_record(
    result: dict[str, Any], used: int, rounds: int, exhausted: bool
) -> dict[str, Any]:
    """*result* with ``tool_loop``: the rounds the loop ran with tool calls,
    its limit and whether the limit was reached (the backend's own count),
    and ``not_a_deliverable`` when the final answer is only a tool call typed
    out as text (``tool_calls.written_as_text``), which the worker treats as
    an empty answer (``loop.answers.llm_failure``)."""
    record: dict[str, Any] = {
        "rounds_used": used,
        "rounds_max": rounds,
        "rounds_exhausted": exhausted,
    }
    try:
        content = result["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        content = None
    if isinstance(content, str) and written_as_text(content):
        record["not_a_deliverable"] = (
            f"{TOOL_CALL_AS_TEXT}; {BUDGET_SPENT}" if exhausted else TOOL_CALL_AS_TEXT
        )
    if isinstance(result, dict):
        result["tool_loop"] = record
    return result


# Names models use for an offered tool under its own name (``workspace__search``
# called ``grep``): read only when the call names no offered tool.
TOOL_ALIASES = {
    "grep": "search",
    "read_file": "read",
    "cat": "read",
    "ls": "list",
    "list_files": "list",
}


def resolve_tool_name(name: str, offered: Any) -> str:
    """*name* when it is offered; else the one offered tool it plainly means.

    A model often drops the server prefix (``read`` for ``workspace__read``),
    keeps it on a tool's common alias (``workspace__grep``) or both. When
    exactly one offered name has that tool part, the call goes to it instead
    of costing a round on "unknown tool"; otherwise *name* comes back
    unchanged and the caller answers with ``unknown_tool_note``.
    """
    names = [str(n) for n in offered or []]
    if name in names:
        return name
    part = str(name or "").strip().rpartition(TOOL_NAME_SEP)[2]
    part = TOOL_ALIASES.get(part, part)
    if not part:
        return name
    matches = [n for n in names if n.partition(TOOL_NAME_SEP)[2] == part]
    return matches[0] if len(matches) == 1 else name


def call_name(function: dict[str, Any], offered: Any) -> tuple[str, str]:
    """The name a tool call asked for and the offered name it goes to."""
    requested = str(function.get("name") or "")
    return requested, resolve_tool_name(requested, offered)


def unknown_tool_note(name: str, offered: Any) -> str:
    """The reply to a call of a tool that is not offered: the name it used
    (or that it named none), the nearest offered name and every offered name,
    so the model can call the right one next round."""
    names = sorted(str(n) for n in offered or [])
    asked = str(name or "").strip()[:100]
    near = [n for n in names if asked and n.endswith(TOOL_NAME_SEP + asked)]
    near = near or difflib.get_close_matches(asked, names, n=1, cutoff=0.6)
    what = f"Unknown tool {asked!r}." if asked else "The call named no tool."
    hint = f" Did you mean {near[0]}?" if near else ""
    return (
        f"{what}{hint} Call a tool by its exact name: "
        + (", ".join(names) or "none is offered")
        + "."
    )


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class Toolbox:
    """The MCP tools one task may call."""

    def __init__(
        self,
        specs: list[dict[str, Any]],
        *,
        tool_timeout: float | None = None,
        connect_timeout: float | None = None,
        result_max_chars: int | None = None,
        unavailable: dict[str, str] | None = None,
        audit: Callable[[dict[str, Any], str], None] | None = None,
    ) -> None:
        self.specs = {spec["name"]: spec for spec in specs}
        self._audit = audit
        self.tool_timeout = tool_timeout or settings.MCP_TOOL_TIMEOUT_SECONDS
        self.connect_timeout = connect_timeout or settings.MCP_CONNECT_TIMEOUT_SECONDS
        self.result_max_chars = result_max_chars or settings.MCP_RESULT_MAX_CHARS
        self.tools: dict[str, dict[str, Any]] = {}
        self.unavailable: dict[str, str] = dict(unavailable or {})
        self._loop: mcp_client.Loop | None = None
        self._discovered = False

    @classmethod
    def load(
        cls, company_id: int | None, role: str | None, *, engine: Any = None
    ) -> Toolbox | None:
        """The toolbox for a role in a company, or ``None`` when it has no servers."""
        if company_id is None:
            return None
        engine = engine or default_engine
        actor = f"agent:{role or 'unknown'}"
        specs: list[dict[str, Any]] = []
        refused: dict[str, str] = {}
        try:
            with Session(engine) as session:
                for row in mcp_config.servers_for_role(session, company_id, role):
                    reason = mcp_config.start_refusal(row)
                    if reason:
                        refused[row.name] = reason[:300]
                        logger.warning(
                            "MCP server %s not started: %s", row.name, reason
                        )
                        # The refused start is in the audit trail, as the
                        # tools endpoint records it.
                        with Session(engine) as audit_session:
                            mcp_config.audit_start(
                                audit_session,
                                row,
                                company_id,
                                actor,
                                "discovery",
                                refused=reason,
                            )
                        continue
                    specs.append(mcp_config.connection_spec(row))
        except Exception as exc:  # noqa: BLE001 - A broken configuration never blocks the task.
            logger.warning(
                "MCP servers unavailable for company=%s: %s", company_id, exc
            )
            return None
        if not specs:
            return None

        def audit(spec: dict[str, Any], purpose: str) -> None:
            with Session(engine) as session:
                mcp_config.audit_start(session, spec, company_id, actor, purpose)

        return cls(specs, unavailable=refused, audit=audit)

    def _starting(self, spec: dict[str, Any], purpose: str) -> None:
        """Audit a stdio server's start before it happens; refuse without an audit."""
        if spec.get("transport") != "stdio":
            return
        if self._audit is None:
            raise mcp_client.McpPolicyError(
                "a stdio server is started only when its start can be audited"
            )
        self._audit(spec, purpose)

    def _run(self, coro, timeout: float):
        if self._loop is None:
            self._loop = mcp_client.Loop()
        return self._loop.run(coro, timeout)

    def discover(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        for name, spec in self.specs.items():
            try:
                self._starting(spec, "discovery")
                tools = self._run(mcp_client.list_tools(spec), self.connect_timeout)
            except Exception as exc:  # noqa: BLE001 - The server is left out and named.
                reason = (
                    f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                )
                self.unavailable[name] = reason[:300]
                logger.warning("MCP server %s unavailable: %s", name, reason)
                continue
            for tool in tools:
                self.tools[f"{name}{TOOL_NAME_SEP}{tool['name']}"] = {
                    **tool,
                    "server": name,
                }

    def schemas(self) -> list[dict[str, Any]]:
        """The tools in the OpenAI ``tools`` shape, in a stable order."""
        self.discover()
        return [
            {
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": tool["description"][:1024],
                    "parameters": tool["input_schema"],
                },
            }
            for full_name, tool in sorted(self.tools.items())
        ]

    def _secret_values(self, server: str) -> list[str]:
        spec = self.specs.get(server) or {}
        values = [
            *(spec.get("env") or {}).values(),
            *(spec.get("headers") or {}).values(),
        ]
        return [v for v in values if isinstance(v, str) and len(v) >= 6]

    def call(
        self, tool_call: dict[str, Any], round_no: int
    ) -> tuple[dict[str, Any], str]:
        """Run one call. Returns the observation to record and the text for the model."""
        function = tool_call.get("function") or {}
        full_name = str(function.get("name") or "")
        raw_arguments = function.get("arguments")
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            ) or {}
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
        except (ValueError, TypeError) as exc:
            arguments = {"_raw": str(raw_arguments)[:500]}
            observation = self._observation(full_name, arguments, round_no, True, 0, "")
            observation["reason"] = f"malformed arguments: {exc}"
            return observation, f"Error: malformed arguments ({exc})."
        requested = full_name
        full_name = resolve_tool_name(full_name, self.tools)
        tool = self.tools.get(full_name)
        if tool is None:
            observation = self._observation(full_name, arguments, round_no, True, 0, "")
            observation.update(reason="unknown tool", requested=requested[:200])
            return observation, unknown_tool_note(requested, self.tools)
        started = time.monotonic()
        try:
            self._starting(self.specs[tool["server"]], f"call {tool['name']}")
            result = self._run(
                mcp_client.call_tool(
                    self.specs[tool["server"]],
                    tool["name"],
                    arguments,
                    self.tool_timeout,
                ),
                self.tool_timeout + self.connect_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - The failure is the result the model sees.
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            result = {
                "text": f"Error: the tool could not be run ({reason[:300]}).",
                "is_error": True,
            }
        elapsed_ms = int((time.monotonic() - started) * 1000)
        text = result["text"]
        for value in self._secret_values(tool["server"]):
            text = text.replace(value, "***")
        observation = self._observation(
            full_name, arguments, round_no, result["is_error"], elapsed_ms, text
        )
        observation["read_only"] = bool(tool.get("read_only"))
        if len(text) > self.result_max_chars:
            observation["truncated"] = True
            text = text[: self.result_max_chars] + "\n[result cut here]"
        return observation, text

    def _observation(
        self,
        full_name: str,
        arguments: dict[str, Any],
        round_no: int,
        is_error: bool,
        elapsed_ms: int,
        text: str,
    ) -> dict[str, Any]:
        server, _, tool = full_name.partition(TOOL_NAME_SEP)
        return {
            "server": server,
            "tool": tool,
            "arguments": json.loads(json.dumps(arguments, default=str))
            if arguments
            else {},
            "round": round_no,
            "is_error": bool(is_error),
            "duration_ms": elapsed_ms,
            "result_sha256": _sha256(text),
            "result_chars": len(text),
            "truncated": False,
        }

    def close(self) -> None:
        if self._loop is not None:
            self._loop.close()
            self._loop = None


def tool_calls_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls in a proxy answer, or ``[]``."""
    try:
        calls = result["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return []
    return [c for c in calls if isinstance(c, dict)]


def chat_with_tools(
    client: Any,
    messages: list[dict[str, Any]],
    model: str,
    role: str,
    toolbox: Any,
    *,
    max_rounds: int | None = None,
    on_round: Callable[[dict[str, Any], list[dict[str, Any]]], None] | None = None,
    bu_id: int | None = None,
    exhausted_note: str = ROUNDS_EXHAUSTED_NOTE,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The model's final answer and the observations of every tool call it made.

    Without a toolbox (or with one that found no tools) this is one plain
    ``client.chat`` call, as before. ``on_round`` sees each intermediate
    answer with the calls it made, so every round is recorded as a turn.
    ``exhausted_note`` asks for the answer once the rounds are spent (QA's
    review asks for its verdict, not a deliverable).
    """
    schemas = toolbox.schemas() if toolbox is not None else []
    billing = {"bu_id": bu_id} if bu_id else {}
    if not schemas:
        return client.chat(messages=messages, model=model, role=role, **billing), []
    rounds = (
        max_rounds
        or getattr(toolbox, "max_rounds", None)
        or settings.MCP_MAX_TOOL_ROUNDS
    )
    observations: list[dict[str, Any]] = []
    history = list(messages)
    # (index in history, round, observation) of each tool result, so the old
    # ones go to the model as one line (``services/tool_history.py``).
    results: list[tuple[int, int, dict[str, Any]]] = []
    keep, budget = settings.TOOL_HISTORY_KEEP_ROUNDS, settings.TOOL_HISTORY_MAX_CHARS
    for round_no in range(1, rounds + 1):
        tool_history.compress(history, results, round_no, keep, budget)
        result = client.chat(
            messages=history, model=model, role=role, tools=schemas, **billing
        )
        calls = tool_calls_of(result)
        if not calls or result.get("error"):
            return loop_record(result, round_no - 1, rounds, False), observations
        if on_round is not None:
            on_round(result, calls)
        history.append(result["choices"][0]["message"])
        for index, call in enumerate(calls, 1):
            observation, text = toolbox.call(call, round_no)
            if observation.get("is_error") and "error" not in observation:
                observation["error"] = error_excerpt(text)
            observations.append(observation)
            results.append((len(history), round_no, observation))
            if index == len(calls):  # Appended once, never changed after.
                text = f"{text}{round_note(round_no, rounds)}"
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{len(observations)}",
                    "content": text,
                }
            )
    tool_history.compress(history, results, rounds + 1, keep, budget)
    history.append({"role": "user", "content": exhausted_note})
    final = client.chat(messages=history, model=model, role=role, **billing)
    return loop_record(final, rounds, rounds, True), observations
