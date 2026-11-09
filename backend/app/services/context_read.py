"""What the Developer read, as evidence: the reads on a run, redaction, and
the Verifier's ``context_read`` check.

The workspace tools (``services/developer_tools.py``) record every read with
the file's sha256 as the daemon read it. These readers turn that record into
the check that says which of the files a delivery modified the Developer had
read first and which it had not, and keep secret-looking lines out of what
the model is shown.
"""

from __future__ import annotations

from typing import Any

from services.contracts.tool_calls import WORKSPACE_SERVER as SERVER


def redact_secrets(path: str, text: str) -> str:
    """*text* with every line that looks like it holds a secret replaced
    (``standards.secrets_in``, or ``standards.secret_line``, which fails
    closed on a secret-looking name followed by a quoted value whatever sits
    between): what the daemon read is shown to the model without the value."""
    from services import standards

    lines = text.splitlines()
    out = []
    for line in lines:
        if standards.secrets_in(path or "file", line) or standards.secret_line(line):
            out.append("[line redacted: it looks like a secret]")
        else:
            out.append(line)
    return "\n".join(out)


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


def files_edited(tool_calls: Any) -> set[str]:
    """The files the workspace edit tool changed in a run, by the daemon's record."""
    return {
        call["path"]
        for call in tool_calls or []
        if isinstance(call, dict)
        and call.get("server") == SERVER
        and call.get("tool") == "edit"
        and not call.get("is_error")
        and isinstance(call.get("path"), str)
    }


def edited_texts(evidence: Any) -> list[dict[str, str]]:
    """The files the edit tool changed, as the daemon read them back when the
    tool loop finished (``tool_edit_files`` on the evidence): what the
    delivery wrote for them, beside the answer's files."""
    files = (
        (evidence or {}).get("tool_edit_files") if isinstance(evidence, dict) else None
    )
    return [
        {"path": f["path"], "content": f["content"]}
        for f in files or []
        if isinstance(f, dict)
        and isinstance(f.get("path"), str)
        and isinstance(f.get("content"), str)
    ]


def check_context_read(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's ``context_read`` check: of the files the delivery
    modified (the daemon says which existed before), which the Developer had
    read first and which it had not. Informational, never the verdict; absent
    when the workspace tools were not used at all (a listing or a failed read
    counts as use: the Developer had them and did not read the file)."""
    evidence = evidence or {}
    calls = [c for c in evidence.get("tool_calls") or [] if isinstance(c, dict)]
    tools_used = any(c.get("server") == SERVER for c in calls)
    read = files_read(calls)
    modified = sorted(
        w["path"]
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict)
        and w.get("status") == "written"
        and w.get("change") == "modified"
    )
    if not tools_used:
        # A producer without the workspace tools is classified by its paths.
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
        "workspace_tools_used": True,
        "read": sorted(read),
        "modified_read": seen,
        "modified_unread": unread,
    }
