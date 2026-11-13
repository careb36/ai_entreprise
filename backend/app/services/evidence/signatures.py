"""Calls that contradict a signature: the symbol part of the ``grounded`` check.

A model can call a function of the project with arguments it does not take
(Gorilla's APIBench measured how often models invent API calls; the same
happens inside one repository). The repository map (``workspace.map``)
reaches the prompt as text; for the Verifier the signatures must be
evidence, so the backend asks the daemon, at delivery time, for the
signatures of the names a delivered Python file imports from project files
the delivery did not write (``workspace.signatures``,
``vscode-daemon/signatures.py``; ``services/tools/signature_lookup.py``
records the reply as ``signatures`` on the evidence, with the daemon's sha256
per file). A delivered file's own definitions are read from the delivered
text here.

For every call in a delivered Python file whose callee is such a name (``f``
from ``from a.b import f``, ``a.b.f`` after ``import a.b``, ``m.f`` after
``import a.b as m``) or a module-level definition of a delivered file, the
positional count is compared with the signature's bounds, each keyword with
its parameter names (unless ``**kwargs``), and the required keyword-only
parameters with the keywords passed. Left alone: a call unpacking ``*x`` or
``**x``, a name bound more than once in the file (rebound, shadowed by a
parameter or a local), a signature the daemon reported ``unknown`` (a
decorator, an inherited constructor) or ``missing`` (a star import may
provide it), and an import that names no single project file.

Every mismatch is a **finding**, never a refutation: the map is partial and
Python resolves names at run time. The check reads observations only (the
daemon's reply, the delivered text); a model's claim is never used. Python
only: JavaScript accepts any number of arguments, so a count proves nothing
there; Go and Rust are out of scope.
"""

from __future__ import annotations

import ast
import posixpath
import sys
from collections.abc import Iterable
from typing import Any

from services.evidence.imports import ProjectIndex

# The daemon refuses a request over these bounds; the backend asks within them.
MAX_PATHS = 50
MAX_NAMES = 400
MISMATCH_LIMIT = 20
_STDLIB = frozenset(sys.stdlib_module_names)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# ---------------------------------------------------------------------------
# Signatures of a module's definitions (the daemon applies the same rules)
# ---------------------------------------------------------------------------


def _parameters(args: ast.arguments, drop_first: bool) -> dict[str, Any] | None:
    positional = [a.arg for a in args.posonlyargs + args.args]
    posonly = len(args.posonlyargs)
    defaults = len(args.defaults)
    if drop_first:
        if not positional:
            return None
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
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        ],
        "varkw": args.kwarg is not None,
    }


def _module_bindings(tree: ast.Module) -> dict[str, int]:
    """How many module-level statements bind each name (blocks included)."""
    counts: dict[str, int] = {}

    def add(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def visit(statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, _DEFS):
                add(node.name)
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    add((alias.asname or alias.name).split(".", 1)[0])
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(
                    sub.ctx, (ast.Store, ast.Del)
                ):
                    add(sub.id)
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    visit(block)
            for handler in getattr(node, "handlers", None) or []:
                if handler.name:
                    add(handler.name)
                visit(handler.body)

    visit(tree.body)
    return counts


def _class_signature(node: ast.ClassDef) -> dict[str, Any] | None:
    if node.decorator_list or node.keywords:
        return None
    own: list[ast.stmt] = []
    for child in node.body:
        names: set[str] = set()
        if isinstance(child, _DEFS):
            names.add(child.name)
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        if names & {"__new__", "__signature__"}:
            return None
        if "__init__" in names:
            own.append(child)
    if len(own) > 1:
        return None
    if own:
        init = own[0]
        if (
            not isinstance(init, (ast.FunctionDef, ast.AsyncFunctionDef))
            or init.decorator_list
        ):
            return None
        return _parameters(init.args, drop_first=True)
    if any(
        not (isinstance(base, ast.Name) and base.id == "object") for base in node.bases
    ):
        return None
    return {
        "positional_only": [],
        "positional": [],
        "positional_defaults": 0,
        "varargs": False,
        "keyword_only": [],
        "varkw": False,
    }


def describe(tree: ast.Module) -> dict[str, dict[str, Any]]:
    """Every module-level definition bound once, as ``found`` or ``unknown``."""
    counts = _module_bindings(tree)
    out: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, _DEFS) or counts.get(node.name, 0) != 1:
            continue
        if isinstance(node, ast.ClassDef):
            signature, kind = _class_signature(node), "class"
        else:
            signature = None if node.decorator_list else _parameters(node.args, False)
            kind = "function"
        if signature is None:
            out[node.name] = {"status": "unknown", "kind": kind, "line": node.lineno}
        else:
            out[node.name] = {
                "status": "found",
                "kind": kind,
                "line": node.lineno,
                **signature,
            }
    return out


