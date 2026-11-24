"""``workspace__git``: read-only git for the roles that read the project (issue #766).

An audit of real runs found the agents had no read-only git. Developers asked
``workspace__run_tests`` for ``git log``, ``git status`` or ``git diff`` (the
test suite ran instead); the Architect's briefs on task 29 told QA to run
``git diff --name-only``, and QA wrote that without it it could not know which
files to verify. This tool gives a fixed set of operations, never a free
argument list:

- ``status``: ``git status --porcelain`` of the project, bounded;
- ``changed``: the files this task changed against its base branch (committed
  or not, untracked included);
- ``diff``: one path's diff against that base, bounded;
- ``log``: the last ``count`` (at most 20) commits of the current branch;
- ``show_base``: one path's content as the base branch's head has it.

The first four are the daemon's views on ``workspace.git.diff``
(``vscode-daemon/git_view.py``, PROTOCOL.md §5.6); ``show_base`` is
``workspace.git.read_at``. Each operation is offered only when every daemon
heard announces what it needs (``offer``), so an older daemon is never asked
for a view it would misread as an integration diff. The base is the
company's base branch (``Company.git_base_branch``), else the daemon's default
branch.

A path is relative to the project, never absolute, never ``..``, never VCS
metadata or a file that may hold secrets (refused here, and again by the
daemon). What the model sees is bounded and its secret-looking lines redacted
(``context_read.redact_secrets``), as a read's. Each call is an observation
with ``read_only`` true (``WorkspaceToolbox.call``): the operation, the base
and merge base the daemon used, and for ``show_base`` the commit and the
sha256 of the bytes received, hashed here.

The functions take any toolbox with ``requester`` and ``workspace_root``, so
another read-only toolbox can offer the tool too.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import logging
from typing import Any

from services.evidence.context_read import redact_secrets
from services.tools import mcp_tools
from services.tools.developer_tool_schemas import SERVER
from services.workspace import daemon_liveness

logger = logging.getLogger("fabrique-task-worker")

TOOL = "git"
NAME = f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{TOOL}"
VIEW_SUBJECT = "workspace.git.diff"
READ_AT = "workspace.git.read_at"
TIMEOUT_SECONDS = 20.0
MAX_LOG = 20
SHOW_MAX_CHARS = 40_000
OPERATIONS = ("status", "changed", "diff", "log", "show_base")
# What each operation needs every daemon to announce (vscode-daemon/abilities.py).
NEEDS: dict[str, tuple[str, str | None]] = {
    "status": (VIEW_SUBJECT, "view"),
    "changed": (VIEW_SUBJECT, "view"),
    "diff": (VIEW_SUBJECT, "view"),
    "log": (VIEW_SUBJECT, "view"),
    "show_base": (READ_AT, None),
}
WITH_PATH = ("diff", "show_base")
# The daemon's ``workspace_read.SENSITIVE_*``: never shown, whatever is asked.
SENSITIVE_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", "credentials", "credentials.json", "secrets.*", "*.secret",
    "secrets.yaml", "secrets.yml", "service-account*.json",
)  # fmt: skip
SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".git", ".docker", ".kube"})


def offered_operations() -> tuple[str, ...]:
    """The operations every daemon heard announces (``daemon_liveness.announces``)."""
    return tuple(
        op for op in OPERATIONS if daemon_liveness.announces(*NEEDS[op]) is True
    )


def base_branch(company_id: int | None, engine: Any) -> str:
    """The company's base branch, or ``""`` (the daemon's default branch)."""
    if company_id is None or engine is None:
        return ""
    try:
        from models.company import Company
        from sqlmodel import Session

        with Session(engine) as session:
            company = session.get(Company, company_id)
            return str((company.git_base_branch if company else None) or "")
    except Exception as exc:  # noqa: BLE001 - The daemon's default branch then.
        logger.warning("Could not read the base branch: %s", exc)
        return ""


def offer(box: Any, company_id: int | None = None, engine: Any = None) -> None:
    """Set *box*'s ``git_operations`` (what to offer) and ``git_base``."""
    if box is None:
        return
    box.git_operations = offered_operations()
    box.git_base = base_branch(company_id, engine) if box.git_operations else ""


def schema(operations: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Read-only git on the project, one fixed operation per call: "
                "`status` (git status --porcelain), `changed` (the files this "
                "task changed against its base branch, committed or not), "
                "`diff` (one `path`'s diff against the base), `log` (the last "
                "`count` commits, at most 20), `show_base` (one `path` as the "
                "base branch has it). Nothing is written."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": list(operations)},
                    "path": {
                        "type": "string",
                        "description": "For diff and show_base: a file, relative to the project root.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "For log: how many commits (1 to 20, default 10).",
                    },
                },
                "required": ["operation"],
            },
        },
    }


def schemas(box: Any) -> list[dict[str, Any]]:
    """The tool's schema when *box* offers at least one operation."""
    operations = tuple(getattr(box, "git_operations", ()) or ())
    return [schema(operations)] if operations else []


