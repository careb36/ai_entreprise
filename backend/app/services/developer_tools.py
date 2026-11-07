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

from services import context_read, developer_tool_schemas, mcp_tools, research_tools

logger = logging.getLogger("fabrique-task-worker")

SERVER = developer_tool_schemas.SERVER
TOOL_ROLES = {"developer", "dev", "qa", "tester"}
READ_TOOLS = ("list", "read", "search")
RUN_TESTS = developer_tool_schemas.RUN_TESTS
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


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _runs_with(command: str, runner: str) -> bool:
    """Whether *command* is *runner* itself or *runner* followed by arguments:
    ``python -m pytest_custom`` is not ``python -m pytest``."""
    return command == runner or command.startswith(runner + " ")


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
            return list(developer_tool_schemas.SCHEMAS)
        return [
            s
            for s in developer_tool_schemas.SCHEMAS
            if not s["function"]["name"].endswith(RUN_TESTS)
        ]

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
            text = context_read.redact_secrets(
                str(arguments.get("path") or ""), str(reply.get("text") or "")
            )
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
            lines = [
                context_read.redact_secrets(m.split(":", 1)[0], m)
                for m in reply.get("matches") or []
            ]
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
        runner = next((r for r in RUNNERS if _runs_with(base, r)), None)
        if (
            runner
            and _runs_with(wanted, runner)
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
    """The built-in toolboxes (the workspace's, the research one) beside the
    company's MCP tools, as one toolbox."""

    def __init__(
        self,
        workspace: WorkspaceToolbox | None,
        mcp: mcp_tools.Toolbox | None,
        research: Any = None,
    ) -> None:
        self.builtin = [box for box in (workspace, research) if box is not None]
        self.workspace = workspace
        self.mcp = mcp
        self.max_rounds = max(box.max_rounds for box in self.builtin)

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self.mcp.unavailable) if self.mcp is not None else {}

    def schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for box in self.builtin:
            schemas += box.schemas()
        return schemas + (self.mcp.schemas() if self.mcp is not None else [])

    def call(
        self, tool_call: dict[str, Any], round_no: int
    ) -> tuple[dict[str, Any], str]:
        name = str(((tool_call.get("function") or {}).get("name")) or "")
        # The built-in tools by their exact names; everything else, the MCP
        # servers' (an MCP server cannot be named ``workspace`` or
        # ``research``: the names are reserved in ``mcp_config``, so nothing
        # is shadowed).
        for box in self.builtin:
            if name in box.tools:
                return box.call(tool_call, round_no)
        if self.mcp is None:
            return self.builtin[0].call(tool_call, round_no)
        return self.mcp.call(tool_call, round_no)

    def close(self) -> None:
        for box in self.builtin:
            box.close()
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


def tool_root(
    company_id: int, item_id: int | None = None, *, engine: Any = None
) -> str | None:
    """Where the tools look: the task's own worktree once it has delivered
    there (the Git workflow; ``git_workflow.record_reply`` keeps it on the
    evidence), else the company's project. On rework the reads and the test
    run then see the previous delivery and the merged dependencies."""
    try:
        root = _bridge["resolve_root"](company_id)
    except Exception as exc:  # noqa: BLE001 - No project path, no workspace tools.
        logger.warning(
            "Could not resolve the project path for company=%s: %s", company_id, exc
        )
        return None
    if not root or item_id is None or engine is None:
        return root
    try:
        from services import delivery_gate

        verification = delivery_gate.latest_verification(engine, company_id, item_id)
    except Exception as exc:  # noqa: BLE001 - The project root is the fallback.
        logger.warning("Could not read the task's last delivery: %s", exc)
        return root
    evidence = (verification.evidence if verification is not None else None) or {}
    worktree = evidence.get("workspace_root") if evidence.get("git") else None
    return worktree if isinstance(worktree, str) and worktree else root


def toolbox_for(
    company_id: int | None,
    role: str | None,
    *,
    engine: Any = None,
    item_id: int | None = None,
) -> Any:
    """The toolbox for a task: the company's MCP tools, plus the workspace
    tools for a role that delivers files when a project path is known.
    Returns ``None`` when there is nothing to offer, as the MCP loader does."""
    mcp = mcp_tools.Toolbox.load(company_id, role, engine=engine)
    workspace = None
    if company_id is not None and offered_to(role) and "nats" in _bridge:
        root = tool_root(company_id, item_id, engine=engine)
        if root:
            workspace = WorkspaceToolbox(
                _request_from_thread, root, test_command=_detect_test_command(root)
            )
    # The research tools for the roles that decide (``services/research_tools.py``).
    research = research_tools.toolbox_for(company_id, role, engine=engine)
    if workspace is None and research is None:
        return mcp
    return CombinedToolbox(workspace, mcp, research)


def repository_section(
    company_id: int | None,
    role: str | None,
    item_id: int | None = None,
    *,
    engine: Any = None,
) -> str:
    """The prompt's "## Repository" section: the top of the project's tree,
    bounded, with a word on the tools. Empty when the tools are not offered
    or the project cannot be listed."""
    if company_id is None or not offered_to(role) or "nats" not in _bridge:
        return ""
    try:
        root = tool_root(company_id, item_id, engine=engine)
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
