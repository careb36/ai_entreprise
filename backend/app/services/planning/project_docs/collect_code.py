"""The snapshot's code graph: what the C4 code level reads (issue #693).

``code_graph`` reads, through the tools context's interface
(``services/tools/code_level``), the stored code graph of the company's
project folder (issue #438) for the components the architecture names. The
snapshot gets two keys:

- ``code_graph``: ``{status, claims, observed}``. ``claims`` are the
  architecture's ``implements`` edges (a module to its path), a claim a
  person approved; ``observed`` holds the stored files under those paths with
  the sha256 the workspace daemon reported, the names they define and the
  references among them, or None when nothing is stored (``status``
  ``not_stored``). It is an input of ``c4.md``'s digest, so a cited file
  whose sha256 changes makes the document stale.
- ``code_graph_observed_at``: when the daemon last reported a file of the
  folder. Shown, not hashed: a sync that changed no file the pack cites does
  not make it stale.

The store is read and nothing else: the daemon is never asked from the
pack. The project folder is not put in the snapshot (it may carry a
person's name).
"""

from __future__ import annotations

import os
from typing import Any

from models import ProjectDiscovery
from sqlmodel import Session, select

from services.tools import code_level


def project_folder(session: Session, company_id: int) -> str | None:
    """The folder the task worker stores the graph for: the company's newest
    discovered project, else ``AGENT_WORKSPACE_ROOT`` (as the worker resolves it)."""
    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == company_id)
        .order_by(ProjectDiscovery.created_at.desc(), ProjectDiscovery.id.desc())
    ).first()
    if discovery is not None and discovery.project_path:
        return discovery.project_path
    return os.getenv("AGENT_WORKSPACE_ROOT") or None


def code_graph(
    session: Session, company_id: int, architecture: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str | None]:
    """``(code_graph, observed_at)`` for the snapshot; ``(None, None)``
    without an architecture."""
    if not architecture:
        return None, None
    graph = code_level.stored(
        session.get_bind(),
        company_id,
        project_folder(session, company_id),
        architecture,
        architecture.get("id"),
    )
    observed_at = graph.pop("observed_at", None)
    return graph, observed_at


def entries(session: Session, company_id: int, architecture: Any) -> dict[str, Any]:
    """The snapshot's ``code_graph`` and ``code_graph_observed_at`` entries.

    ``code_graph_observed_at`` is shown, never an input of any digest.
    """
    graph, observed_at = code_graph(session, company_id, architecture)
    return {"code_graph": graph, "code_graph_observed_at": observed_at}
