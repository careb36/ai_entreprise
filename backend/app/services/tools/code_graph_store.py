"""The stored code knowledge graph (issue #438, second slice).

The daemon's ``workspace.graph`` (``vscode-daemon/code_graph.py``) reports,
for each file whose sha256 the backend does not already hold, the
definitions it parsed (name, line, depth) and the names the file uses. The
store keeps that per company and project folder, keyed by the sha256: a
sync sends the hashes it holds, the daemon parses and sends only the files
that changed, and each reported file replaces its own rows and nothing
else; a file the daemon says is gone is deleted. The ``references`` edge is
resolved at read time with the daemon's own rule (a file that uses a name
other files define points at them; a name defined in more than
``MAX_DEFINERS`` files gives no edge), so replacing one file never leaves an
edge to another file's old definitions.

What is stored is what the daemon observed (the sha256 is the daemon's,
computed from the bytes on the machine that holds them), never what a
producer claimed about its own work. Beside it, ``record_edges`` copies the
edges that come from records, each with its source: ``implements`` and
``depends_on`` from the approved architecture (a claim a person approved)
and ``verified`` from the Verifier's records of the daemon's report (an
observation). The stored graph feeds the ``## Related code`` section
(``code_graph.related_section``) as context, never evidence.

Two stores, one interface (the primitives of ``SqlStore``): the Postgres
tables (``models/code_graph.py``), and Neo4j (``code_graph_neo4j``) when
``CODE_GRAPH_STORE`` is ``neo4j`` and ``NEO4J_ENABLED`` is on. ``off``
stores nothing and the section is computed on demand, as in the first slice.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from config import settings
from models.code_graph import (
    FILE_STATUSES,
    CodeGraphDefinition,
    CodeGraphEdge,
    CodeGraphFile,
    CodeGraphReference,
)
from sqlalchemy import insert
from sqlmodel import Session, col, delete, func, select

from services.evidence import touched
from services.platform import memory_graph

SUBJECT = "workspace.graph"
TIMEOUT_SECONDS = 10.0
PAGE_FILES = 200
MAX_PAGES = 20
# No page starts past this; the next sync goes on from what was stored.
SYNC_BUDGET_SECONDS = 120.0
# The daemon's bounds (``vscode-daemon/code_graph.py``, ``repo_map.py``).
MAX_KNOWN = 5000
MAX_DEFINERS = 20
MAX_NAMES = 8
MAX_EDGES = 10
_CHUNK = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]
Edges = dict[str, dict[str, set[str]]]


class SyncError(RuntimeError):
    """The daemon's reply to ``workspace.graph`` was not one."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize(path: Any) -> str:
    """*path* as a project-relative POSIX path (a trailing ``/`` kept), or
    ``""`` for one that is not a string, names the project or leaves it."""
    if not isinstance(path, str):
        return ""
    text = path.strip().replace("\\", "/")
    folder = text.endswith("/")
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or text.startswith("/") or ".." in parts or ":" in parts[0]:
        return ""
    return "/".join(parts) + ("/" if folder else "")


