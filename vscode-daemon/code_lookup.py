"""Where a name is defined and who uses it: ``workspace.related`` with ``lookup``.

The agents' code navigation tools (``backend/app/services/tools/
workspace_navigation.py``: ``workspace__definition`` and
``workspace__references``) ask the existing ``workspace.related`` subject
with an optional ``lookup`` field instead of a new subject; a daemon that
announces the ``lookup`` feature on ``workspace.related``
(``abilities.FEATURES``) answers here, one from before it ignores the field
and answers an empty walk without ``lookup``, which the backend reads as "not
served".

The graph is the code graph's (``code_graph``): the repository map's
candidate files and guards (no folder a listing skips, no hidden file, no
file that may hold secrets, no symlink out of the project, regular files
only), its parse cache (each file parsed once per sha256) and its edge rule
(``repo_map.reference_edges``). Nothing new is stored.

Request: ``{"workspace_root", "seeds": [], "lookup": {"kind": "definition" |
"references", "name"?: an identifier, "path"?: a project-relative file},
"max_files"?: default DEFAULT_FILES, at most MAX_FILES}``. A definition needs
``name`` (``path`` narrows it to one file); references need ``name``, ``path``
or both: with ``path`` alone, the files that use the names that file defines
(who uses the module); with both, who uses that file's ``name``.

Reply: the ``workspace.related`` fields (``files`` empty, ``partial``,
``seeds_matched``, ``seeds_unmatched``) and ``lookup``: ``{"kind", "name",
"path", "path_found", "definitions": [{"path", "sha256", "line", "depth",
"signature", "parameters"?}], "references": [{"path", "sha256", "names",
"lines", "more_lines"}], "more", "common_name"}``. The signature is the
repository map's (redacted: no default's literal); for a top-level Python
function or class whose signature is known (``signatures.describe``), its
parameters too. A reference's lines are the lines that name it, at most
``MAX_LINES`` a file; ``more`` counts the files left out past ``max_files``.
A name every program uses (``repo_map._COMMON_NAMES``: ``get``, ``len``…) has
no references in the parse: ``common_name`` says so. Only paths, names, line
numbers and hashes leave the machine, never a file's contents.
"""

from __future__ import annotations

import ast
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import code_graph
import project_facts
import repo_map
import signatures
import workspace_read

KINDS = ("definition", "references")
DEFAULT_FILES = 20
MAX_FILES = 30
MAX_LINES = 10
MAX_NAME = 200
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")


def _request(lookup: Any, data: Dict[str, Any]) -> Tuple[str, str, str, int]:
    if not isinstance(lookup, dict) or lookup.get("kind") not in KINDS:
        raise workspace_read.ReadError(400, "lookup.kind must be definition or references.")
    kind = lookup["kind"]
    name = lookup.get("name")
    name = name.strip() if isinstance(name, str) else ""
    if name and (len(name) > MAX_NAME or not _IDENTIFIER.match(name)):
        raise workspace_read.ReadError(400, "lookup.name must be one identifier.")
    raw_path = lookup.get("path")
    path = ""
    if raw_path is not None:
        if not isinstance(raw_path, str):
            raise workspace_read.ReadError(400, "lookup.path must be a path.")
        path = code_graph._normalize(raw_path)
        if not path or path.endswith("/"):
            raise workspace_read.ReadError(400, "lookup.path must be a file inside the project.")
    if kind == "definition" and not name:
        raise workspace_read.ReadError(400, "A definition lookup needs a name.")
    if kind == "references" and not (name or path):
        raise workspace_read.ReadError(400, "A references lookup needs a name or a path.")
    try:
        max_files = int(data.get("max_files") or DEFAULT_FILES)
    except (TypeError, ValueError):
        raise workspace_read.ReadError(400, "max_files must be a number.") from None
    return kind, name, path, max(1, min(max_files, MAX_FILES))


