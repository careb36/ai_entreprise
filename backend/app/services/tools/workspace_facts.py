"""``workspace__api_surface``, ``workspace__config_vars``, ``workspace__dependencies`` (issue #766).

Documenting a whole project asks to extract, not guess
(``docs/design/agent-toolsets.md``, "Next changes" item 10): an agent that
writes the API reference, the configuration page or the dependency list from
memory or from a prompt's excerpt invents routes, variables and versions.
Three read-only tools (family R, ``contracts/tool_steps``) ask the daemon,
which reads the project statically (``vscode-daemon/project_facts.py``,
PROTOCOL.md §5.9) through the existing ``workspace.related`` subject with
``lookup.kind`` set to the tool's name:

- ``workspace__api_surface``: the OpenAPI documents committed in the project
  and the routes FastAPI-style decorators declare (method, path, function,
  file:line), ``include_router`` prefixes listed apart. No server is started.
- ``workspace__config_vars``: the environment variables the code reads, with
  file:line and the default when it is a literal (a secret-looking name's
  default withheld by the daemon). No ``.env`` file is read.
- ``workspace__dependencies``: the manifests' direct dependencies with their
  declared versions. A licence is not here: it is the registry's claim, which
  ``research__package`` reports (``license_by_registry``) where the turn may
  research; the reply says so.

Each tool is offered only when every daemon heard announces its kind as a
feature of ``workspace.related`` (``offer``); a reply without the kind (an
older daemon) is an error, never "nothing found". ``path`` narrows to a
folder or file, checked here (``workspace_git.clean_path``: relative, inside,
never VCS metadata or a file that may hold secrets) and again by the daemon.
The text is bounded (``MAX_SHOWN`` rows) and redacted
(``context_read.redact_secrets``). The observation (``WorkspaceToolbox.call``)
names every file a row came from with the sha256 the daemon read, the count
and digest of every file scanned, and the rows left out; what the model
writes from it stays a claim.
"""

from __future__ import annotations

from typing import Any

from services.evidence.context_read import redact_secrets
from services.tools import mcp_tools
from services.tools.developer_tool_schemas import SERVER
from services.tools.workspace_git import clean_path
from services.workspace import daemon_liveness

API_SURFACE, CONFIG_VARS, DEPENDENCIES = "api_surface", "config_vars", "dependencies"
TOOLS = (API_SURFACE, CONFIG_VARS, DEPENDENCIES)
SUBJECT = "workspace.related"
TIMEOUT_SECONDS = 30.0
MAX_SHOWN = 120
MAX_FILES_OBSERVED = 100
LICENCES_NOTE = (
    "Licences are not read here: research__package reports each package's "
    "licence as its registry states it (a claim of the registry, not an "
    "observation of the code), where this turn may research."
)
_DESCRIPTIONS = {
    API_SURFACE: (
        "The project's HTTP API as its files declare it: the OpenAPI documents "
        "committed in it (operations: method, path, operationId) and the routes "
        "FastAPI-style decorators declare in the code (method, path, function, "
        "file:line). Static: nothing is started. Read-only."
    ),
    CONFIG_VARS: (
        "The environment variables the project's code reads (os.environ, "
        "os.getenv, pydantic BaseSettings fields, process.env, import.meta.env), "
        "with file:line and the default when it is a literal. Values in .env "
        "files are never read. Read-only."
    ),
    DEPENDENCIES: (
        "The direct dependencies the project's manifests declare (pyproject.toml, "
        "requirements*.txt, package.json), with the version as declared and the "
        "group (main, dev, optional). Licences come from research__package, as "
        "the registry's claim. Read-only."
    ),
}


def _schema(tool: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{tool}",
            "description": _DESCRIPTIONS[tool],
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: only this folder or file, relative to the project root.",
                    }
                },
            },
        },
    }


SCHEMAS = {tool: _schema(tool) for tool in TOOLS}


def offer(box: Any) -> None:
    """Set *box*'s ``facts``: the tools whose kind every daemon heard announces."""
    if box is not None:
        box.facts = frozenset(
            t for t in TOOLS if daemon_liveness.announces(SUBJECT, t) is True
        )


def schemas(box: Any) -> list[dict[str, Any]]:
    offered = getattr(box, "facts", frozenset())
    return [SCHEMAS[t] for t in TOOLS if t in offered]


def _cut(lines: list[str], more: int) -> list[str]:
    left = max(0, len(lines) - MAX_SHOWN) + int(more or 0)
    out = lines[:MAX_SHOWN]
    if left:
        out.append(f"[{left} more not shown; give `path` to narrow]")
    return out


