"""The workspace tools: read the project and run its tests before delivering.

The step sets which families a turn gets and the role may only narrow them
(``contracts/tool_steps``): every role executing a task reads (R), runs (X),
researches (S) and edits where edits may go (W). The tools come through the
workspace daemon, beside the company's MCP tools in the same
loop (``services/mcp_tools.chat_with_tools``); the replies follow SWE-agent's
agent-computer interface (``developer_tool_replies``):

- ``workspace__list``: the tree under a folder, bounded;
- ``workspace__read``: a window of numbered lines, as many as fit the result
  budget up to 400 (or ``lines``), paged with ``start_line``;
- ``workspace__search``: the lines matching a pattern; past the bound, how many;
- ``workspace__find_file``: files by name or glob; past the bound, how many;
- ``workspace__edit``: change a line range or one SEARCH/REPLACE block of an
  existing file, applied by the daemon only if the file still parses
  (``vscode-daemon/workspace_edit.py``); offered only where the task's files
  may be touched (the task's worktree, from the first turn: ``task_worktree``;
  or a project without the Git workflow);
- ``workspace__run_tests``: the project's test command (or a narrower one
  within the same runner, ``test_commands``), or a check script committed under ``scripts/``
  that the task did not change (``check_scripts``), bounded output.

Every call is an observation the backend made, recorded on the run's
evidence like an MCP call (``tools_used``): tool, arguments, error, duration,
sha256 of the result; ``daemon_absent`` when NATS said no daemon listens (the
worker pauses the sprint, ``loop/daemon_gate.py``). A read records the file's
sha256 and size as the daemon read them, so the Verifier can say which of the
files a delivery modified the Developer had read first (``context_read``), and
raise the risk tier for one it had not. What the model says it learned stays a
claim; its account of a test run is never evidence, the daemon's exit status
after the delivery is (``delivery_checks.run_delivery_tests``).

An edit is an observation with the file's sha256 before and after, and a working
copy delivered with the answer's files (``services/workspace_edits.py``).

The other tools are read-only on the project; ``read_tools`` offers those alone
(the steps that only read: the Architect's brief, the PO's planning, the
ScrumMaster's decision, ``review_tools.for_step``). ``run_tests`` runs project code on the daemon's machine,
so only with ``DELIVERY_TESTS_ENABLED``, which runs it after each delivery anyway.

A repository map (``services/repo_map.py``: the tree's top, the definitions by
use) goes into the prompt on an existing project, so the model knows what to read.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from config import settings

from services.contracts import roles, tool_steps
from services.tools import (
    check_scripts,
    developer_tool_replies,
    developer_tool_schemas,
    edit_lint,
    mcp_tools,
    repo_map,
    research_tools,
    task_worktree,
    test_commands,
    test_runs,
    workspace_edits,
    workspace_git,
)
from services.tools.developer_tool_schemas import DELETE, MOVE, WRITE_NAMES, WRITES
from services.workspace import sandbox, stack_image
from services.workspace.workspace_daemon import no_responders

logger = logging.getLogger("fabrique-task-worker")

SERVER = developer_tool_schemas.SERVER
READ_TOOLS = ("list", "read", "search", developer_tool_schemas.FIND)
RUN_TESTS = developer_tool_schemas.RUN_TESTS
EDIT = developer_tool_schemas.EDIT
LIST_TIMEOUT_SECONDS = 20.0
READ_TIMEOUT_SECONDS = 20.0
SEARCH_TIMEOUT_SECONDS = 40.0
MAP_MAX_CHARS = 2500
Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


_runs_with = test_commands.runs_with


def offered_to(role: str | None, step: str = tool_steps.EXECUTE) -> bool:
    """Whether a turn of *step* by *role* gets the workspace tools: the step
    allows reading and the role keeps it (``contracts/tool_steps``)."""
    return settings.DEVELOPER_TOOLS_ENABLED and tool_steps.allows(
        step, roles.canonical(role), tool_steps.READ
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
        folder_tests: dict[str, dict[str, str | None]] | None = None,
        result_max_chars: int | None = None,
        sandbox: dict[str, Any] | None = None,
        sandbox_error: str | None = None,
        can_edit: bool = False,
        edit_suffixes: set[str] | None = None,
    ) -> None:
        self.requester = requester
        self.workspace_root, self.test_command = workspace_root, test_command
        # Each folder's (and the root's, under "") test and install commands
        # (``test_runs.detect``); the folders installed so far in this toolbox.
        self.folder_tests = dict(folder_tests or {})
        if test_command and test_runs.ROOT not in self.folder_tests:
            self.folder_tests[test_runs.ROOT] = {"test": test_command, "install": None}
        self.installed: set[str] = set()
        # Where its test runs execute (services/sandbox.py): agent code; or why
        # the company's sandbox could not be read (its test runs are refused).
        self.sandbox, self.sandbox_error = sandbox, sandbox_error
        self.result_max_chars = result_max_chars or settings.MCP_RESULT_MAX_CHARS
        self.unavailable: dict[str, str] = {}
        self.max_rounds = settings.DEVELOPER_TOOL_ROUNDS
        # The edit tool: where it may write, which file types a delivery takes,
        # the daemon's session for the originals, and what ``close`` brought back.
        self.can_edit, self.edit_suffixes = can_edit, edit_suffixes
        self.edit_session = uuid.uuid4().hex
        self.edits_made, self.can_move = False, False
        self.edited_files: list[dict[str, Any]] = []
        self.finish_error: str | None = None

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return {
            s["function"]["name"]: {
                "server": SERVER,
                "read_only": not s["function"]["name"].endswith(WRITE_NAMES),
            }
            for s in self.schemas()
        }

    def schemas(self) -> list[dict[str, Any]]:
        schemas = [
            developer_tool_schemas.run_tests_schema(s, self.folder_tests)
            for s in developer_tool_schemas.SCHEMAS
            if self.folder_tests or not s["function"]["name"].endswith(RUN_TESTS)
        ]
        if self.can_edit:
            schemas.append(developer_tool_schemas.EDIT_SCHEMA)
            schemas += developer_tool_schemas.STRUCTURE_SCHEMAS if self.can_move else []
        return schemas + workspace_git.schemas(self)  # Read-only git, when offered.

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
            observation.update(reason="unknown tool", requested=full_name[:200])
            return observation, mcp_tools.unknown_tool_note(full_name, self.tools)
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
            extra.update({"daemon_absent": True} if no_responders(exc) else {})
        elapsed_ms = int((time.monotonic() - started) * 1000)
        observation = self._observation(
            tool, arguments, round_no, is_error, elapsed_ms, text
        )
        observation["read_only"] = tool not in WRITES
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
            return developer_tool_replies.list_text(reply)
        if tool == "read":
            reply = self.requester(
                "workspace.read",
                {
                    "workspace_root": root,
                    "path": arguments.get("path"),
                    "start_line": arguments.get("start_line"),
                    "lines": arguments.get("lines")
                    or developer_tool_replies.READ_ASK_LINES,
                },
                READ_TIMEOUT_SECONDS,
            )
            text = developer_tool_replies.read_text(
                str(arguments.get("path") or ""), reply, self.result_max_chars
            )
            extra.update(developer_tool_replies.read_fields(reply, text))
            return text
        if tool in ("search", developer_tool_schemas.FIND):
            subject = "workspace.search" if tool == "search" else "workspace.find"
            pattern = arguments.get("pattern")
            reply = self.requester(
                subject,
                {
                    "workspace_root": root,
                    "pattern": pattern,
                    "path": arguments.get("path"),
                },
                SEARCH_TIMEOUT_SECONDS,
            )
            extra["match_count"] = reply.get("match_count")
            if tool == "search":
                extra["files_scanned"] = reply.get("files_scanned")
                return developer_tool_replies.search_text(str(pattern), reply)
            return developer_tool_replies.find_text(str(pattern), reply)
        if tool == workspace_git.TOOL:
            return workspace_git.run(self, arguments, extra)
        if tool == EDIT:
            return workspace_edits.edit(self, arguments, extra)
        if tool in (MOVE, DELETE) and self.can_move:
            return workspace_edits.structure(self, tool, arguments, extra)
        if tool == RUN_TESTS:
            # A committed check script runs as asked (``check_scripts``).
            checked, why = check_scripts.attempt(self, arguments, extra)
            if checked is not None:
                return checked
            # A narrower command on the folder's runner (``test_commands``).
            folder, plan, command, notes, refused = test_commands.resolve(
                self.folder_tests, arguments
            )
            extra["command"] = command
            extra["folder"] = folder
            if self.sandbox_error:
                raise RuntimeError(f"tests not run: {self.sandbox_error}")
            if folder not in self.installed:
                # An isolated sandbox starts with nothing installed (#766).
                installed = test_runs.install_first(
                    self.requester, root, self.sandbox, folder, plan.get("install")
                )
                extra["install"] = installed
                failed = test_runs.install_failed(installed)
                if failed:
                    return failed
                self.installed.add(folder)
            exit_code, output, timed_out = test_runs.execute(
                self, test_runs.in_folder(command, folder), extra
            )
            if timed_out:
                return f"The tests ran past the time limit and were stopped.\n{output[-2000:]}"
            return developer_tool_replies.tests_text(
                exit_code,
                output,
                self.result_max_chars,
                arguments.get("command"),
                command,
                why or refused,
                notes,
            )
        raise RuntimeError("unknown tool")

    def close(self) -> None:
        """The edits' files to deliver come back; the originals go back on disk."""
        workspace_edits.finish(self)

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

    @property
    def edited_files(self) -> list[dict[str, Any]]:
        return self.workspace.edited_files if self.workspace is not None else []

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


