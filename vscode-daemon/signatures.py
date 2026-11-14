"""The signatures of named Python definitions: ``workspace.signatures``.

The Verifier's ``grounded`` check (``backend/app/services/evidence/signatures.py``)
compares the calls a delivery makes to the project's own functions and
classes with their signatures. The backend asks this subject, at delivery
time, for the names a delivered Python file imports from project files the
delivery did not write, and records the reply on the evidence as an
observation; verification never asks the daemon again.

Request: ``{"workspace_root": <project or task worktree>, "wanted": [{"path":
"pkg/mod.py", "names": ["f", "C"]}, ...]}``, at most ``MAX_PATHS`` paths and
``MAX_NAMES`` names in all (more is refused with the reason, nothing read).

Reply: ``{"files": [{"path", "status": "read" | "unreadable" | "does not
parse" | "refused", "sha256", "names": {name: {"status": "found" | "missing"
| "unknown", ...}}}]}``. A name is ``found`` only when the module binds it
exactly once, with an undecorated ``def`` or a class whose constructor is
known: its own undecorated ``__init__`` (less the instance), or none at all
with no base but ``object``, no metaclass and no ``__new__``. A decorator, an
inherited constructor, a second binding (an assignment, an import, a
conditional definition) make it ``unknown``: the check never guesses.
``missing``: the module binds no such name (it may come from a star import;
the check leaves it alone too).

Python only: JavaScript accepts any number of arguments, so a count proves
nothing there. The reply carries parameter *names* and whether each has a
default, never a default's value, so no literal leaves the machine.

The file is read the way the repository map reads one (``repo_map``): never
outside the project or through a symlink that leads out, never into VCS
metadata (the daemon's resolver), never a file that may hold secrets
(``workspace_read.is_sensitive``), only a regular file of at most
``repo_map.MAP_MAX_FILE_BYTES``.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import repo_map
import workspace_read

MAX_PATHS = 50
MAX_NAMES = 400
_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _parameters(args: ast.arguments, drop_first: bool) -> Optional[Dict[str, Any]]:
    positional = [a.arg for a in args.posonlyargs + args.args]
    posonly = len(args.posonlyargs)
    defaults = len(args.defaults)
    if drop_first:
        if not positional:
            return None  # ``def __init__(*args)``: no instance to drop.
        positional = positional[1:]
        posonly = max(0, posonly - 1)
        defaults = min(defaults, len(positional))
    return {
        "positional_only": positional[:posonly],
        "positional": positional[posonly:],
        "positional_defaults": defaults,
        "varargs": args.vararg is not None,
        "keyword_only": [
            {"name": arg.arg, "default": default is not None}
            for arg, default in zip(args.kwonlyargs, args.kw_defaults)
        ],
        "varkw": args.kwarg is not None,
    }


def _bindings(tree: ast.Module) -> Dict[str, List[ast.AST]]:
    """Every module-level binding of each name: its top-level statements and
    the blocks they hold (``if``, ``try``, ``with``, loops), never a function
    or class body."""
    found: Dict[str, List[ast.AST]] = {}

    def add(name: str, node: ast.AST) -> None:
        found.setdefault(name, []).append(node)

    def targets(node: ast.AST, owner: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                add(sub.id, owner)

    def visit(statements: List[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, _KINDS):
                add(node.name, node)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    add((alias.asname or alias.name).split(".", 1)[0], node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    targets(target, node)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets(node.target, node)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets(node.target, node)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        targets(item.optional_vars, node)
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    targets(target, node)
            for sub in ast.walk(node):
                if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                    add(sub.target.id, node)
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    visit(block)
            for handler in getattr(node, "handlers", None) or []:
                if handler.name:
                    add(handler.name, node)
                visit(handler.body)

    visit(tree.body)
    return found


def _class_signature(node: ast.ClassDef) -> Optional[Dict[str, Any]]:
    if node.decorator_list or node.keywords:
        return None
    own: List[ast.AST] = []
    for child in node.body:
        names: Set[str] = set()
        if isinstance(child, _KINDS):
            names.add(child.name)
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            for target in child.targets if isinstance(child, ast.Assign) else [child.target]:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        if names & {"__new__", "__signature__"}:
            return None  # The constructor is not ``__init__``'s alone.
        if "__init__" in names:
            own.append(child)
    if len(own) > 1:
        return None
    if own:
        init = own[0]
        if not isinstance(init, (ast.FunctionDef, ast.AsyncFunctionDef)) or init.decorator_list:
            return None
        return _parameters(init.args, drop_first=True)
    if any(not (isinstance(base, ast.Name) and base.id == "object") for base in node.bases):
        return None  # The constructor is inherited: never guessed.
    return _parameters(ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), False)


def describe(tree: ast.Module, names: List[str]) -> Dict[str, Dict[str, Any]]:
    """Each of *names* as the module binds it: ``found`` with its signature,
    ``unknown`` or ``missing``."""
    bindings = _bindings(tree)
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        bound = bindings.get(name) or []
        if not bound:
            out[name] = {"status": "missing"}
            continue
        node = bound[0]
        if len(bound) > 1 or not isinstance(node, _KINDS) or node not in tree.body:
            out[name] = {"status": "unknown", "reason": "bound more than once or not by a definition"}
            continue
        if isinstance(node, ast.ClassDef):
            signature = _class_signature(node)
            kind = "class"
        else:
            signature = None if node.decorator_list else _parameters(node.args, False)
            kind = "function"
        if signature is None:
            out[name] = {"status": "unknown", "kind": kind, "line": node.lineno,
                         "reason": "decorated, or a constructor not its own"}
            continue
        out[name] = {"status": "found", "kind": kind, "line": node.lineno, **signature}
    return out


def _wanted(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    wanted = data.get("wanted")
    if not isinstance(wanted, list):
        raise workspace_read.ReadError(400, "wanted must be a list of {path, names}.")
    if len(wanted) > MAX_PATHS:
        raise workspace_read.ReadError(400, f"At most {MAX_PATHS} paths may be asked at once.")
    rows: List[Dict[str, Any]] = []
    total = 0
    for row in wanted:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise workspace_read.ReadError(400, "Each wanted entry needs a path.")
        names = [n for n in row.get("names") or [] if isinstance(n, str) and n.isidentifier()]
        total += len(names)
        if total > MAX_NAMES:
            raise workspace_read.ReadError(400, f"At most {MAX_NAMES} names may be asked at once.")
        rows.append({"path": row["path"], "names": sorted(set(names))})
    return rows


def _one(root: Path, relative: str, names: List[str], resolve) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"path": relative}
    if not relative.endswith(".py"):
        return {**entry, "status": "refused", "reason": "not a Python file"}
    target = resolve(str(root), relative)
    contained = workspace_read._inside(root, target) if target is not None else None
    if contained is None:
        return {**entry, "status": "refused", "reason": "not a path inside the project"}
    if workspace_read.is_sensitive(root, contained):
        return {**entry, "status": "refused", "reason": "a file that may hold secrets"}
    raw = repo_map.read_source(contained)
    if raw is None:
        return {**entry, "status": "unreadable", "reason": "missing, not a regular file, or too large"}
    entry["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        tree = ast.parse(raw.decode("utf-8", errors="replace"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {**entry, "status": "does not parse"}
    return {**entry, "status": "read", "names": describe(tree, names)}


def signatures(workspace_path: str, data: Dict[str, Any], resolve) -> Dict[str, Any]:
    """The reply to ``workspace.signatures`` for the project in *workspace_path*."""
    root = workspace_read._root(workspace_path)
    rows = _wanted(data)
    return {"files": [_one(root, row["path"], row["names"], resolve) for row in rows]}


async def handle_signatures(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.signatures`` → per file its sha256 and each name's signature."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, signatures)


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (("workspace.signatures", handle_signatures),)
