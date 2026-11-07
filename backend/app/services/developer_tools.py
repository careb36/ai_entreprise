"""The Developer's workspace tools: read the project and run its tests before delivering.

A task used to be written in one shot from a prompt, with a listing of the
project's root files as all the model saw of the code it was changing. Here
the roles that deliver files (Developer, QA) get four tools through the
workspace daemon, offered beside the company's MCP tools in the same loop
(``services/mcp_tools.chat_with_tools``):

- ``workspace__list``: the tree under a folder, bounded;
- ``workspace__read``: a file, numbered, bounded;
- ``workspace__search``: the lines matching a pattern, bounded;
- ``workspace__run_tests``: the project's test command (or a narrower one
  within the same runner), bounded output.

Every call is an observation the backend made, recorded on the run's
evidence like an MCP call (``tools_used``): tool, arguments, error, duration,
sha256 of the result. A read also records the file's sha256 and size as the
daemon read them, so the Verifier can say which of the files a delivery
modified the Developer had read first (``context_read``), and raise the risk
tier for one it had not. What the model says it learned stays a claim; its
own account of a test run is never evidence, the daemon's exit status after
the delivery is (``workspace_daemon.run_delivery_tests``).

The tools are read-only on the project. ``run_tests`` executes the project's
test command, which ``DELIVERY_TESTS_ENABLED`` already runs after every
delivery; it is offered only when that setting is on, since it runs
project code on the daemon's machine.

A "repository map" (the top of the tree) goes into the prompt of a task on
an existing project, so the model knows what to read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from config import settings

from services import mcp_tools

logger = logging.getLogger("fabrique-task-worker")

SERVER = "workspace"
TOOL_ROLES = {"developer", "dev", "qa", "tester"}
READ_TOOLS = ("list", "read", "search")
RUN_TESTS = "run_tests"
LIST_TIMEOUT_SECONDS = 20.0
READ_TIMEOUT_SECONDS = 20.0
SEARCH_TIMEOUT_SECONDS = 40.0
MAP_MAX_CHARS = 2500
# The command the model may narrow the test run to must start with the
# project's own runner: never an arbitrary shell command.
RUNNERS = (
    "python -m pytest",
    "pytest",
    "npm test",
    "npx vitest",
    "npx jest",
    "go test",
    "cargo test",
)

Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]

_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}list",
            "description": (
                "List the files and folders of the project under a path "
                "(default: the root), two levels deep unless `depth` says "
                "otherwise; folders end in `/`. Bounded; `truncated` says when."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "A folder, relative to the project root.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Levels to descend, 1 to 6 (default 2).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}read",
            "description": (
                "Read a file of the project as it is on disk, with line numbers, "
                "at most 400 lines from `start_line`. Read a file before you change it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file, relative to the project root.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to show (default 1).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}search",
            "description": (
                "Find the lines matching a regular expression (case-insensitive) "
                "in the project or under a path: `file:line: text`, at most 60 matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "A regular expression.",
                    },
                    "path": {
                        "type": "string",
                        "description": "A folder or file to search in (default: the root).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{RUN_TESTS}",
            "description": (
                "Run the project's tests as they are on disk now (your delivery "
                "is not written yet) and see the output. `command` may narrow "
                "the run within the same runner, e.g. `python -m pytest tests/test_x.py`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "A narrower test command with the project's runner (optional).",
                    },
                },
            },
        },
    },
]


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def offered_to(role: str | None) -> bool:
    """Whether a role gets the workspace tools."""
    return (
        settings.DEVELOPER_TOOLS_ENABLED
        and str(role or "").strip().lower() in TOOL_ROLES
    )


class WorkspaceToolbox:
    """The daemon's read tools (and the test run) for one task, in the
    ``mcp_tools.Toolbox`` shape: ``schemas``, ``call``, ``close``."""

    def __init__(
        self,
        requester: Requester,
        workspace_root: str,
        *,
        test_command: str | None = None,
        result_max_chars: int | None = None,
    ) -> None:
        self.requester = requester
        self.workspace_root = workspace_root
        self.test_command = test_command
        self.result_max_chars = result_max_chars or settings.MCP_RESULT_MAX_CHARS
        self.unavailable: dict[str, str] = {}
        self.max_rounds = settings.DEVELOPER_TOOL_ROUNDS

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return {
            s["function"]["name"]: {"server": SERVER, "read_only": True}
            for s in self.schemas()
        }

    def schemas(self) -> list[dict[str, Any]]:
        if self.test_command:
            return list(_SCHEMAS)
        return [s for s in _SCHEMAS if not s["function"]["name"].endswith(RUN_TESTS)]

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
            observation = self._observation(
                tool, {"_raw": str(raw)[:500]}, round_no, True, 0, ""
            )
            observation["reason"] = f"malformed arguments: {exc}"
            return observation, f"Error: malformed arguments ({exc})."
        if full_name not in self.tools:
            observation = self._observation(tool, arguments, round_no, True, 0, "")
            observation["reason"] = "unknown tool"
            return observation, mcp_tools.NO_TOOL_NOTE
        started = time.monotonic()
        extra: dict[str, Any] = {}
        try:
            text = self._run(tool, arguments, extra)
            is_error = False
        except DaemonReplyError as exc:
            text, is_error = f"Error: {str(exc)[:300]}", True
        except Exception as exc:  # noqa: BLE001 - The failure is the result the model sees.
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            text, is_error = f"Error: {reason[:300]}", True
        elapsed_ms = int((time.monotonic() - started) * 1000)
        observation = self._observation(
            tool, arguments, round_no, is_error, elapsed_ms, text
        )
        observation.update(extra)
        if len(text) > self.result_max_chars:
            observation["truncated"] = True
            text = text[: self.result_max_chars] + "\n[result cut here]"
        return observation, text

    def _run(self, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
        root = self.workspace_root
        if tool == "list":
            reply = self.requester(
                "workspace.list",
                {
                    "workspace_root": root,
                    "path": arguments.get("path"),
                    "depth": arguments.get("depth"),
                },
                LIST_TIMEOUT_SECONDS,
            )
            lines = list(reply.get("entries") or [])
            if reply.get("truncated"):
                lines.append("[listing cut here]")
            return "\n".join(lines) or "(empty)"
        if tool == "read":
            reply = self.requester(
                "workspace.read",
                {
                    "workspace_root": root,
                    "path": arguments.get("path"),
                    "start_line": arguments.get("start_line"),
                },
                READ_TIMEOUT_SECONDS,
            )
            extra.update(
                {
                    "path": reply.get("path"),
                    "sha256": reply.get("sha256"),
                    "bytes": reply.get("bytes"),
                    "lines": reply.get("lines"),
                    "file_truncated": bool(reply.get("truncated")),
                }
            )
            if reply.get("binary"):
                return f"(binary file, {reply.get('bytes')} bytes)"
            text = str(reply.get("text") or "")
            if reply.get("truncated"):
                text += f"\n[{reply.get('lines')} lines in the file; read on with start_line]"
            return text or "(empty file)"
        if tool == "search":
            reply = self.requester(
                "workspace.search",
                {
                    "workspace_root": root,
                    "pattern": arguments.get("pattern"),
                    "path": arguments.get("path"),
                },
                SEARCH_TIMEOUT_SECONDS,
            )
            lines = list(reply.get("matches") or [])
            extra["files_scanned"] = reply.get("files_scanned")
            if reply.get("truncated"):
                lines.append("[more matches not shown]")
            return "\n".join(lines) or "(no match)"
        if tool == RUN_TESTS:
            command = self._test_command(arguments.get("command"))
            extra["command"] = command
            reply = self.requester(
                "workspace.execute",
                {"workspace_root": root, "command": command},
                settings.DELIVERY_TEST_TIMEOUT_SECONDS,
            )
            exit_code = reply.get("exit_code")
            extra["exit_code"] = exit_code
            extra["timed_out"] = bool(reply.get("timed_out"))
            output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
            if reply.get("timed_out"):
                return f"The tests ran past the time limit and were stopped.\n{output[-2000:]}"
            if reply.get("error") and exit_code is None:
                raise RuntimeError(str(reply["error"]))
            return f"exit status {exit_code}\n{output[-self.result_max_chars :]}"
        raise RuntimeError("unknown tool")

    def _test_command(self, requested: Any) -> str:
        """The project's test command, or a narrower one on the same runner."""
        base = str(self.test_command or "")
        wanted = str(requested or "").strip()
        if not wanted:
            return base
        runner = next((r for r in RUNNERS if base.startswith(r)), None)
        if (
            runner
            and wanted.startswith(runner)
            and all(ch not in wanted for ch in ";&|`$><\n")
        ):
            return wanted
        return base

    def _observation(
        self,
        tool: str,
        arguments: dict[str, Any],
        round_no: int,
        is_error: bool,
        elapsed_ms: int,
        text: str,
    ) -> dict[str, Any]:
        return {
            "server": SERVER,
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
            "read_only": True,
        }

    def close(self) -> None:
        pass