def render(name: str, signature: dict[str, Any]) -> str:
    """``f(a, /, b, c=…, *args, d, e=…, **kwargs)`` from a reported signature."""
    parts: list[str] = list(signature.get("positional_only") or [])
    if parts:
        parts.append("/")
    positional = list(signature.get("positional") or [])
    names = list(signature.get("positional_only") or []) + positional
    first_default = len(names) - int(signature.get("positional_defaults") or 0)
    for index, param in enumerate(names):
        if index < len(signature.get("positional_only") or []):
            if index >= first_default:
                parts[index] += "=…"
            continue
        parts.append(param + ("=…" if index >= first_default else ""))
    keyword_only = signature.get("keyword_only") or []
    if signature.get("varargs"):
        parts.append("*args")
    elif keyword_only:
        parts.append("*")
    parts.extend(
        str(kw.get("name")) + ("=…" if kw.get("default") else "") for kw in keyword_only
    )
    if signature.get("varkw"):
        parts.append("**kwargs")
    return f"{name}({', '.join(parts)})"


def mismatch(signature: dict[str, Any], positional: int, keywords: list[str]) -> str:
    """Why a call with *positional* arguments and *keywords* cannot bind; ``""`` if it can."""
    posonly = list(signature.get("positional_only") or [])
    names = posonly + list(signature.get("positional") or [])
    required = len(names) - int(signature.get("positional_defaults") or 0)
    keyword_only = {
        str(kw.get("name")): bool(kw.get("default"))
        for kw in signature.get("keyword_only") or []
    }
    varkw = bool(signature.get("varkw"))
    problems: list[str] = []
    if positional > len(names) and not signature.get("varargs"):
        problems.append(f"takes at most {len(names)} positional, {positional} given")
    filled = set(names[:positional])
    for keyword in keywords:
        if keyword in posonly and not varkw:
            problems.append(f"'{keyword}' is positional-only")
        elif keyword in names and keyword not in posonly:
            if keyword in filled:
                problems.append(f"'{keyword}' given twice")
            filled.add(keyword)
        elif keyword in keyword_only:
            filled.add(keyword)
        elif not varkw:
            problems.append(f"no parameter '{keyword}'")
    missing = [param for param in names[:required] if param not in filled]
    missing += [
        name
        for name, default in keyword_only.items()
        if not default and name not in filled
    ]
    if missing:
        problems.append("missing " + ", ".join(f"'{name}'" for name in missing))
    return "; ".join(problems)


# ---------------------------------------------------------------------------
# What a delivered file calls, and where each callee is defined
# ---------------------------------------------------------------------------


def _module_path(path: str) -> list[str]:
    parts = path[: -len(".py")].split("/")
    return parts[:-1] if parts[-1] == "__init__" else parts