def _detect_tests(
    company_id: int, workspace_root: str, engine: Any = None
) -> tuple[str | None, dict[str, dict[str, str | None]]]:
    """The root's test command and each folder's, when the loop would run
    them after the delivery (``test_runs.detect``)."""
    try:
        return test_runs.detect(
            _bridge.get("loop"), _bridge.get("nats"), company_id, workspace_root, engine
        )
    except Exception as exc:  # noqa: BLE001 - Without a command the tool is not offered.
        logger.warning("Could not detect the test commands for the tools: %s", exc)
        return None, {}


def tool_root(
    company_id: int, item_id: int | None = None, *, engine: Any = None
) -> str | None:
    """Where the tools look: the task's own worktree, prepared for this attempt
    (``task_worktree``) or where it last delivered (``git_workflow.record_reply``
    keeps it on the evidence), else the company's project: the reads and the
    test run see the task's earlier deliveries and merged dependencies."""
    if prepared := task_worktree.prepared_for(company_id, item_id):
        return prepared
    try:
        root = _bridge["resolve_root"](company_id)
    except Exception as exc:  # noqa: BLE001 - No project path, no workspace tools.
        logger.warning("Could not resolve the project path for %s: %s", company_id, exc)
        return None
    if not root or item_id is None or engine is None:
        return root
    try:
        from services.evidence import delivery_gate

        verification = delivery_gate.latest_verification(engine, company_id, item_id)
    except Exception as exc:  # noqa: BLE001 - The project root is the fallback.
        logger.warning("Could not read the task's last delivery: %s", exc)
        return root
    evidence = (verification.evidence if verification is not None else None) or {}
    worktree = evidence.get("workspace_root") if evidence.get("git") else None
    return worktree if isinstance(worktree, str) and worktree else root