def _api_text(lookup: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for doc in lookup.get("openapi") or []:
        if doc.get("error"):
            lines.append(f"OpenAPI document {doc.get('path')}: {doc['error']}")
            continue
        title = " ".join(str(doc.get(k)) for k in ("title", "version") if doc.get(k))
        lines.append(
            f"OpenAPI document {doc.get('path')} ({doc.get('spec') or '?'}{', ' + title if title else ''}) "
            f"[sha256 {str(doc.get('sha256'))[:12]}]:"
        )
        for op in doc.get("operations") or []:
            extra = f"  {op['operation_id']}" if op.get("operation_id") else ""
            lines.append(f"- {op.get('method')} {op.get('path')}{extra}")
    routes = lookup.get("routes") or []
    if routes:
        lines.append("Routes declared in the code (decorators, read statically):")
        lines += [
            f"- {r.get('method')} {r.get('path')}  {r.get('function')}  {r.get('file')}:{r.get('line')}"
            for r in routes
        ]
    for mount in lookup.get("mounts") or []:
        lines.append(
            f"Note: {mount.get('file')}:{mount.get('line')} includes router "
            f"`{mount.get('router')}` with prefix {mount.get('prefix')!r}; that "
            "prefix is not applied to the paths above."
        )
    return lines or ["No OpenAPI document and no decorated routes in the project."]


def _config_text(lookup: dict[str, Any]) -> list[str]:
    rows = lookup.get("variables") or []
    if not rows:
        return ["The code reads no environment variable the daemon's parse finds."]
    lines = [
        "Environment variables the code reads (values in .env files are never read):"
    ]
    for row in rows:
        if row.get("default_withheld"):
            default = "  default: (withheld, the name looks like a secret)"
        elif "default" in row:
            default = f"  default: {row['default']!r}"
        else:
            default = "  required" if row.get("required") else ""
        lines.append(
            f"- {row.get('name')}  {row.get('file')}:{row.get('line')}  ({row.get('source')}){default}"
        )
    return lines


def _dependencies_text(lookup: dict[str, Any]) -> list[str]:
    lines = []
    for manifest in lookup.get("manifests") or []:
        state = (
            f": {manifest['error']}"
            if manifest.get("error")
            else f", {manifest.get('count', 0)} dependencies"
        )
        lines.append(
            f"Manifest {manifest.get('path')} ({manifest.get('ecosystem')}{state}) "
            f"[sha256 {str(manifest.get('sha256') or '')[:12]}]"
        )
    for row in lookup.get("dependencies") or []:
        lines.append(
            f"- {row.get('name')} {row.get('version') or '(no version)'}  "
            f"[{row.get('group')}, {row.get('ecosystem')}]  {row.get('manifest')}"
        )
    return lines or [
        "No pyproject.toml, requirements*.txt or package.json in the project."
    ]


TEXTS = {
    API_SURFACE: _api_text,
    CONFIG_VARS: _config_text,
    DEPENDENCIES: _dependencies_text,
}


def run(box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call: the text the model sees; *extra* gets the observation."""
    if tool not in getattr(box, "facts", frozenset()):
        raise RuntimeError(f"the workspace daemon does not announce {tool}")
    raw_path = arguments.get("path")
    path = clean_path(raw_path) if raw_path not in (None, "") else ""
    request: dict[str, Any] = {"kind": tool, **({"path": path} if path else {})}
    extra["lookup"] = dict(request)
    reply = box.requester(
        SUBJECT,
        {"workspace_root": box.workspace_root, "seeds": [], "lookup": request},
        TIMEOUT_SECONDS,
    )
    lookup = reply.get("lookup")
    if not isinstance(lookup, dict) or lookup.get("kind") != tool:
        # An older daemon answered an empty walk: never read as "nothing found".
        raise RuntimeError(f"the workspace daemon does not serve {tool}")
    files = [
        {"path": f.get("path"), "sha256": f.get("sha256")}
        for f in (lookup.get("files") or [])[:MAX_FILES_OBSERVED]
        if isinstance(f, dict)
    ]
    extra.update(
        files=files,
        files_scanned=lookup.get("files_scanned"),
        scanned_sha256=lookup.get("scanned_sha256"),
        more=lookup.get("more") or 0,
        partial=bool(reply.get("partial")),
    )
    lines = _cut(TEXTS[tool](lookup), lookup.get("more") or 0)
    if tool == DEPENDENCIES:
        lines.append(LICENCES_NOTE)
    if reply.get("partial"):
        lines.append(
            "[the project is larger than the daemon reads; some files are not in it]"
        )
    return redact_secrets(tool, "\n".join(lines))
