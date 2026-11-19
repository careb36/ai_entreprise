"""The related code in the Developer's prompt (issue #438, first slice).

From a task's seeds (the paths of the approved-architecture modules its
requirements map to, and the files its earlier runs wrote;
``services/loop/worker/related.py``) the daemon's ``workspace.related``
(``vscode-daemon/code_graph.py``) walks the project's reference graph, the
repository map's, computed on demand from its parse cache: which files the
seeds use and which use them, with the names that tie them. The section names
the files a change could break, and the recent runs the daemon reports wrote
any of these files, with the verdict the Verifier recorded
(``services/evidence/touched.py``), refuted ones marked.

It is context, never evidence: the graph is a parse of the files as they are
now (a name used is not a call proven), and the runs are past observations
about other deliveries. The section is cut at ``CODE_GRAPH_MAX_CHARS`` (``0``
leaves it out). The wait is bounded (``TIMEOUT_SECONDS``); no seeds, a daemon
without the subject (an older one), a timeout, an error or an empty reply
leave the section out (``""``), and the prompt is what it was before.

Second slice: when the company's graph is stored (``code_graph_store``,
``CODE_GRAPH_STORE``), the section reads it instead of asking the daemon to
walk, and says it is the graph as the daemon last reported it; with nothing
stored, or no seed in it, it asks the daemon on demand as above. ``persist``
brings the stored graph up to date from the daemon's ``workspace.graph``
(only the files whose sha256 changed) and records the edges from the
architecture and the verifications. The task worker starts it with
``persist_soon``, in the background and for the company's project folder
only (``services/loop/worker/related.py``): on a project the size of this
repository the first sync parses and stores every file, and a task never
waits for that; a task's worktree, read by nobody once merged, is never
stored. At most one sync per company and folder runs in this process, and
while one is part way through (pages stored, more to come) the folder's
half-written graph is not read: the section asks the daemon on demand. A daemon without the subject, or any failure,
leaves the store as it was and the on-demand path in place.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from config import settings
from sqlmodel import Session

from services.contracts.verdicts import VERDICT_REFUTED
from services.evidence import touched
from services.evidence.context_read import redact_secrets
from services.tools import code_graph_store

logger = logging.getLogger("fabrique-task-worker")

SUBJECT = "workspace.related"
TIMEOUT_SECONDS = 10.0
MAX_SEEDS = 50
HOPS = 1
MAX_FILES = 30
RUNS_SHOWN = 5
NAMES_SHOWN = 8

Requester = Callable[[str, dict[str, Any], float], dict[str, Any]]

_NOTE = (
    "Context from the workspace daemon's parse of the project as it is on disk "
    "now, not evidence: the files this task starts from, the files they use and "
    "the files that use them, each with the names that tie them. A change to a "
    "starting file can break the files that use it; read them before you change "
    "a name they use."
)
_STORED_NOTE = (
    "Context from the stored graph of the workspace daemon's parse, as the "
    "daemon last reported it{when}, not evidence: the files this task starts "
    "from, the files they use and the files that use them, each with the names "
    "that tie them. A change to a starting file can break the files that use "
    "it; read them before you change a name they use."
)
_RUNS_NOTE = (
    "What the daemon reported earlier deliveries wrote, with the verdict the "
    "Verifier recorded for each run."
)
_CUT = "\n[related code cut here; search for the rest]"


def clean_seeds(seeds: list[Any]) -> list[str]:
    """The seeds as unique, non-empty strings, in order, at most ``MAX_SEEDS``."""
    out: list[str] = []
    for seed in seeds or []:
        text = seed.strip() if isinstance(seed, str) else ""
        if text and text not in out:
            out.append(text)
    return out[:MAX_SEEDS]


def _merge(
    rows: list[dict[str, Any]], key: str, skip: set[str]
) -> dict[str, list[str]]:
    """The edges *key* of *rows*, merged by path, less the files in *skip*."""
    merged: dict[str, list[str]] = {}
    for row in rows:
        for edge in row.get(key) or []:
            if not isinstance(edge, dict) or not isinstance(edge.get("path"), str):
                continue
            path = edge["path"]
            if path in skip:
                continue
            names = merged.setdefault(path, [])
            for name in edge.get("names") or []:
                if isinstance(name, str) and name not in names:
                    names.append(name)
    return merged


def _lines(merged: dict[str, list[str]]) -> list[str]:
    rows = sorted(merged.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [
        f"- {path}: {', '.join(names[:NAMES_SHOWN])}" if names else f"- {path}"
        for path, names in rows
    ]


def _run_line(run: dict[str, Any]) -> str:
    verdict = str(run.get("verdict") or "no verdict")
    mark = " — REFUTED: its delivery did not hold" if verdict == VERDICT_REFUTED else ""
    title = str(run.get("title") or f"task {run.get('backlog_item_id')}")
    return f'- "{title}" ({verdict}{mark}): {", ".join(run.get("written") or [])}'


def _note(reply: dict[str, Any]) -> str:
    if not reply.get("stored"):
        return _NOTE
    when = reply.get("observed_at")
    return _STORED_NOTE.format(when=f" ({when})" if isinstance(when, str) else "")


def render(reply: dict[str, Any], runs: list[dict[str, Any]], max_chars: int) -> str:
    """The section from the daemon's *reply* and the *runs*; ``""`` without files."""
    files = [f for f in reply.get("files") or [] if isinstance(f, dict)]
    seeds = [f for f in files if f.get("seed") and isinstance(f.get("path"), str)]
    if not seeds or max_chars <= 0:
        return ""
    seed_paths = {f["path"] for f in seeds}
    parts = [
        "\n\n## Related code\n" + _note(reply),
        "### Starting files\n" + "\n".join(f"- {p}" for p in sorted(seed_paths)),
    ]
    uses = _lines(_merge(seeds, "uses", seed_paths))
    if uses:
        parts.append("### What they use\n" + "\n".join(uses))
    used_by = _lines(_merge(seeds, "used_by", seed_paths))
    if used_by:
        parts.append(
            "### What uses them (a change could break these)\n" + "\n".join(used_by)
        )
    if runs:
        parts.append(
            "### Recent runs that touched these files\n"
            + _RUNS_NOTE
            + "\n"
            + "\n".join(_run_line(r) for r in runs)
        )
    if reply.get("partial"):
        parts.append(
            "[the project is larger than the daemon walked; some files are not in it]"
        )
    text = redact_secrets("related code", "\n\n".join(parts))
    if len(text) > max_chars:
        text = text[: max(0, max_chars - len(_CUT))].rsplit("\n", 1)[0] + _CUT
    return text