class CombinedToolbox:
    """The workspace tools beside the company's MCP tools, as one toolbox."""

    def __init__(
        self, workspace: WorkspaceToolbox, mcp: mcp_tools.Toolbox | None
    ) -> None:
        self.workspace = workspace
        self.mcp = mcp
        self.max_rounds = workspace.max_rounds

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self.mcp.unavailable) if self.mcp is not None else {}

    def schemas(self) -> list[dict[str, Any]]:
        return self.workspace.schemas() + (
            self.mcp.schemas() if self.mcp is not None else []
        )

    def call(
        self, tool_call: dict[str, Any], round_no: int
    ) -> tuple[dict[str, Any], str]:
        name = str(((tool_call.get("function") or {}).get("name")) or "")
        if name.startswith(f"{SERVER}{mcp_tools.TOOL_NAME_SEP}") or self.mcp is None:
            return self.workspace.call(tool_call, round_no)
        return self.mcp.call(tool_call, round_no)

    def close(self) -> None:
        self.workspace.close()
        if self.mcp is not None:
            self.mcp.close()


# ---------------------------------------------------------------------------
# Wiring: the worker's NATS client, reached from the thread the LLM call runs in
# ---------------------------------------------------------------------------

_bridge: dict[str, Any] = {}


def bind(
    nats_client: Any,
    loop: asyncio.AbstractEventLoop,
    resolve_root: Callable[[int], str | None],
) -> None:
    """Give this module the worker's NATS client and event loop, and the
    resolver of a company's project path. Called once when the worker starts."""
    _bridge.update({"nats": nats_client, "loop": loop, "resolve_root": resolve_root})