def module_file(index: ProjectIndex, dotted: str, *, exact: bool = False) -> str | None:
    """The one project ``.py`` file that is module *dotted*; ``None`` when none or several.

    A standard-library name (``json``, ``os.path``) matches only a file at
    that exact path: ``app/json.py`` is not what ``import json`` loads."""
    wanted = dotted.split(".")
    exact = exact or wanted[0] in _STDLIB
    exact_hits: list[str] = []
    suffix_hits: list[str] = []
    for path in sorted(index.files):
        if not path.endswith(".py"):
            continue
        parts = _module_path(path)
        if parts == wanted:
            exact_hits.append(path)
        elif not exact and len(parts) > len(wanted) and parts[-len(wanted) :] == wanted:
            suffix_hits.append(path)
    if len(exact_hits) == 1:
        return exact_hits[0]
    if not exact_hits and len(suffix_hits) == 1:
        return suffix_hits[0]
    return None


def _binding_counts(tree: ast.Module) -> dict[str, int]:
    """How often each name is bound anywhere in the file. Repeated ``import
    a.x`` / ``import a.y`` statements bind ``a`` once together."""
    counts: dict[str, int] = {}
    package_roots: set[str] = set()

    def add(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            add(node.id)
        elif isinstance(node, ast.arg):
            add(node.arg)
        elif isinstance(node, _DEFS):
            add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    add(alias.asname)
                else:
                    package_roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                add(alias.asname or alias.name)
        elif (
            isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                add(name)
    for root in package_roots:
        add(root)
    return counts


def _source_module(path: str, node: ast.ImportFrom) -> tuple[str, bool] | None:
    """The dotted module an ``ImportFrom`` names, and whether it is relative."""
    if not node.level:
        return (node.module, False) if node.module else None
    base = posixpath.dirname(path)
    for _ in range(node.level - 1):
        base = posixpath.dirname(base)
    parts = [part for part in base.split("/") if part]
    if node.module:
        parts.extend(node.module.split("."))
    return (".".join(parts), True) if parts else None


def _symbols(
    path: str, tree: ast.Module, index: ProjectIndex
) -> dict[str, tuple[str, ...]]:
    """What each name bound once in the file is: ``("def", path, name)`` for a
    module-level definition, ``("symbol", file, name)`` for a name imported
    from a project file, ``("module", file)`` and ``("package", dotted...)``
    for imported modules."""
    counts = _binding_counts(tree)
    table: dict[str, tuple[str, ...]] = {}
    packages: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _source_module(path, node)
            if source is None:
                continue
            dotted, relative = source
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "*" or counts.get(local) != 1:
                    continue
                as_module = module_file(index, f"{dotted}.{alias.name}", exact=relative)
                if as_module:
                    table[local] = ("module", as_module)
                    continue
                file = module_file(index, dotted, exact=relative)
                if file:
                    table[local] = ("symbol", file, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    if counts.get(alias.asname) == 1:
                        file = module_file(index, alias.name)
                        if file:
                            table[alias.asname] = ("module", file)
                else:
                    root = alias.name.split(".", 1)[0]
                    packages.setdefault(root, set()).add(alias.name)
    for root, dotted in packages.items():
        if counts.get(root) == 1:
            table[root] = ("package", *sorted(dotted))
    for node in tree.body:
        if isinstance(node, _DEFS) and counts.get(node.name) == 1:
            table[node.name] = ("def", path, node.name)
    return table


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return parts[::-1]


def _callee(
    func: ast.expr, table: dict[str, tuple[str, ...]], index: ProjectIndex
) -> tuple[str, str] | None:
    """``(file, name)`` the call reaches, when the file reading can tell."""
    parts = _dotted(func)
    if not parts or parts[0] not in table:
        return None
    entry = table[parts[0]]
    if len(parts) == 1:
        if entry[0] in ("def", "symbol"):
            return entry[1], entry[2]
        return None
    if entry[0] == "module" and len(parts) == 2:
        return entry[1], parts[1]
    if entry[0] == "package":
        dotted = ".".join(parts[:-1])
        if dotted in entry[1:]:
            file = module_file(index, dotted)
            if file:
                return file, parts[-1]
    return None


def _python_files(files: Iterable[dict[str, Any]]) -> dict[str, ast.Module]:
    """The delivered Python files with their whole text, parsed."""
    parsed: dict[str, ast.Module] = {}
    for file in files:
        path = file.get("path")
        content = file.get("content")
        if (
            not isinstance(path, str)
            or not path.endswith(".py")
            or not isinstance(content, str)
        ):
            continue
        path = path.replace("\\", "/").removeprefix("./")
        try:
            parsed[path] = ast.parse(content)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
    return parsed


def _calls(
    files: list[dict[str, Any]], evidence: dict[str, Any] | None
) -> list[tuple[str, ast.Call, str, str, str]]:
    """Every call that reaches a project definition: ``(path, call, text, file, name)``."""
    parsed = _python_files(files)
    if not parsed:
        return []
    index = ProjectIndex.from_evidence(evidence, files)
    found = []
    for path, tree in parsed.items():
        table = _symbols(path, tree, index)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _callee(node.func, table, index)
            if target:
                found.append((path, node, ast.unparse(node.func), *target))
    return found


def wanted(
    evidence: dict[str, Any] | None, files: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """The names to ask the daemon for (in files the delivery did not write
    whole), within ``MAX_PATHS`` and ``MAX_NAMES``; the second value says
    whether the bounds cut the list."""
    delivered = set(_python_files(files))
    names: dict[str, set[str]] = {}
    for _path, _call, _text, file, name in _calls(files, evidence):
        if file not in delivered:
            names.setdefault(file, set()).add(name)
    asked: list[dict[str, Any]] = []
    total = 0
    capped = False
    for file in sorted(names):
        room = min(MAX_NAMES - total, len(names[file]))
        if len(asked) >= MAX_PATHS or room <= 0:
            capped = True
            break
        chosen = sorted(names[file])[:room]
        capped = capped or len(chosen) < len(names[file])
        asked.append({"path": file, "names": chosen})
        total += len(chosen)
    return asked, capped


def has_python(files: Iterable[dict[str, Any]]) -> bool:
    return any(
        isinstance(file.get("path"), str) and file["path"].endswith(".py")
        for file in files
    )


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _observed(evidence: dict[str, Any] | None) -> tuple[bool | None, dict[str, Any]]:
    record = (evidence or {}).get("signatures")
    if not isinstance(record, dict):
        return None, {}
    by_path: dict[str, Any] = {}
    for row in record.get("files") or []:
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            by_path[row["path"]] = row
    return bool(record.get("observed")), {
        "files": by_path,
        "reason": record.get("reason"),
    }


def audit(
    evidence: dict[str, Any] | None, files: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Each call to a project definition compared with its signature."""
    files = [file for file in files or [] if isinstance(file, dict)]
    observed, record = _observed(evidence)
    delivered = {path: describe(tree) for path, tree in _python_files(files).items()}
    mismatches: list[dict[str, Any]] = []
    checked = 0
    unobserved = 0
    for path, call, text, file, name in _calls(files, evidence):
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
            keyword.arg is None for keyword in call.keywords
        ):
            continue
        source: dict[str, Any]
        if file in delivered:
            signature = delivered[file].get(name)
            source = {"source": "delivered"}
        else:
            row = record.get("files", {}).get(file)
            if not isinstance(row, dict):
                unobserved += 1
                continue
            signature = (row.get("names") or {}).get(name)
            source = {"source": "daemon", "sha256": row.get("sha256")}
        if not isinstance(signature, dict) or signature.get("status") != "found":
            continue
        checked += 1
        keywords = [str(keyword.arg) for keyword in call.keywords]
        problem = mismatch(signature, len(call.args), keywords)
        if problem:
            mismatches.append(
                {
                    "path": path,
                    "line": call.lineno,
                    "callee": text,
                    "defined_in": file,
                    "passed": {"positional": len(call.args), "keywords": keywords},
                    "signature": render(name, signature),
                    "problem": problem,
                    **source,
                }
            )
    return {
        "observed": observed,
        "reason": record.get("reason"),
        "checked": checked,
        "unobserved": unobserved,
        "mismatches": mismatches,
    }