def read_tools(company_id: int, item_id: Any = None, engine: Any = None) -> Any:
    """List, read, search and find_file alone on the task's root (``tool_root``):
    no edit, move, delete or test run; ``None`` without the daemon or a root."""
    root = tool_root(company_id, item_id, engine=engine) if "nats" in _bridge else None
    return WorkspaceToolbox(_request_from_thread, root) if root else None


def enter_task_worktree(company_id: Any, item_id: Any, *, engine: Any = None) -> Any:
    """The task's worktree prepared as this attempt's tool root (``task_worktree``)."""
    return task_worktree.enter(
        _request_from_thread, _bridge.get("resolve_root"), company_id, item_id, engine
    )


def apply_limits(toolbox: Any, *, rounds: int, result_max_chars: int) -> None:
    """The model's limits (``services/models/model_profile.py``, issue #766)
    on *toolbox*: the workspace's rounds and every box's result budget."""
    if toolbox is None:
        return
    workspace = getattr(toolbox, "workspace", toolbox)
    for box in (toolbox, workspace, getattr(toolbox, "mcp", None)):
        if box is not None and hasattr(box, "result_max_chars"):
            box.result_max_chars = result_max_chars
    if isinstance(workspace, WorkspaceToolbox):
        workspace.max_rounds = rounds
    if hasattr(toolbox, "builtin"):
        toolbox.max_rounds = max(box.max_rounds for box in toolbox.builtin)