def related_section(
    request_fn: Requester,
    root: str | None,
    seeds: list[Any],
    *,
    company_id: int | None,
    engine: Any,
) -> str:
    """The ``## Related code`` section for *seeds* in *root*, or ``""``."""
    max_chars = settings.CODE_GRAPH_MAX_CHARS
    seeds = clean_seeds(seeds)
    if max_chars <= 0 or not root or not seeds:
        return ""
    partial = half_written(company_id, root)
    reply = None if partial else _stored(root, seeds, company_id, engine)
    if reply is not None:
        return render(reply, _runs(reply, company_id, engine), max_chars)
    try:
        reply = request_fn(
            SUBJECT,
            {
                "workspace_root": root,
                "seeds": seeds,
                "hops": HOPS,
                "max_files": MAX_FILES,
            },
            TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - The prompt goes without the section.
        logger.info("No related code for %s: %s", root, exc)
        return ""
    if not isinstance(reply, dict) or not isinstance(reply.get("files"), list):
        return ""
    return render(reply, _runs(reply, company_id, engine), max_chars)


def _stored(
    root: str, seeds: list[str], company_id: int | None, engine: Any
) -> dict[str, Any] | None:
    """The stored graph around *seeds*, or None (nothing stored, no seed in
    it, the store off or failing): then the daemon is asked on demand."""
    if company_id is None or engine is None:
        return None
    try:
        store = code_graph_store.store_for(engine)
        if store is None:
            return None
        return code_graph_store.related(store, company_id, root, seeds, HOPS, MAX_FILES)
    except Exception as exc:  # noqa: BLE001 - The on-demand path.
        logger.info("Could not read the stored code graph of %s: %s", root, exc)
        return None


def _runs(
    reply: dict[str, Any], company_id: int | None, engine: Any
) -> list[dict[str, Any]]:
    paths = [
        f["path"]
        for f in reply["files"]
        if isinstance(f, dict) and isinstance(f.get("path"), str)
    ]
    runs: list[dict[str, Any]] = []
    if paths and engine is not None:
        try:
            with Session(engine) as session:
                runs = touched.runs_touching(session, company_id, paths, RUNS_SHOWN)
        except Exception as exc:  # noqa: BLE001 - The graph alone.
            logger.info("Could not read the runs that touched the files: %s", exc)
    return runs


def persist(
    request_fn: Requester,
    engine: Any,
    company_id: int | None,
    root: str | None,
    *,
    architecture: dict[str, Any] | None = None,
    architecture_id: Any = None,
    on_page: Callable[[bool], None] | None = None,
) -> dict[str, Any] | None:
    """Bring the stored graph of *root* up to date from the daemon (only the
    files whose sha256 changed) and record the edges from the architecture
    and the verifications. None when nothing is stored (``off``, no root, a
    daemon without ``workspace.graph``, a failure): the store stays as it was."""
    if company_id is None or not root or engine is None:
        return None
    try:
        store = code_graph_store.store_for(engine)
        if store is None:
            return None
        done = code_graph_store.sync(store, request_fn, company_id, root, on_page)
    except Exception as exc:  # noqa: BLE001 - The on-demand path stays.
        logger.info("The code graph of %s was not stored: %s", root, exc)
        return None
    try:
        code_graph_store.record_edges(
            store,
            engine,
            company_id,
            root,
            architecture,
            architecture_id=architecture_id,
        )
    except Exception as exc:  # noqa: BLE001 - The file graph alone.
        logger.info("Could not record the code graph's edges for %s: %s", root, exc)
    return done


# The background syncs of this process, one per (company, folder); the value
# says whether the sync has stored a page after which the daemon said more.
_SYNCS: dict[tuple[int, str], dict[str, Any]] = {}
_SYNCS_LOCK = threading.Lock()


def half_written(company_id: int | None, root: str | None) -> bool:
    """Whether a running sync is part way through *root*'s graph: some pages
    stored, more to come. The section does not read the store meanwhile."""
    with _SYNCS_LOCK:
        state = _SYNCS.get((company_id, root))
        return bool(state and state["partial"])


def persist_soon(
    request_fn: Requester,
    engine: Any,
    company_id: int | None,
    root: str | None,
    *,
    architecture: dict[str, Any] | None = None,
    architecture_id: Any = None,
) -> threading.Thread | None:
    """``persist`` in a background thread, so the task goes on at once; None
    when nothing is started (no root, the store ``off``, or a sync of the
    same folder already running in this process)."""
    if company_id is None or not root or engine is None:
        return None
    if (settings.CODE_GRAPH_STORE or "").strip().lower() == "off":
        return None
    key = (company_id, root)
    state: dict[str, Any] = {"partial": False}

    def page(more: bool) -> None:
        with _SYNCS_LOCK:
            state["partial"] = more

    def run() -> None:
        try:
            persist(
                request_fn,
                engine,
                company_id,
                root,
                architecture=architecture,
                architecture_id=architecture_id,
                on_page=page,
            )
        finally:
            with _SYNCS_LOCK:
                _SYNCS.pop(key, None)

    thread = threading.Thread(target=run, name="code-graph-sync", daemon=True)
    state["thread"] = thread
    with _SYNCS_LOCK:
        if key in _SYNCS:
            return None
        _SYNCS[key] = state
    try:
        thread.start()
    except RuntimeError as exc:  # No thread to be had: the on-demand path.
        with _SYNCS_LOCK:
            _SYNCS.pop(key, None)
        logger.info("The code graph of %s was not synced: %s", root, exc)
        return None
    return thread


def wait_for_persists(timeout: float | None = None) -> bool:
    """Wait for the background syncs running now; False if one outlasts *timeout*."""
    with _SYNCS_LOCK:
        threads = [state["thread"] for state in _SYNCS.values()]
    for thread in threads:
        thread.join(timeout)
    return not any(thread.is_alive() for thread in threads)