def clean_files(files: Iterable[Any]) -> list[dict[str, Any]]:
    """The well-formed files of a daemon's report: a relative path, a sha256,
    and the definitions and references that are well formed. Anything else
    is dropped, never stored."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in files or []:
        if not isinstance(row, dict):
            continue
        path = normalize(row.get("path"))
        sha = row.get("sha256")
        if not path or path.endswith("/") or path in seen:
            continue
        if not isinstance(sha, str) or not _SHA256.match(sha):
            continue
        defs = [
            (d[0], d[1], d[2])
            for d in row.get("defs") or []
            if isinstance(d, list | tuple)
            and len(d) == 3
            and isinstance(d[0], str)
            and d[0]
            and all(isinstance(v, int) and not isinstance(v, bool) for v in d[1:])
        ]
        refs = [
            (r[0], r[1])
            for r in row.get("refs") or []
            if isinstance(r, list | tuple)
            and len(r) == 2
            and isinstance(r[0], str)
            and r[0]
            and isinstance(r[1], int)
            and not isinstance(r[1], bool)
        ]
        status = row.get("status")
        seen.add(path)
        out.append(
            {
                "path": path,
                "sha256": sha,
                "status": status if status in FILE_STATUSES else "parsed",
                "truncated": bool(row.get("truncated")),
                "defs": defs,
                "refs": refs,
            }
        )
    return out


def _chunks(values: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(values), _CHUNK):
        yield values[i : i + _CHUNK]


class SqlStore:
    """The graph in the Postgres tables (SQLite in the tests)."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def _files(self, company_id: int, root: str):
        return select(CodeGraphFile).where(
            CodeGraphFile.company_id == company_id,
            CodeGraphFile.workspace_root == root,
        )

    def known(self, company_id: int, root: str) -> dict[str, str]:
        """The sha256 held per path."""
        with Session(self.engine) as session:
            return {
                f.path: f.sha256 for f in session.exec(self._files(company_id, root))
            }

    def observed_at(self, company_id: int, root: str) -> datetime | None:
        """When the daemon last reported a file of this folder, or None."""
        with Session(self.engine) as session:
            return session.exec(
                select(func.max(CodeGraphFile.observed_at)).where(
                    CodeGraphFile.company_id == company_id,
                    CodeGraphFile.workspace_root == root,
                )
            ).one()

    def apply(
        self,
        company_id: int,
        root: str,
        files: list[dict[str, Any]],
        removed: list[str],
    ) -> tuple[int, int]:
        """Each reported file replaces its own rows; each removed path goes.
        Returns how many files were written and how many removed."""
        now = _now()
        gone = 0
        with Session(self.engine) as session:
            paths = [f["path"] for f in files] + [p for p in removed if p]
            for chunk in _chunks(paths):
                rows = session.exec(
                    self._files(company_id, root).where(
                        col(CodeGraphFile.path).in_(chunk)
                    )
                ).all()
                ids = [r.id for r in rows]
                gone += sum(1 for r in rows if r.path in removed)
                if ids:
                    session.exec(
                        delete(CodeGraphDefinition).where(
                            col(CodeGraphDefinition.file_id).in_(ids)
                        )
                    )
                    session.exec(
                        delete(CodeGraphReference).where(
                            col(CodeGraphReference.file_id).in_(ids)
                        )
                    )
                    session.exec(
                        delete(CodeGraphFile).where(col(CodeGraphFile.id).in_(ids))
                    )
            defs: list[dict[str, Any]] = []
            refs: list[dict[str, Any]] = []
            for f in files:
                row = CodeGraphFile(
                    company_id=company_id,
                    workspace_root=root,
                    path=f["path"],
                    sha256=f["sha256"],
                    status=f["status"],
                    truncated=f["truncated"],
                    observed_at=now,
                )
                session.add(row)
                session.flush()
                defs += (
                    {
                        "file_id": row.id,
                        "company_id": company_id,
                        "name": n,
                        "line": line,
                        "depth": depth,
                    }
                    for n, line, depth in f["defs"]
                )
                refs += (
                    {"file_id": row.id, "company_id": company_id, "name": n, "count": c}
                    for n, c in f["refs"]
                )
            # Plain rows, not ORM objects: a large project's first sync has
            # some hundred thousand of them.
            for model, rows in (
                (CodeGraphDefinition, defs),
                (CodeGraphReference, refs),
            ):
                if rows:
                    session.connection().execute(insert(model.__table__), rows)
            session.commit()
        return len(files), gone

    def _pairs(
        self, company_id: int, root: str, model: Any, column: Any, values: list[str]
    ):
        out: list[tuple[str, str]] = []
        with Session(self.engine) as session:
            for chunk in _chunks(sorted(set(values))):
                rows = session.exec(
                    select(CodeGraphFile.path, model.name)
                    .where(model.file_id == CodeGraphFile.id)
                    .where(
                        CodeGraphFile.company_id == company_id,
                        CodeGraphFile.workspace_root == root,
                        col(column).in_(chunk),
                    )
                ).all()
                out.extend((p, n) for p, n in rows)
        return out

    def references_of(self, company_id: int, root: str, paths: list[str]):
        """``(path, name)`` for each name the files *paths* use."""
        return self._pairs(
            company_id, root, CodeGraphReference, CodeGraphFile.path, paths
        )

    def definitions_of(self, company_id: int, root: str, paths: list[str]):
        """``(path, name)`` for each name the files *paths* define."""
        return self._pairs(
            company_id, root, CodeGraphDefinition, CodeGraphFile.path, paths
        )

    def definers(self, company_id: int, root: str, names: list[str]):
        """``(path, name)`` for each file that defines one of *names*."""
        return self._pairs(
            company_id, root, CodeGraphDefinition, CodeGraphDefinition.name, names
        )

    def referrers(self, company_id: int, root: str, names: list[str]):
        """``(path, name)`` for each file that uses one of *names*."""
        return self._pairs(
            company_id, root, CodeGraphReference, CodeGraphReference.name, names
        )

    def replace_edges(
        self, company_id: int, root: str, source: str, edges: list[dict[str, Any]]
    ) -> None:
        """The edges of *source* for this folder become *edges*."""
        now = _now()
        with Session(self.engine) as session:
            session.exec(
                delete(CodeGraphEdge).where(
                    CodeGraphEdge.company_id == company_id,
                    CodeGraphEdge.workspace_root == root,
                    CodeGraphEdge.source == source,
                )
            )
            session.add_all(
                CodeGraphEdge(
                    company_id=company_id,
                    workspace_root=root,
                    source=source,
                    recorded_at=now,
                    **edge,
                )
                for edge in edges
            )
            session.commit()