def _parameters(raw: bytes, relative: str, name: str, depth: int) -> Optional[Dict[str, Any]]:
    """The parameters ``workspace.signatures`` would report for a top-level
    Python *name*, when its signature is known; else ``None``."""
    if depth != 0 or not relative.endswith(".py"):
        return None
    try:
        tree = ast.parse(raw.decode("utf-8", errors="replace"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    described = signatures.describe(tree, [name]).get(name) or {}
    if described.get("status") != "found":
        return None
    return {k: v for k, v in described.items() if k not in ("status", "line")}


def _lines(raw: bytes, names: Set[str]) -> Tuple[List[int], int]:
    """The line numbers that name any of *names*, at most ``MAX_LINES``, and how many more."""
    pattern = re.compile(r"(?<![\w$])(?:" + "|".join(sorted(map(re.escape, names))) + r")(?![\w$])")
    hits = [n for n, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1) if pattern.search(line)]
    return hits[:MAX_LINES], max(0, len(hits) - MAX_LINES)


def lookup(workspace_path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """The reply to ``workspace.related`` when the request carries ``lookup``."""
    asked = data.get("lookup")
    if isinstance(asked, dict) and asked.get("kind") in project_facts.KINDS:
        return project_facts.facts(workspace_path, data)  # §5.9.
    root = workspace_read._root(workspace_path)
    kind, name, path, max_files = _request(asked, data)
    deadline = time.monotonic() + code_graph.DEADLINE_SECONDS
    files, files_capped = repo_map._candidates(root)
    contents, too_large = repo_map.read_sources(files)
    cache_path, cache = repo_map._load_cache(root)
    parsed, late = repo_map.parse_sources(contents, cache, deadline)
    repo_map._save_cache(cache_path, cache)
    raw = {relative: (digest, blob) for relative, digest, blob in contents}
    result: Dict[str, Any] = {
        "kind": kind, "name": name or None, "path": path or None,
        "path_found": (path in parsed) if path else None,
        "definitions": [], "references": [], "more": 0,
        "common_name": bool(name) and name in repo_map._COMMON_NAMES,
    }
    cut = False
    if kind == "definition":
        found = []
        for relative in sorted(parsed):
            if path and relative != path:
                continue
            for def_name, signature, line, depth in parsed[relative]["defs"]:
                if def_name == name:
                    found.append((depth, relative, line, signature))
        found.sort()
        for depth, relative, line, signature in found[:max_files]:
            digest, source = raw[relative]
            row = {"path": relative, "sha256": digest, "line": line, "depth": depth, "signature": signature}
            parameters = _parameters(source, relative, name, depth)
            if parameters is not None:
                row["parameters"] = parameters
            result["definitions"].append(row)
        result["more"] = max(0, len(found) - max_files)
    elif not path or path in parsed:
        wanted: Dict[str, Set[str]] = {}
        if path:
            defined = {d[0] for d in parsed[path]["defs"]}
            names = {name} & defined if name else defined
            found_edges, _defines, cut = repo_map.reference_edges(parsed, deadline)
            for source, ref, _count, targets in found_edges:
                if ref in names and path in targets:
                    wanted.setdefault(source, set()).add(ref)
        else:
            for relative in sorted(parsed):
                if name in parsed[relative]["refs"]:
                    wanted[relative] = {name}
        ordered = sorted(wanted, key=lambda p: (-len(wanted[p]), p))
        for relative in ordered[:max_files]:
            digest, source = raw[relative]
            lines, more_lines = _lines(source, wanted[relative])
            result["references"].append({
                "path": relative, "sha256": digest, "names": sorted(wanted[relative])[: code_graph.MAX_NAMES],
                "lines": lines, "more_lines": more_lines,
            })
        result["more"] = max(0, len(ordered) - max_files)
    return {
        "files": [], "partial": files_capped or too_large or late or cut,
        "seeds_matched": [], "seeds_unmatched": [], "lookup": result,
    }
