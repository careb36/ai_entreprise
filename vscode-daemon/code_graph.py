"""The files related to a task's starting points: ``workspace.related``.

The backend asks it for the files around a task (issue #438): the paths the
approved architecture gives the task's modules and the files its earlier
runs wrote. The reply is the part of the project's reference graph around
those seeds: which files each one uses and which use it, with the names that
tie them, so the Developer's prompt can name the files a change could break.
The backend puts it in a ``## Related code`` section
(``backend/app/services/tools/code_graph.py``): context, never evidence.

The graph is the repository map's (``repo_map``), computed on demand: the
same candidate files and guards (no folder a listing skips, no hidden file,
no file that may hold secrets, no symlink out of the project, regular files
only), the same parse cache (each file parsed once per sha256), the same
parsers and the same edge rule (``repo_map.reference_edges``: a file that
uses a name another file defines points at it; a name defined in more than
``MAP_MAX_DEFINERS`` files gives no edge). Nothing new is stored.

Request: ``{"workspace_root", "seeds": [project-relative paths; one ending in
"/" or naming a folder stands for every candidate file under it; at most
MAX_SEEDS], "hops": 1 | 2 (default 1), "max_files": default 30, at most 60}``.

Reply: ``{"files": [{"path", "sha256", "hop", "seed", "uses": [{"path",
"names"}], "used_by": [...], "more_uses", "more_used_by"}], "partial",
"seeds_matched", "seeds_unmatched"}``. At most ``MAX_NAMES`` names per edge
and ``MAX_EDGES`` edges per file (the ones tied by most names first); the
``more_*`` counts say how many edges were left out. ``partial`` says the
project was larger than the walk reads (files, bytes), ``max_files`` cut the
walk, or the deadline (``DEADLINE_SECONDS``, inside the backend's wait)
stopped the parsing. Only paths, names and hashes leave the machine, never
a file's contents.

``workspace.graph`` (issue #438, second slice) reports the same parse for
the backend to store: each file's sha256 with the definitions it holds
(name, line, depth) and the names it uses (name, count), for the files whose
sha256 differs from the one the backend says it holds. Request:
``{"workspace_root", "known": {path: sha256} (at most REPORT_MAX_KNOWN),
"max_files": default REPORT_DEFAULT_FILES, at most REPORT_MAX_FILES}``.
Reply: ``{"files": [{"path", "sha256", "status", "defs", "refs",
"truncated"}], "unchanged", "removed", "more", "partial"}``. A file whose
sha256 the backend already holds is neither parsed nor sent; ``removed``
names the known paths that are no longer candidate files (none when the
file cap cut the listing, since a file past it may still be there);
``more`` says the reply stopped at ``max_files``, ``REPORT_MAX_BYTES`` or
the deadline and the backend should ask again with what it now holds. At
most ``MAX_FILE_DEFS`` definitions and ``MAX_FILE_REFS`` names a file (the
most used first), ``truncated`` saying so. Names, lines and hashes only.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Set, Tuple

import repo_map
import workspace_read

MAX_SEEDS = 50
DEFAULT_FILES = 30
MAX_FILES = 60
MAX_NAMES = 8
MAX_EDGES = 10
# Inside the backend's wait (``services/tools/code_graph.TIMEOUT_SECONDS``).
DEADLINE_SECONDS = 8.0
REPORT_MAX_KNOWN = 5000
REPORT_DEFAULT_FILES = 200
REPORT_MAX_FILES = 500
REPORT_MAX_BYTES = 512 * 1024
MAX_FILE_DEFS = 300
MAX_FILE_REFS = 300


def _request(data: Dict[str, Any]) -> Tuple[List[str], int, int]:
    seeds = data.get("seeds")
    if not isinstance(seeds, list) or not all(isinstance(s, str) for s in seeds):
        raise workspace_read.ReadError(400, "seeds must be a list of paths.")
    if len(seeds) > MAX_SEEDS:
        raise workspace_read.ReadError(400, f"At most {MAX_SEEDS} seeds may be asked at once.")
    try:
        hops = int(data.get("hops") or 1)
        max_files = int(data.get("max_files") or DEFAULT_FILES)
    except (TypeError, ValueError):
        raise workspace_read.ReadError(400, "hops and max_files must be numbers.") from None
    return seeds, max(1, min(hops, 2)), max(1, min(max_files, MAX_FILES))


def _normalize(seed: str) -> str:
    """The seed as a project-relative POSIX path (a trailing ``/`` kept), or
    ``""`` for one that names the whole project or leaves it."""
    text = seed.strip().replace("\\", "/")
    folder = text.endswith("/")
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or text.startswith("/") or ".." in parts:
        return ""
    return "/".join(parts) + ("/" if folder else "")


def match_seeds(seeds: List[str], paths: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """The candidate *paths* the *seeds* name (a file, or every file under a
    folder), and the seeds that matched and did not."""
    chosen: List[str] = []
    matched: List[str] = []
    unmatched: List[str] = []
    known = set(paths)
    for seed in seeds:
        path = _normalize(seed)
        if not path:
            unmatched.append(seed)
            continue
        if path in known:
            hits = [path]
        else:
            prefix = path if path.endswith("/") else path + "/"
            hits = [p for p in paths if p.startswith(prefix)]
        (matched if hits else unmatched).append(seed)
        chosen.extend(h for h in hits if h not in chosen)
    return chosen, matched, unmatched


def _edges(found: List[Tuple[str, str, int, List[str]]]) -> Tuple[Dict[str, Dict[str, Set[str]]], Dict[str, Dict[str, Set[str]]]]:
    uses: Dict[str, Dict[str, Set[str]]] = {}
    used_by: Dict[str, Dict[str, Set[str]]] = {}
    for source, name, _count, targets in found:
        for target in targets:
            uses.setdefault(source, {}).setdefault(target, set()).add(name)
            used_by.setdefault(target, {}).setdefault(source, set()).add(name)
    return uses, used_by


def walk(
    seeds: List[str], uses: Dict[str, Dict[str, Set[str]]], used_by: Dict[str, Dict[str, Set[str]]],
    hops: int, max_files: int,
) -> Tuple[Dict[str, int], bool]:
    """Each file reached from *seeds* within *hops* edges, either direction,
    with its distance; at most *max_files*, the closer first and, at the same
    distance, the more tightly tied (more names to the files before it)."""
    reached: Dict[str, int] = {}
    for path in sorted(seeds)[:max_files]:
        reached[path] = 0
    cut = len(seeds) > max_files
    frontier = list(reached)
    for hop in range(1, hops + 1):
        if cut:
            break
        ties: Dict[str, int] = {}
        for path in frontier:
            for graph in (uses, used_by):
                for other, names in graph.get(path, {}).items():
                    if other not in reached:
                        ties[other] = ties.get(other, 0) + len(names)
        frontier = []
        for other in sorted(ties, key=lambda p: (-ties[p], p)):
            if len(reached) >= max_files:
                cut = True
                break
            reached[other] = hop
            frontier.append(other)
    return reached, cut


def _listed(edges: Dict[str, Set[str]]) -> Tuple[List[Dict[str, Any]], int]:
    rows = sorted(edges.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = [{"path": path, "names": sorted(names)[:MAX_NAMES]} for path, names in rows[:MAX_EDGES]]
    return shown, max(0, len(rows) - MAX_EDGES)


def related(workspace_path: str, data: Dict[str, Any], resolve=None) -> Dict[str, Any]:
    """The reply to ``workspace.related`` for the project in *workspace_path*."""
    root = workspace_read._root(workspace_path)
    seeds, hops, max_files = _request(data)
    started = time.monotonic()
    deadline = started + DEADLINE_SECONDS
    files, files_capped = repo_map._candidates(root)
    chosen, matched, unmatched = match_seeds(seeds, [relative for relative, _ in files])
    reply: Dict[str, Any] = {
        "files": [], "partial": files_capped, "seeds_matched": matched, "seeds_unmatched": unmatched,
    }
    if not chosen:
        return reply
    cache_path, cache = repo_map._load_cache(root)
    contents, too_large = repo_map.read_sources(files)
    digests = {relative: digest for relative, digest, _raw in contents}
    parsed, late = repo_map.parse_sources(contents, cache, deadline)
    found, _defines, cut = repo_map.reference_edges(parsed, deadline)
    repo_map._save_cache(cache_path, cache)
    uses, used_by = _edges(found)
    reached, walk_cut = walk([p for p in chosen if p in parsed], uses, used_by, hops, max_files)
    for path in sorted(reached, key=lambda p: (reached[p], p)):
        shown_uses, more_uses = _listed(uses.get(path, {}))
        shown_used_by, more_used_by = _listed(used_by.get(path, {}))
        reply["files"].append({
            "path": path, "sha256": digests.get(path), "hop": reached[path], "seed": reached[path] == 0,
            "uses": shown_uses, "used_by": shown_used_by,
            "more_uses": more_uses, "more_used_by": more_used_by,
        })
    reply["partial"] = files_capped or too_large or late or cut or walk_cut
    return reply


async def handle_related(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.related`` → the files around the seeds, with the names that tie them."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, related)


def _report_request(data: Dict[str, Any]) -> Tuple[Dict[str, str], int]:
    known = data.get("known")
    known = {} if known is None else known
    if not isinstance(known, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in known.items()
    ):
        raise workspace_read.ReadError(400, "known must map paths to sha256.")
    if len(known) > REPORT_MAX_KNOWN:
        raise workspace_read.ReadError(400, f"At most {REPORT_MAX_KNOWN} known files may be sent.")
    try:
        max_files = int(data.get("max_files") or REPORT_DEFAULT_FILES)
    except (TypeError, ValueError):
        raise workspace_read.ReadError(400, "max_files must be a number.") from None
    return known, max(1, min(max_files, REPORT_MAX_FILES))


def _file_row(relative: str, digest: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    defs = [[name, line, depth] for name, _sig, line, depth in entry.get("defs") or []]
    refs = sorted((entry.get("refs") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    status = "unparsed" if entry.get("error") else "refs_skipped" if entry.get("refs_skipped") else "parsed"
    return {
        "path": relative, "sha256": digest, "status": status,
        "defs": defs[:MAX_FILE_DEFS], "refs": [[name, count] for name, count in refs[:MAX_FILE_REFS]],
        "truncated": len(defs) > MAX_FILE_DEFS or len(refs) > MAX_FILE_REFS,
    }


def graph_report(workspace_path: str, data: Dict[str, Any], resolve=None) -> Dict[str, Any]:
    """The reply to ``workspace.graph``: the parse of each file whose sha256
    the backend does not hold, and the known files that are gone."""
    root = workspace_read._root(workspace_path)
    known, max_files = _report_request(data)
    deadline = time.monotonic() + DEADLINE_SECONDS
    files, files_capped = repo_map._candidates(root)
    contents, too_large = repo_map.read_sources(files)
    current = {relative for relative, _digest, _raw in contents}
    if files_capped:
        removed: List[str] = []
    else:
        # Past the byte cap a candidate is unread, not gone.
        present = {relative for relative, _ in files} if too_large else current
        removed = sorted(path for path in known if path not in present)
    changed = [item for item in contents if known.get(item[0]) != item[1]]
    cache_path, cache = repo_map._load_cache(root)
    rows: List[Dict[str, Any]] = []
    used = 0
    more = late = False
    for item in changed:
        parsed, late = repo_map.parse_sources([item], cache, deadline)
        if late:
            more = True
            break
        row = _file_row(item[0], item[1], parsed[item[0]])
        size = len(json.dumps(row))
        if rows and (len(rows) >= max_files or used + size > REPORT_MAX_BYTES):
            more = True
            break
        rows.append(row)
        used += size
    repo_map._save_cache(cache_path, cache)
    return {
        "files": rows, "unchanged": len(contents) - len(changed), "removed": removed,
        "more": more, "partial": files_capped or too_large or late,
    }


async def handle_graph(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.graph`` → the parse of the files whose sha256 changed, for the backend to store."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, graph_report)


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (("workspace.related", handle_related), ("workspace.graph", handle_graph))
