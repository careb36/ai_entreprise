"""``workspace__definition`` and ``workspace__references``: code navigation as tools (issue #766).

The daemon keeps the project's reference graph (``workspace.related``,
``vscode-daemon/code_graph.py``) and reads signatures
(``workspace.signatures``), but only the prompt's related-code section and
the Verifier used them: an agent guessed a function's parameters or who
called it, or spent rounds searching. Two read-only tools ask the same graph
(``docs/design/agent-toolsets.md``, next change 6):

- ``workspace__definition``: where a name is defined, with its signature (and
  for a top-level Python function or class, its parameters as
  ``workspace.signatures`` reads them), the path, the line and the sha256 of
  the file as the daemon read it; ``path`` narrows it to one file;
- ``workspace__references``: the files and lines that use a name, or the
  names a module (``path``) defines, or that module's ``name`` alone.

Both go to ``workspace.related`` with its optional ``lookup`` field
(``vscode-daemon/code_lookup.py``, PROTOCOL.md §5.7), not a new subject. They
are offered only when every daemon heard announces the ``lookup`` feature on
``workspace.related`` (``daemon_liveness.announces``); a reply without
``lookup`` (an older daemon) is an error, never "no definition". Every
toolbox with the read tools offers them (``WorkspaceToolbox`` calls
``offer``): the file roles' and the read-only ones (the Architect's brief,
QA's review).

A ``path`` is relative to the tool root, never absolute, never ``..``, never
VCS metadata or a file that may hold secrets (``workspace_git.clean_path``;
the daemon refuses them again). Results are bounded (``MAX_FILES`` files, ten
lines each) and redacted (``context_read.redact_secrets``). Each call is an
observation on the turn (``WorkspaceToolbox.call``): the lookup asked, the
files the reply named with their sha256 as the daemon read them, and what the
bounds left out. What the model concludes from it stays a claim; the graph is
a parse (a name used is not a call proven).

``workspace__lint`` (``workspace_lint``) is offered beside them in execution
turns; ``schemas`` and ``run`` serve all three for ``WorkspaceToolbox``.
"""

from __future__ import annotations

from typing import Any

from services.evidence.context_read import redact_secrets
from services.tools import mcp_tools, workspace_lint
from services.tools.developer_tool_schemas import SERVER
from services.tools.workspace_git import clean_path
from services.workspace import daemon_liveness

SUBJECT = "workspace.related"
FEATURE = "lookup"
DEFINITION = "definition"
REFERENCES = "references"
NAVIGATION = (DEFINITION, REFERENCES)
TOOLS = (*NAVIGATION, workspace_lint.TOOL)
TIMEOUT_SECONDS = 20.0
MAX_FILES = 20
MAX_NAME = 200


def offered() -> bool:
    """Whether every daemon heard announces ``lookup`` on ``workspace.related``."""
    return daemon_liveness.announces(SUBJECT, FEATURE) is True


def offer(box: Any) -> None:
    """Set *box*'s ``navigation`` (whether the two tools are offered)."""
    if box is not None:
        box.navigation = offered()


def _name(tool: str) -> str:
    return f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{tool}"


DEFINITION_SCHEMA = {
    "type": "function",
    "function": {
        "name": _name(DEFINITION),
        "description": (
            "Where a function, class or method is defined in the project: the "
            "file, the line, its signature (parameters, for a top-level Python "
            "one) and the file's sha256. Check a call against it before you "
            "write the call. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "One identifier."},
                "path": {
                    "type": "string",
                    "description": "Optional: only definitions in this file, relative to the project root.",
                },
            },
            "required": ["name"],
        },
    },
}
REFERENCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": _name(REFERENCES),
        "description": (
            "Which files use a name, or a module: give `name` (who uses it), "
            "`path` (who uses what that file defines) or both (who uses that "
            "file's `name`). Each file with the line numbers that name it. "
            "Read them before you change a name others use. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "One identifier."},
                "path": {
                    "type": "string",
                    "description": "A file, relative to the project root.",
                },
            },
        },
    },
}


def schemas(box: Any) -> list[dict[str, Any]]:
    """The navigation schemas when offered, and the lint's when offered."""
    navigation = [DEFINITION_SCHEMA, REFERENCES_SCHEMA]
    out = navigation if getattr(box, "navigation", False) else []
    return out + workspace_lint.schemas(box)


def _clean_name(value: Any, required: bool) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        if required:
            raise ValueError("`name` is required: one identifier")
        return ""
    if len(text) > MAX_NAME or not text.replace("$", "_").isidentifier():
        raise ValueError(f"`name` must be one identifier, not {text[:80]!r}")
    return text


def _short(digest: Any) -> str:
    return str(digest or "")[:12]