def store_for(engine: Any) -> Any:
    """The store ``CODE_GRAPH_STORE`` names, or None when it is ``off``."""
    choice = (settings.CODE_GRAPH_STORE or "").strip().lower()
    if choice == "off":
        return None
    if choice == "neo4j" and memory_graph.graph_enabled():
        from services.tools import code_graph_neo4j

        return code_graph_neo4j.Neo4jStore()
    return SqlStore(engine) if engine is not None else None


# --- the sync: the daemon's report into the store ------------------------------


def sync(
    store: Any,
    request_fn: Requester,
    company_id: int,
    root: str,
    on_page: Callable[[bool], None] | None = None,
) -> dict[str, Any]:
    """Ask the daemon for the files whose sha256 the store does not hold and
    store them, page by page (at most ``MAX_PAGES``, none started past
    ``SYNC_BUDGET_SECONDS``); *on_page* hears, after each stored page, whether
    the daemon said more is to come. Raises ``SyncError`` (or the requester's
    error) when the daemon does not answer with a report."""
    done = {"pages": 0, "replaced": 0, "removed": 0, "complete": False}
    deadline = time.monotonic() + SYNC_BUDGET_SECONDS
    for _ in range(MAX_PAGES):
        if done["pages"] and time.monotonic() >= deadline:
            break
        known = dict(sorted(store.known(company_id, root).items())[:MAX_KNOWN])
        reply = request_fn(
            SUBJECT,
            {"workspace_root": root, "known": known, "max_files": PAGE_FILES},
            TIMEOUT_SECONDS,
        )
        if not isinstance(reply, dict) or not isinstance(reply.get("files"), list):
            raise SyncError("The daemon did not report the code graph.")
        files = clean_files(reply["files"])
        removed = [p for p in (normalize(r) for r in reply.get("removed") or []) if p]
        replaced, gone = store.apply(company_id, root, files, removed)
        done["pages"] += 1
        done["replaced"] += replaced
        done["removed"] += gone
        if on_page is not None:
            on_page(bool(reply.get("more")))
        if not reply.get("more"):
            done["complete"] = True
            break
        if not files:
            break
    return done


# --- the read: the files around the seeds, as the daemon's walk gives them ------


def neighbours(
    store: Any, company_id: int, root: str, paths: list[str]
) -> tuple[Edges, Edges]:
    """What the files *paths* use and what uses them, with the names that tie
    them: the daemon's edge rule (``repo_map.reference_edges``) on the stored rows."""
    uses: Edges = {}
    used_by: Edges = {}
    if not paths:
        return uses, used_by
    refs = store.references_of(company_id, root, paths)
    defs = store.definitions_of(company_id, root, paths)
    names = sorted({n for _p, n in refs} | {n for _p, n in defs})
    definers: dict[str, set[str]] = {}
    for path, name in store.definers(company_id, root, names):
        definers.setdefault(name, set()).add(path)
    for source, name in refs:
        targets = definers.get(name, set())
        if len(targets) > MAX_DEFINERS:
            continue
        for target in targets - {source}:
            uses.setdefault(source, {}).setdefault(target, set()).add(name)
    wanted = set(paths)
    defined = sorted({n for _p, n in defs if len(definers.get(n, ())) <= MAX_DEFINERS})
    for source, name in store.referrers(company_id, root, defined):
        for target in (definers.get(name, set()) & wanted) - {source}:
            used_by.setdefault(target, {}).setdefault(source, set()).add(name)
    return uses, used_by