def clean_path(value: Any) -> str:
    """*value* as a project-relative path, or ValueError (outside, metadata, a secret)."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("`path` is required: a file relative to the project root")
    text = value.strip().replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or not parts or ".." in parts or text[1:2] == ":":
        raise ValueError(f"not a path inside the project: {value}")
    if any(p in SENSITIVE_DIRS for p in parts) or any(
        fnmatch.fnmatch(parts[-1], pattern) for pattern in SENSITIVE_NAMES
    ):
        raise ValueError(f"VCS metadata or a file that may hold secrets: {value}")
    return "/".join(parts)


def _status_text(reply: dict[str, Any]) -> str:
    entries = [e for e in reply.get("entries") or [] if isinstance(e, dict)]
    head = f"git status on {reply.get('branch') or '(detached HEAD)'}: "
    if not entries:
        return head + "clean, nothing changed."
    rows = [
        f"{e.get('code', '??')} {e.get('path')}"
        + (f" (from {e['from']})" if e.get("from") else "")
        for e in entries
    ]
    more = reply.get("count", len(rows)) - len(rows)
    tail = [f"[{more} more entries not shown]"] if reply.get("truncated") else []
    return head + f"{reply.get('count', len(rows))} entries\n" + "\n".join(rows + tail)


def _changed_text(reply: dict[str, Any]) -> str:
    files = [f for f in reply.get("files") or [] if isinstance(f, dict)]
    head = (
        f"Files changed against {reply.get('base')} (merge base "
        f"{str(reply.get('merge_base') or '')[:7]}), committed or not: "
    )
    if not files:
        return head + "none."
    rows = [f"{f.get('change')}: {f.get('path')}" for f in files]
    more = reply.get("count", len(rows)) - len(rows)
    tail = [f"[{more} more files not shown]"] if reply.get("truncated") else []
    return head + f"{reply.get('count', len(rows))}\n" + "\n".join(rows + tail)


def _diff_text(reply: dict[str, Any], path: str) -> str:
    patch = redact_secrets(path, str(reply.get("patch") or ""))
    against = (
        f"{reply.get('base')} (merge base {str(reply.get('merge_base') or '')[:7]})"
    )
    if reply.get("untracked"):
        return f"{path} is a new file git does not track yet: read it with workspace__read."
    if not patch.strip():
        return f"{path}: no change against {against}."
    cut = "\n[diff cut here by the daemon]" if reply.get("truncated") else ""
    return f"Diff of {path} against {against}:\n{patch}{cut}"


def _log_text(reply: dict[str, Any]) -> str:
    commits = [c for c in reply.get("commits") or [] if isinstance(c, dict)]
    rows = [
        redact_secrets(
            "git log", f"{str(c.get('commit') or '')[:7]} {c.get('subject') or ''}"
        )
        for c in commits
    ]
    head = f"Last {len(rows)} commit(s) of {reply.get('branch') or '(detached HEAD)'}:"
    return "\n".join([head, *rows]) if rows else "No commits on this branch yet."


def _show_base(box: Any, path: str, extra: dict[str, Any]) -> str:
    request = {"workspace_root": box.workspace_root, "paths": [path]}
    if getattr(box, "git_base", ""):
        request["base"] = box.git_base
    reply = box.requester(READ_AT, request, TIMEOUT_SECONDS)
    extra.update(commit=reply.get("commit"), base=reply.get("base"))
    entries = [f for f in reply.get("files") or [] if isinstance(f, dict)]
    entry = next((f for f in entries if f.get("path") == path), {})
    status = entry.get("status") or "missing"
    if status != "present":
        return f"{path} at {reply.get('base')} ({str(reply.get('commit') or '')[:7]}): {status}."
    try:
        data = base64.b64decode(str(entry.get("content_b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeError("the workspace daemon sent unreadable bytes") from None
    extra.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    if b"\0" in data[:8192]:
        return f"{path} at {reply.get('base')} is a binary file ({len(data)} bytes)."
    text = redact_secrets(path, data.decode("utf-8", "replace"))
    cut = "\n[file cut here]" if len(text) > SHOW_MAX_CHARS else ""
    return (
        f"{path} as {reply.get('base')} has it (commit "
        f"{str(reply.get('commit') or '')[:7]}):\n{text[:SHOW_MAX_CHARS]}{cut}"
    )


def run(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call of the tool: the text the model sees; *extra* gets the observation."""
    operation = arguments.get("operation")
    offered = tuple(getattr(box, "git_operations", ()) or ())
    if operation not in offered:
        raise ValueError(f"`operation` is one of {', '.join(offered)}")
    extra["git_operation"] = operation
    path = clean_path(arguments.get("path")) if operation in WITH_PATH else None
    if path is not None:
        extra["path"] = path
    if operation == "show_base":
        return _show_base(box, path or "", extra)
    request: dict[str, Any] = {"workspace_root": box.workspace_root, "view": operation}
    if getattr(box, "git_base", ""):
        request["base"] = box.git_base
    if path is not None:
        request["path"] = path
    if operation == "log":
        count = arguments.get("count")
        ok = isinstance(count, int) and not isinstance(count, bool)
        request["limit"] = max(1, min(count, MAX_LOG)) if ok else 10
    reply = box.requester(VIEW_SUBJECT, request, TIMEOUT_SECONDS)
    if reply.get("view") != operation:
        # An older daemon answered as an integration diff: never misread.
        raise RuntimeError("the workspace daemon does not serve this git view")
    for key in ("base", "merge_base", "truncated"):
        if key in reply:
            extra[key] = reply[key]
    if operation == "status":
        return _status_text(reply)
    if operation == "changed":
        return _changed_text(reply)
    if operation == "diff":
        return _diff_text(reply, path or "")
    return _log_text(reply)