def unbind() -> None:
    _bridge.clear()


def _request_from_thread(
    subject: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """A daemon request made from a worker thread, on the worker's loop."""
    nats_client, loop = _bridge.get("nats"), _bridge.get("loop")
    if nats_client is None or loop is None:
        raise RuntimeError("the workspace daemon is not reachable from this worker")
    future = asyncio.run_coroutine_threadsafe(
        nats_client.request(subject, payload, timeout=timeout), loop
    )
    reply = future.result(timeout + 5)
    return _checked(reply)


def _checked(reply: Any) -> dict[str, Any]:
    """The daemon's reply, or the error it carried as an exception."""
    if not isinstance(reply, dict):
        raise DaemonReplyError("the workspace daemon sent an unreadable reply")
    error = reply.get("error")
    if isinstance(error, dict):
        raise DaemonReplyError(str(error.get("message") or "workspace daemon error"))
    return reply


class DaemonReplyError(RuntimeError):
    """The daemon could not serve a tool call; the message is what the model sees."""


def _detect_test_command(workspace_root: str) -> str | None:
    """The project's test command, when the loop would run it after the delivery."""
    if not settings.DELIVERY_TESTS_ENABLED:
        return None
    from services import workspace_daemon

    loop = _bridge.get("loop")
    if loop is None:
        return None
    try:
        return asyncio.run_coroutine_threadsafe(
            workspace_daemon.detect_project_test_command(
                _bridge.get("nats"), workspace_root
            ),
            loop,
        ).result(40)
    except Exception as exc:  # noqa: BLE001 - Without a command the tool is not offered.
        logger.warning("Could not detect the test command for the tools: %s", exc)
        return None


def toolbox_for(company_id: int | None, role: str | None, *, engine: Any = None) -> Any:
    """The toolbox for a task: the company's MCP tools, plus the workspace
    tools for a role that delivers files when a project path is known.
    Returns ``None`` when there is nothing to offer, as the MCP loader does."""
    mcp = mcp_tools.Toolbox.load(company_id, role, engine=engine)
    if company_id is None or not offered_to(role) or "nats" not in _bridge:
        return mcp
    try:
        root = _bridge["resolve_root"](company_id)
    except Exception as exc:  # noqa: BLE001 - No project path, no workspace tools.
        logger.warning(
            "Could not resolve the project path for company=%s: %s", company_id, exc
        )
        root = None
    if not root:
        return mcp
    workspace = WorkspaceToolbox(
        _request_from_thread, root, test_command=_detect_test_command(root)
    )
    return CombinedToolbox(workspace, mcp)


def repository_section(company_id: int | None, role: str | None) -> str:
    """The prompt's "## Repository" section: the top of the project's tree,
    bounded, with a word on the tools. Empty when the tools are not offered
    or the project cannot be listed."""
    if company_id is None or not offered_to(role) or "nats" not in _bridge:
        return ""
    try:
        root = _bridge["resolve_root"](company_id)
        if not root:
            return ""
        reply = _request_from_thread(
            "workspace.list",
            {"workspace_root": root, "path": None, "depth": 2},
            LIST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the map.
        logger.warning("Could not list the project for company=%s: %s", company_id, exc)
        return ""
    entries = [str(e) for e in reply.get("entries") or []]
    if not entries:
        return ""
    listing = "\n".join(entries)
    if len(listing) > MAP_MAX_CHARS:
        listing = listing[:MAP_MAX_CHARS].rsplit("\n", 1)[0] + "\n[listing cut here]"
    elif reply.get("truncated"):
        listing += "\n[listing cut here]"
    return (
        "\n\n## Repository\n"
        "The project exists; the tree below is its top two levels as they are on "
        "disk now. Use the workspace tools to read the files you will change and "
        "the ones around them before you write, and to search for what you need; "
        "a file you deliver replaces the one on disk whole, so deliver it complete.\n\n"
        f"{listing}"
    )


def files_read(tool_calls: Any) -> dict[str, str | None]:
    """The files the workspace tools read in a run, with the sha256 the daemon
    reported for each (the latest read of a file wins)."""
    read: dict[str, str | None] = {}
    for call in tool_calls or []:
        if (
            isinstance(call, dict)
            and call.get("server") == SERVER
            and call.get("tool") == "read"
            and not call.get("is_error")
            and isinstance(call.get("path"), str)
        ):
            read[call["path"]] = call.get("sha256")
    return read


def check_context_read(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's ``context_read`` check: of the files the delivery
    modified (the daemon says which existed before), which the Developer had
    read first and which it had not. Informational, never the verdict; absent
    when the workspace tools were not used and nothing was modified."""
    evidence = evidence or {}
    read = files_read(evidence.get("tool_calls"))
    modified = sorted(
        w["path"]
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict)
        and w.get("status") == "written"
        and w.get("change") == "modified"
    )
    if not read and not modified:
        return None
    unread = [p for p in modified if p not in read]
    seen = [p for p in modified if p in read]
    if not modified:
        observation = f"read {len(read)} file(s); no existing file was changed"
    elif not unread:
        observation = f"every modified file was read first ({len(seen)})"
    else:
        observation = f"modified without reading first: {', '.join(unread[:5])}"
    return {
        "name": "context_read",
        "outcome": "pass",
        "observation": observation,
        "read": sorted(read),
        "modified_read": seen,
        "modified_unread": unread,
    }