def _definition_text(name: str, lookup: dict[str, Any], partial: bool) -> str:
    rows = [d for d in lookup.get("definitions") or [] if isinstance(d, dict)]
    where = f" in {lookup['path']}" if lookup.get("path") else ""
    if lookup.get("path") and lookup.get("path_found") is False:
        return f"{lookup['path']} is not a source file the daemon's parse reads."
    if not rows:
        text = f"No definition of `{name}`{where} in the project's Python or JavaScript/TypeScript files."
    else:
        lines = [
            f"Definitions of `{name}`{where} (the daemon's parse of the files on disk now):"
        ]
        for row in rows:
            method = " (a method)" if row.get("depth") else ""
            lines.append(
                f"- {row.get('path')}:{row.get('line')}{method}  {row.get('signature')}  "
                f"[sha256 {_short(row.get('sha256'))}]"
            )
            params = row.get("parameters")
            if isinstance(params, dict):
                lines.append(f"  parameters: {_parameters_text(params)}")
        if lookup.get("more"):
            lines.append(f"[{lookup['more']} more definitions not shown; give `path`]")
        text = "\n".join(lines)
    if partial:
        text += (
            "\n[the project is larger than the daemon reads; some files are not in it]"
        )
    return redact_secrets("definition", text)


def _parameters_text(params: dict[str, Any]) -> str:
    positional = [
        *(params.get("positional_only") or []),
        *(params.get("positional") or []),
    ]
    defaults = int(params.get("positional_defaults") or 0)
    parts = [
        f"{p}=…" if i >= len(positional) - defaults else str(p)
        for i, p in enumerate(positional)
    ]
    if params.get("varargs"):
        parts.append("*args")
    for kw in params.get("keyword_only") or []:
        if isinstance(kw, dict):
            parts.append(
                f"{kw.get('name')}=…"
                if kw.get("default")
                else f"{kw.get('name')} (keyword)"
            )
    if params.get("varkw"):
        parts.append("**kwargs")
    return f"({', '.join(parts)})"


def _references_text(
    name: str, path: str, lookup: dict[str, Any], partial: bool
) -> str:
    rows = [r for r in lookup.get("references") or [] if isinstance(r, dict)]
    what = f"`{name}` of {path}" if name and path else f"`{name}`" if name else path
    if path and lookup.get("path_found") is False:
        text = f"{path} is not a source file the daemon's parse reads."
    elif not rows:
        text = f"No file uses {what}."
        if lookup.get("common_name"):
            text += f" (`{name}` is a name every program uses; the parse does not count it: search for it.)"
    else:
        lines = [
            f"Files that use {what} (the daemon's parse of the files on disk now):"
        ]
        for row in rows:
            numbers = ", ".join(str(n) for n in row.get("lines") or [])
            more = (
                f" (+{row['more_lines']} more lines)" if row.get("more_lines") else ""
            )
            names = ", ".join(row.get("names") or [])
            lines.append(
                f"- {row.get('path')} lines {numbers or '?'}{more}: {names}  "
                f"[sha256 {_short(row.get('sha256'))}]"
            )
        if lookup.get("more"):
            lines.append(f"[{lookup['more']} more files not shown]")
        text = "\n".join(lines)
    if partial:
        text += (
            "\n[the project is larger than the daemon reads; some files are not in it]"
        )
    return redact_secrets("references", text)


def _observed(lookup: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The files the reply named, with the sha256 the daemon read: the observation."""
    return [
        {k: row.get(k) for k in ("path", "sha256", "line") if k in row}
        for row in (lookup.get(key) or [])[:MAX_FILES]
        if isinstance(row, dict)
    ]


def navigate(
    box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]
) -> str:
    """One definition or references call: the text the model sees."""
    if not getattr(box, "navigation", False):
        raise RuntimeError("the workspace daemon does not announce code navigation")
    name = _clean_name(arguments.get("name"), required=tool == DEFINITION)
    raw_path = arguments.get("path")
    path = clean_path(raw_path) if raw_path not in (None, "") else ""
    if tool == REFERENCES and not (name or path):
        raise ValueError("give `name`, `path` or both")
    lookup_request: dict[str, Any] = {"kind": tool}
    if name:
        lookup_request["name"] = name
    if path:
        lookup_request["path"] = path
    extra["lookup"] = dict(lookup_request)
    reply = box.requester(
        SUBJECT,
        {
            "workspace_root": box.workspace_root,
            "seeds": [],
            "lookup": lookup_request,
            "max_files": MAX_FILES,
        },
        TIMEOUT_SECONDS,
    )
    lookup = reply.get("lookup")
    if not isinstance(lookup, dict) or lookup.get("kind") != tool:
        # An older daemon answered an empty walk: never read as "nothing found".
        raise RuntimeError("the workspace daemon does not serve code navigation")
    partial = bool(reply.get("partial"))
    key = "definitions" if tool == DEFINITION else "references"
    extra.update(
        files=_observed(lookup, key),
        more=lookup.get("more") or 0,
        partial=partial,
    )
    if tool == DEFINITION:
        return _definition_text(name, lookup, partial)
    return _references_text(name, path, lookup, partial)


def run(box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call of a navigation tool or the lint (``workspace_lint``)."""
    if tool == workspace_lint.TOOL:
        return workspace_lint.run(box, arguments, extra)
    return navigate(box, tool, arguments, extra)
