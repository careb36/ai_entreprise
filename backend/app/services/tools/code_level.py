"""The stored code graph read per architecture component (issue #693).

The documentation pack's C4 code level (``services/planning/project_docs``)
asks, for each module the architecture names, which files the stored code
graph (``code_graph_store``, issue #438) has under the module's path, the
names each defines, and the references between those files. Two kinds of
statement come back, in separate fields:

- ``claims``: each module's ``implements`` edge to its path, as
  ``code_graph_store.architecture_edges`` derives it from the architecture
  the pack documents (the rule the store records the edges with). What the
  architecture says a component is made of: a claim a person approved.
- ``observed``: the stored files under each claimed path, with the sha256 the
  workspace daemon computed from the bytes on the machine that holds them,
  the names they define, and the ``references`` edges among them resolved
  with the daemon's rule (``code_graph_store.neighbours``: a name a file uses
  and another defines; not a call proven). None when nothing is stored for
  the folder, the store is ``off`` or fails, or no claimed path holds a
  stored file.

Only the store is read: the daemon is never asked from here, so a pack shows
the graph as the daemon last reported it (``observed_at``). The project
folder is a key of the store and is not returned (it may carry a person's
name). Bounded: ``MAX_FILES_PER_MODULE`` files a component, ``MAX_DEFINES``
names a file, ``MAX_REFERENCES`` edges with ``MAX_NAMES`` names each, with
counts of what was left out.
"""

from __future__ import annotations

import logging
from typing import Any

from services.tools import code_graph_store

logger = logging.getLogger("fabrique-project-docs")

MAX_FILES_PER_MODULE = 25
MAX_DEFINES = 12
MAX_REFERENCES = 100
MAX_NAMES = 8


def claims(architecture: dict[str, Any] | None, architecture_id: Any) -> list[dict]:
    """Each module's ``implements`` edge to its path: ``{module, path, source_ref}``."""
    out = []
    for edge in code_graph_store.architecture_edges(architecture, architecture_id):
        if edge["kind"] != "implements" or not edge["to_ref"].startswith("file:"):
            continue
        out.append(
            {
                "module": edge["from_ref"].removeprefix("module:"),
                "path": edge["to_ref"].removeprefix("file:"),
                "source_ref": edge["source_ref"],
            }
        )
    return out


def _observed(
    store: Any, company_id: int, root: str, claimed: list[dict]
) -> tuple[dict[str, Any] | None, str | None]:
    known = store.known(company_id, root)
    if not known:
        return None, None
    stored = sorted(known)
    modules: dict[str, list[str]] = {}
    more_files = 0
    for claim in claimed:
        hits, _matched, _unmatched = code_graph_store.match_seeds(
            [claim["path"]], stored
        )
        more_files += max(0, len(hits) - MAX_FILES_PER_MODULE)
        for path in sorted(hits)[:MAX_FILES_PER_MODULE]:
            owners = modules.setdefault(path, [])
            if claim["module"] not in owners:
                owners.append(claim["module"])
    if not modules:
        return None, None
    paths = sorted(modules)
    defines: dict[str, set[str]] = {}
    for path, name in store.definitions_of(company_id, root, paths):
        defines.setdefault(path, set()).add(name)
    uses, _used_by = code_graph_store.neighbours(store, company_id, root, paths)
    edges = [
        (source, target, names)
        for source, targets in uses.items()
        if source in modules
        for target, names in targets.items()
        if target in modules
    ]
    edges.sort(key=lambda e: (-len(e[2]), e[0], e[1]))
    files = []
    for path in paths:
        names = sorted(defines.get(path, set()))
        files.append(
            {
                "path": path,
                "sha256": known[path],
                "modules": modules[path],
                "defines": names[:MAX_DEFINES],
                "more_defines": max(0, len(names) - MAX_DEFINES),
            }
        )
    references = [
        {
            "from": source,
            "from_sha256": known[source],
            "to": target,
            "to_sha256": known[target],
            "names": sorted(names)[:MAX_NAMES],
            "more_names": max(0, len(names) - MAX_NAMES),
        }
        for source, target, names in edges[:MAX_REFERENCES]
    ]
    observed = store.observed_at(company_id, root)
    return (
        {
            "files": files,
            "references": references,
            "more_files": more_files,
            "more_references": max(0, len(edges) - MAX_REFERENCES),
        },
        observed.isoformat(timespec="seconds") if observed else None,
    )


def stored(
    engine: Any,
    company_id: int,
    root: str | None,
    architecture: dict[str, Any] | None,
    architecture_id: Any,
) -> dict[str, Any]:
    """``{status, claims, observed, observed_at}`` for the components of
    *architecture*; ``status`` is ``stored`` when something was observed and
    ``not_stored`` otherwise (``observed`` and ``observed_at`` then None)."""
    claimed = claims(architecture, architecture_id)
    out: dict[str, Any] = {
        "status": "not_stored",
        "claims": claimed,
        "observed": None,
        "observed_at": None,
    }
    if not root or engine is None or not claimed:
        return out
    try:
        store = code_graph_store.store_for(engine)
        if store is None:
            return out
        observed, observed_at = _observed(store, company_id, root, claimed)
    except Exception as exc:  # noqa: BLE001 - The pack says it was not observed.
        logger.info("Could not read the stored code graph for the pack: %s", exc)
        return out
    if observed is not None:
        out.update(status="stored", observed=observed, observed_at=observed_at)
    return out
