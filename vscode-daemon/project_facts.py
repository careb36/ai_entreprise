"""What a project declares, read from its files: ``workspace.related`` with ``lookup`` (issue #766).

Documenting a whole project asks to extract, not guess
(docs/design/agent-toolsets.md, "Next changes" item 10). Three more lookup
kinds on the existing ``workspace.related`` subject, each announced as a
feature of its own (``abilities.FEATURES``), answer the agents' read-only
``workspace__api_surface``, ``workspace__config_vars`` and
``workspace__dependencies`` (backend ``services/tools/workspace_facts.py``):

- ``api_surface``: the OpenAPI documents committed in the project
  (``OPENAPI_NAMES``: their operations, method and path), and the routes
  FastAPI-style decorators declare in the Python code (``@app.get("/x")``,
  ``@router.post``, ``api_route``), with the ``APIRouter(prefix=…)`` of the
  same module applied and each ``include_router`` listed apart. Static: no
  server is started and nothing is imported.
- ``config_vars``: the environment variables the code reads
  (``os.environ[…]``, ``os.environ.get``, ``os.getenv``, the fields of a
  pydantic ``BaseSettings`` class, ``process.env``, ``import.meta.env``),
  with the file, the line and the default when it is a literal. A default
  whose name looks like a secret, or that is a URL with a password, is never
  sent. No ``.env`` file is read:
  hidden files and files that may hold secrets are never candidates.
- ``dependencies``: the direct dependencies the manifests declare
  (``manifest_deps``: ``pyproject.toml``, ``requirements*.txt``,
  ``package.json``), with the version as declared. Licences are not here:
  they are a registry's claim (the backend's research tool).

The files are the code graph's candidates (``repo_map._candidates``: no
folder a listing skips, no hidden file, no file that may hold secrets, no
symlink out of the project, regular files only), and for documents and
manifests a walk with the same guards. Each source file's extract is cached
by its sha256 in the parse cache (``repo_map._load_cache``), so a file is
parsed once per content. ``path`` narrows to one folder or file. The reply
names every file a row came from with the sha256 of the bytes read, and the
count and a digest of every file scanned; rows are bounded (``MAX_ROWS``),
``more`` counts the rest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import code_graph
import manifest_deps
import repo_map
import workspace_read

KINDS = ("api_surface", "config_vars", "dependencies")
VERSION = 1  # Of the cached extracts: raised when their shape changes.
MAX_ROWS = 200
MAX_FILES_LISTED = 100
DOCUMENT_MAX_BYTES = 2 * 1024 * 1024
MAX_WALK_FILES = 20000
OPENAPI_NAMES = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml")
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
DEFAULT_MAX_CHARS = 120
_SECRET_NAME = re.compile(r"secret|token|passw|pwd|key|credential|private|auth", re.I)
_URL_CREDENTIALS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_ROUTE_HINT = re.compile(rb"@\s*[\w.]+\.(?:get|post|put|patch|delete|head|options|trace|api_route|websocket)\s*\(")
_ENV_HINT = re.compile(rb"environ|getenv|BaseSettings|process\.env|import\.meta\.env")
_JS_ENV = re.compile(
    r"(process\.env|import\.meta\.env)(?:\.([A-Za-z_]\w*)|\[\s*(['\"])([^'\"]+)\3\s*\])"
    r"(?:\s*(?:\|\||\?\?)\s*(?:(['\"])([^'\"\n]*)\5|(-?\d+(?:\.\d+)?|true|false)))?"
)


def _scope(lookup: Dict[str, Any]) -> str:
    """The folder or file *lookup* narrows to, or ``""`` for the project."""
    raw = lookup.get("path")
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        raise workspace_read.ReadError(400, "lookup.path must be a path.")
    path = code_graph._normalize(raw)
    if not path:
        raise workspace_read.ReadError(400, "lookup.path must be inside the project.")
    return path.rstrip("/")


def _within(relative: str, scope: str) -> bool:
    return not scope or relative == scope or relative.startswith(scope + "/")


def walk_named(root: Path, wanted, scope: str = "") -> Tuple[List[Tuple[str, Path]], bool]:
    """``(relative, path)`` of the files whose name *wanted* accepts, with the
    listing's guards; the second value says the walk was cut."""
    found: List[Tuple[str, Path]] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in workspace_read.SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            seen += 1
            if seen > MAX_WALK_FILES:
                return found, True
            if name.startswith(".") or not wanted(name, dirpath):
                continue
            path = Path(dirpath) / name
            contained = workspace_read._inside(root, path)
            if contained is None or workspace_read.is_sensitive(root, contained):
                continue
            relative = path.relative_to(root).as_posix()
            if _within(relative, scope):
                found.append((relative, contained))
    return found, False