def match_seeds(
    seeds: list[str], paths: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """The stored *paths* the *seeds* name (a file, or every file under a
    folder), and the seeds that matched and did not."""
    chosen: list[str] = []
    matched: list[str] = []
    unmatched: list[str] = []
    known = set(paths)
    for seed in seeds:
        path = normalize(seed)
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


def _listed(edges: dict[str, set[str]]) -> tuple[list[dict[str, Any]], int]:
    rows = sorted(edges.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = [{"path": p, "names": sorted(n)[:MAX_NAMES]} for p, n in rows[:MAX_EDGES]]
    return shown, max(0, len(rows) - MAX_EDGES)


def related(
    store: Any, company_id: int, root: str, seeds: list[str], hops: int, max_files: int
) -> dict[str, Any] | None:
    """The stored graph around *seeds* in the shape of the daemon's
    ``workspace.related`` reply (plus ``stored`` and ``observed_at``), or None
    when nothing is stored for the folder or no seed names a stored file."""
    known = store.known(company_id, root)
    chosen, matched, unmatched = match_seeds(seeds, sorted(known))
    if not chosen:
        return None
    uses: Edges = {}
    used_by: Edges = {}
    done: set[str] = set()

    def expand(paths: list[str]) -> None:
        todo = [p for p in paths if p not in done]
        more_uses, more_used_by = neighbours(store, company_id, root, todo)
        uses.update(more_uses)
        used_by.update(more_used_by)
        done.update(todo)

    reached = {path: 0 for path in sorted(chosen)[:max_files]}
    cut = len(chosen) > max_files
    frontier = list(reached)
    for hop in range(1, max(1, min(hops, 2)) + 1):
        if cut:
            break
        expand(frontier)
        ties: dict[str, int] = {}
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
    expand(list(reached))
    files = []
    for path in sorted(reached, key=lambda p: (reached[p], p)):
        shown_uses, more_uses = _listed(uses.get(path, {}))
        shown_used_by, more_used_by = _listed(used_by.get(path, {}))
        files.append(
            {
                "path": path,
                "sha256": known.get(path),
                "hop": reached[path],
                "seed": reached[path] == 0,
                "uses": shown_uses,
                "used_by": shown_used_by,
                "more_uses": more_uses,
                "more_used_by": more_used_by,
            }
        )
    observed = store.observed_at(company_id, root)
    return {
        "files": files,
        "partial": cut,
        "seeds_matched": matched,
        "seeds_unmatched": unmatched,
        "stored": True,
        "observed_at": observed.isoformat(timespec="seconds") if observed else None,
    }


# --- the edges from records --------------------------------------------------------


def architecture_edges(
    arch: dict[str, Any] | None, architecture_id: Any
) -> list[dict[str, Any]]:
    """``implements`` (a module to each requirement it covers and to its
    path) and ``depends_on`` (a module to the modules it names), as the
    approved architecture states them: claims, with their source."""
    modules = [m for m in (arch or {}).get("modules") or [] if isinstance(m, dict)]
    ids: dict[str, str] = {}
    for module in modules:
        key = str(module.get("id") or module.get("name") or "").strip()
        for alias in (module.get("id"), module.get("name")):
            if isinstance(alias, str) and alias.strip() and key:
                ids.setdefault(alias.strip(), key)
    ref = f"architecture:{architecture_id}"
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(kind: str, frm: str, to: str) -> None:
        edges.setdefault(
            (kind, frm, to),
            {
                "kind": kind,
                "from_ref": frm,
                "to_ref": to,
                "source_ref": ref,
                "detail": None,
            },
        )

    for module in modules:
        key = str(module.get("id") or module.get("name") or "").strip()
        if not key:
            continue
        for req in module.get("covers") or []:
            if isinstance(req, str) and req.strip():
                add("implements", f"module:{key}", f"requirement:{req.strip()}")
        path = normalize(module.get("path"))
        if path:
            add("implements", f"module:{key}", f"file:{path}")
        for dep in module.get("depends_on") or []:
            if isinstance(dep, str) and dep.strip():
                target = ids.get(dep.strip(), dep.strip())
                if target != key:
                    add("depends_on", f"module:{key}", f"module:{target}")
    return list(edges.values())


def verified_edges(writes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``verified``: a file to each run whose latest verdict is ``verified``
    and whose daemon report wrote it, with the sha256 that report holds:
    observations, copied from the Verifier's records."""
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for write in writes:
        path = normalize(write.get("path"))
        if not path or write.get("run_id") is None:
            continue
        edges.setdefault(
            (path, str(write["run_id"])),
            {
                "kind": "verified",
                "from_ref": f"file:{path}",
                "to_ref": f"run:{write['run_id']}",
                "source_ref": f"verification:{write.get('verification_id')}",
                "detail": {
                    "sha256": write.get("sha256"),
                    "backlog_item_id": write.get("backlog_item_id"),
                },
            },
        )
    return list(edges.values())


def record_edges(
    store: Any,
    engine: Any,
    company_id: int,
    root: str,
    arch: dict[str, Any] | None,
    *,
    architecture_id: Any = None,
) -> None:
    """Replace this folder's edges from the architecture and from the
    verifications with what those records say now."""
    store.replace_edges(
        company_id, root, "architecture", architecture_edges(arch, architecture_id)
    )
    with Session(engine) as session:
        writes = touched.verified_writes(session, company_id)
    store.replace_edges(company_id, root, "verification", verified_edges(writes))
