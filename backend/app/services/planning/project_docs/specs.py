"""Every approved specification of a pack (issue #329, finding F3).

A company with one system and many specifications (La Fabrique: #469's and
#535's) is documented for all of them, not only the newest. The snapshot's
``specifications`` lists the approved ones, oldest first, each with the
``lineage_ids`` of its versions; ``specification`` stays the newest. With one
approved specification (or none) a document reads exactly as before and its
inputs do not name ``specifications``, so a pack compiled before keeps its
digests; with several, the documents that list requirements (scope
statement, WBS, traceability matrix, schedule, index) take a section per
specification, and a requirement's tasks are those of its own
specification's sprints, since ``REQ-1`` of one is not ``REQ-1`` of another.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import texts
from services.planning.project_docs.refs import Sources
from services.planning.project_docs.sanitize import md_id, md_inline

KEY = "specifications"


def approved(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The approved specifications, oldest first."""
    return [s for s in snapshot.get(KEY) or [] if isinstance(s, dict)]


def several(snapshot: dict[str, Any]) -> bool:
    return len(approved(snapshot)) > 1


def documented(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The specifications a document lists: every approved one when there are
    several, else the snapshot's one specification (approved or not)."""
    if several(snapshot):
        return approved(snapshot)
    spec = snapshot.get("specification")
    return [spec] if spec else []


def inputs(snapshot: dict[str, Any], base: tuple[str, ...]) -> tuple[str, ...]:
    """*base*, with ``specifications`` when the document reads several."""
    return (*base, KEY) if several(snapshot) else base


def tasks_of(
    snapshot: dict[str, Any], spec: dict[str, Any] | None = None
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every (sprint, task) pair; with several specifications and *spec*, only
    those of sprints planned from one of *spec*'s versions."""
    lineage = set((spec or {}).get("lineage_ids") or [])
    filtered = bool(lineage) and several(snapshot)
    return [
        (sprint, task)
        for sprint in snapshot.get("sprints") or []
        if not filtered or sprint.get("specification_id") in lineage
        for task in sprint.get("tasks") or []
    ]


def heading(spec: dict[str, Any], mark: str, lang: str, level: int = 2) -> str:
    tr = texts.translator(lang)
    return f"{'#' * level} " + tr(
        "specs.heading",
        id=md_id(spec.get("id")),
        version=md_id(spec.get("version")),
        title=md_inline(spec.get("title")),
        mark=mark,
    )


def intro(snapshot: dict[str, Any], src: Sources) -> list[str]:
    tr = texts.translator(src.lang)
    return [tr("specs.several", count=len(approved(snapshot))), ""]


def deeper(lines: list[str]) -> list[str]:
    """*lines* with each Markdown heading one level deeper, for a section per
    specification."""
    return [f"#{line}" if line.startswith("#") else line for line in lines]