def toolbox_for(
    company_id: int | None,
    role: str | None,
    *,
    engine: Any = None,
    item_id: int | None = None,
    step: str = tool_steps.EXECUTE,
) -> Any:
    """The toolbox for a turn of *step*: the company's MCP tools, plus what
    the step allows and the role keeps (``contracts/tool_steps``) when a
    project path is known: R the reads, X the tests and committed scripts,
    W the edits where edits may go, S the research tools. Returns ``None``
    when there is nothing to offer, as the MCP loader does."""
    allowed = tool_steps.families(step, roles.canonical(role))
    mcp = mcp_tools.Toolbox.load(company_id, role, engine=engine)
    workspace = None
    if company_id is not None and offered_to(role, step) and "nats" in _bridge:
        root = tool_root(company_id, item_id, engine=engine)
        if root:
            box, box_error, root_test, folder_tests = None, None, None, {}
            if tool_steps.RUN in allowed:
                try:
                    box = sandbox.request_for_company_id(company_id, engine)
                except sandbox.PolicyUnavailable as e:
                    box_error = str(e)
                # The project's sandbox image before the first test run, which
                # would otherwise wait for its build (issue #766).
                stack_image.prepare_from_thread(_request_from_thread, root, box)
                root_test, folder_tests = _detect_tests(company_id, root, engine)
            workspace = WorkspaceToolbox(
                _request_from_thread,
                root,
                test_command=root_test,
                folder_tests=folder_tests,
                sandbox=box,
                sandbox_error=box_error,
                can_edit=tool_steps.WRITE in allowed
                and workspace_edits.allowed(_bridge, company_id, root, engine),
                edit_suffixes=workspace_edits.delivery_suffixes(),
            )
            workspace_edits.offer_move(workspace)  # Move and delete, or why not.
            workspace_git.offer(workspace, company_id, engine)  # Read-only git.
            if workspace.can_edit:  # The linter after each edit, read once.
                workspace.lint_profile = functools.partial(
                    edit_lint.load_profile, _bridge, company_id, root, engine
                )
    # The research tools where the step allows them, whatever the role.
    research = None
    if tool_steps.RESEARCH in allowed:
        research = research_tools.company_toolbox(company_id, engine=engine)
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
    """The prompt's "## Repository" section (``services/repo_map.py``): the
    top of the project's tree and its definitions ranked by use, bounded,
    with a word on the tools for a role that has them. The Architect gets it
    too, as context for its brief. Empty when neither applies or the project
    cannot be listed."""
    tools = offered_to(role)
    architect = str(role or "").strip().lower() in repo_map.ARCHITECT_ROLES
    if company_id is None or "nats" not in _bridge:
        return ""
    if not (tools or (architect and settings.REPO_MAP_MAX_CHARS > 0)):
        return ""
    root = tool_root(company_id, item_id, engine=engine)
    if not root:
        return ""
    return repo_map.section(
        _request_from_thread, root, tools=tools, tree_chars=MAP_MAX_CHARS
    )