def _literal(node: Optional[ast.AST]) -> Optional[str]:
    """A constant's value as text (a string as it is), else ``None``."""
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, (str, int, float, bool))):
        return node.value if isinstance(node.value, str) else repr(node.value)
    return None


def _keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _callee(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


# ---------------------------------------------------------------------------
# api_surface
# ---------------------------------------------------------------------------


def python_routes(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """The routes FastAPI-style decorators declare in *text*, and its ``include_router`` calls."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {"routes": [], "mounts": [], "error": ["does not parse"]}
    prefixes: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _callee(node.value.func) in ("APIRouter", "FastAPI"):
            prefix = _literal(_keyword(node.value, "prefix")) or ""
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
    routes: List[Dict[str, Any]] = []
    mounts: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node.func) == "include_router" and node.args:
            mounts.append({
                "line": node.lineno, "router": ast.unparse(node.args[0])[:80],
                "prefix": _literal(_keyword(node, "prefix")) or "",
            })
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            verb = decorator.func.attr
            if verb in HTTP_METHODS or verb == "websocket":
                methods = [verb.upper()]
            elif verb == "api_route":
                listed = _keyword(decorator, "methods")
                methods = [str(_literal(e)).upper() for e in getattr(listed, "elts", []) if _literal(e)] or ["GET"]
            else:
                continue
            path = _literal(decorator.args[0]) if decorator.args else _literal(_keyword(decorator, "path"))
            if not isinstance(path, str):
                continue
            owner = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
            routes.append({
                "method": ",".join(methods), "path": prefixes.get(owner, "") + path,
                "function": node.name, "line": decorator.lineno, "router": owner,
            })
    return {"routes": routes, "mounts": mounts}


def _openapi_json(raw: bytes) -> Dict[str, Any]:
    doc = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    operations = []
    for path, item in (doc.get("paths") or {}).items():
        for method, operation in (item or {}).items() if isinstance(item, dict) else []:
            if method in HTTP_METHODS:
                op = operation if isinstance(operation, dict) else {}
                operations.append({
                    "method": method.upper(), "path": str(path),
                    "operation_id": str(op.get("operationId") or "")[:120] or None,
                    "summary": str(op.get("summary") or "")[:160] or None,
                })
    return {
        "spec": str(doc.get("openapi") or doc.get("swagger") or "")[:20] or None,
        "title": str(info.get("title") or "")[:160] or None,
        "version": str(info.get("version") or "")[:40] or None,
        "operations": operations,
    }


def _openapi_yaml(raw: bytes) -> Dict[str, Any]:
    """The operations of a YAML OpenAPI document by its indentation (no YAML
    library among the daemon's dependencies): the keys under ``paths:`` that
    start with ``/``, and the HTTP methods one level below each."""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    out: Dict[str, Any] = {"spec": None, "title": None, "version": None, "operations": []}
    in_paths, path_indent, path = False, None, None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        top = re.match(r"^(openapi|swagger):\s*['\"]?([^'\"\s]+)", line)
        if top:
            out["spec"] = top.group(2)[:20]
        if indent == 0:
            in_paths, path_indent, path = stripped == "paths:", None, None
            continue
        title = re.match(r"^\s+(title|version):\s*['\"]?(.*?)['\"]?\s*$", line)
        if not in_paths and title and indent <= 2 and out[title.group(1)] is None:
            out[title.group(1)] = title.group(2)[:160]
        if not in_paths:
            continue
        key = re.match(r"^['\"]?(/[^'\"]*?)['\"]?:\s*$", stripped)
        if key and (path_indent is None or indent == path_indent):
            path_indent, path = indent, key.group(1)
            continue
        verb = re.match(r"^([a-z]+):", stripped)
        if path and path_indent is not None and indent > path_indent and verb and verb.group(1) in HTTP_METHODS:
            out["operations"].append({"method": verb.group(1).upper(), "path": path, "operation_id": None, "summary": None})
    return out


def _documents(root: Path, scope: str) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]], bool]:
    found, cut = walk_named(root, lambda name, _d: name.lower() in OPENAPI_NAMES, scope)
    documents: List[Dict[str, Any]] = []
    read: List[Tuple[str, str]] = []
    for relative, path in found[:20]:
        raw = repo_map.read_source(path, DOCUMENT_MAX_BYTES)
        if raw is None:
            documents.append({"path": relative, "error": "not a regular file of at most 2 MiB"})
            continue
        digest = hashlib.sha256(raw).hexdigest()
        read.append((relative, digest))
        row: Dict[str, Any] = {"path": relative, "sha256": digest}
        try:
            row.update(_openapi_json(raw) if relative.lower().endswith(".json") else _openapi_yaml(raw))
        except (ValueError, RecursionError):
            row["error"] = "does not parse"
        documents.append(row)
    return documents, read, cut


# ---------------------------------------------------------------------------
# config_vars
# ---------------------------------------------------------------------------


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (isinstance(node, ast.Name) and node.id == "environ")


def _env_row(name: Any, line: int, source: str, default: Optional[ast.AST] = None, required: bool = False) -> Optional[Dict[str, Any]]:
    if not isinstance(name, str) or not name or len(name) > 200:
        return None
    row: Dict[str, Any] = {"name": name, "line": line, "source": source}
    if required:
        row["required"] = True
    value = _literal(default)
    if value is not None:
        row["default"] = value
    return row


def _settings_rows(node: ast.ClassDef) -> List[Dict[str, Any]]:
    prefix = ""
    for item in node.body:
        if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "model_config" for t in item.targets) and isinstance(item.value, ast.Call):
            prefix = _literal(_keyword(item.value, "env_prefix")) or prefix
        if isinstance(item, ast.ClassDef) and item.name == "Config":
            for inner in item.body:
                if isinstance(inner, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "env_prefix" for t in inner.targets):
                    prefix = _literal(inner.value) or prefix
    rows = []
    for item in node.body:
        if not (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)) or item.target.id.startswith("_") or item.target.id == "model_config":
            continue
        name, default = (prefix + item.target.id).upper(), item.value
        if isinstance(default, ast.Call) and _callee(default.func) == "Field":
            alias = _literal(_keyword(default, "validation_alias")) or _literal(_keyword(default, "alias"))
            name = alias if isinstance(alias, str) else name
            default = default.args[0] if default.args else _keyword(default, "default")
        row = _env_row(name, item.lineno, "pydantic BaseSettings", default, required=item.value is None)
        if row:
            rows.append(row)
    return rows


def python_env(text: str) -> List[Dict[str, Any]]:
    """The environment variables *text* reads, with the line and a literal default."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    rows: List[Optional[Dict[str, Any]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            rows.append(_env_row(_literal(node.slice), node.lineno, "os.environ[]", required=True))
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            default = node.args[1] if len(node.args) > 1 else _keyword(node, "default")
            if isinstance(func, ast.Attribute) and func.attr in ("get", "setdefault") and _is_environ(func.value):
                rows.append(_env_row(_literal(node.args[0]), node.lineno, f"os.environ.{func.attr}", default))
            elif _callee(func) == "getenv":
                rows.append(_env_row(_literal(node.args[0]), node.lineno, "os.getenv", default))
        elif isinstance(node, ast.ClassDef) and any(_callee(b).endswith("BaseSettings") for b in node.bases):
            rows.extend(_settings_rows(node))
    return [r for r in rows if r]


def javascript_env(text: str) -> List[Dict[str, Any]]:
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _JS_ENV.finditer(line):
            name = match.group(2) or match.group(4)
            row: Dict[str, Any] = {"name": name, "line": number, "source": match.group(1)}
            default = match.group(6) if match.group(5) else match.group(7)
            if default is not None:
                row["default"] = default
            rows.append(row)
    return rows


def _shown_default(row: Dict[str, Any]) -> Dict[str, Any]:
    """*row* with its default cut, or withheld when the name looks like a secret."""
    if "default" not in row:
        return row
    if _SECRET_NAME.search(row["name"]) or _URL_CREDENTIALS.search(str(row["default"])):
        kept = {k: v for k, v in row.items() if k != "default"}
        return {**kept, "default_withheld": True}
    return {**row, "default": str(row["default"])[:DEFAULT_MAX_CHARS]}


# ---------------------------------------------------------------------------
# The lookup
# ---------------------------------------------------------------------------


def _sources(root: Path, scope: str, hint: "re.Pattern[bytes]", kind: str, deadline: float):
    """``(relative, sha256, extract)`` of each candidate source file *hint* matches,
    from the parse cache or extracted now; then the files scanned and whether cut."""
    files, capped = repo_map._candidates(root)
    files = [(r, p) for r, p in files if _within(r, scope)]
    contents, too_large = repo_map.read_sources(files)
    cache_path, cache = repo_map._load_cache(root)
    parses = cache.setdefault("parses", {})
    out, late = [], False
    for relative, digest, raw in contents:
        suffix = Path(relative).suffix.lower()
        key = f"facts{VERSION}:{kind}:{suffix}:{digest}"
        entry = parses.get(key)
        if entry is None:
            if not hint.search(raw):
                continue
            if time.monotonic() > deadline:
                late = True
                break
            text = raw.decode("utf-8", errors="replace")
            if kind == "api_surface":
                entry = python_routes(text) if suffix in repo_map.PY_SUFFIXES else {"routes": [], "mounts": []}
            else:
                entry = python_env(text) if suffix in repo_map.PY_SUFFIXES else javascript_env(text)
            parses[key] = entry
        out.append((relative, digest, entry))
    repo_map._save_cache(cache_path, cache)
    scanned = [(r, d) for r, d, _raw in contents]
    return out, scanned, capped or too_large or late


def _digest_of(scanned: List[Tuple[str, str]]) -> str:
    return hashlib.sha256("\n".join(f"{r} {d}" for r, d in sorted(scanned)).encode("utf-8")).hexdigest()


def _bounded(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    return rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS)


def facts(workspace_path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """The reply to ``workspace.related`` whose ``lookup.kind`` is one of ``KINDS``."""
    root = workspace_read._root(workspace_path)
    lookup = data.get("lookup") or {}
    kind = lookup.get("kind")
    scope = _scope(lookup)
    deadline = time.monotonic() + code_graph.DEADLINE_SECONDS
    result: Dict[str, Any] = {
        "kind": kind, "name": None, "path": scope or None, "path_found": None,
        "definitions": [], "references": [], "more": 0, "common_name": False,
    }
    partial = False
    used: Dict[str, str] = {}
    if kind == "dependencies":
        manifests, rows, cut = manifest_deps.read(root, scope, walk_named)
        result["manifests"] = manifests
        result["dependencies"], result["more"] = _bounded(rows)
        used = {m["path"]: m["sha256"] for m in manifests if m.get("sha256")}
        scanned = list(used.items())
        partial = cut
    elif kind == "api_surface":
        documents, read, cut = _documents(root, scope)
        extracts, scanned, partial = _sources(root, scope, _ROUTE_HINT, kind, deadline)
        routes: List[Dict[str, Any]] = []
        mounts: List[Dict[str, Any]] = []
        for relative, digest, entry in extracts:
            if entry.get("routes") or entry.get("mounts"):
                used[relative] = digest
            routes += [{**r, "file": relative} for r in entry.get("routes") or []]
            mounts += [{**m, "file": relative} for m in entry.get("mounts") or []]
        for relative, digest in read:
            used[relative] = digest
        scanned = scanned + read
        result["openapi"] = documents
        result["routes"], result["more"] = _bounded(sorted(routes, key=lambda r: (r["path"], r["method"], r["file"])))
        result["mounts"] = mounts[:50]
        partial = partial or cut
    else:
        extracts, scanned, partial = _sources(root, scope, _ENV_HINT, "config_vars", deadline)
        variables: List[Dict[str, Any]] = []
        for relative, digest, entry in extracts:
            if entry:
                used[relative] = digest
            variables += [{**_shown_default(r), "file": relative} for r in entry]
        variables.sort(key=lambda r: (r["name"], r["file"], r["line"]))
        result["variables"], result["more"] = _bounded(variables)
    result["files"] = [{"path": p, "sha256": d} for p, d in sorted(used.items())[:MAX_FILES_LISTED]]
    result["files_scanned"] = len(scanned)
    result["scanned_sha256"] = _digest_of(scanned)
    return {"files": [], "partial": bool(partial), "seeds_matched": [], "seeds_unmatched": [], "lookup": result}
