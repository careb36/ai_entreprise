"""The workspace tools beyond list, read, search and find_file (issue #766).

Family R, read (``contracts/tool_steps``), is more than the four base reads
of ``developer_tools.WorkspaceToolbox``: read-only Git, ``blame`` included
(``workspace_git``), a document's text (``workspace_documents``) and code
navigation (``workspace_navigation``) and what the project declares
(``workspace_facts``: its API surface, configuration variables and
dependencies), each offered only when every daemon heard announces it.
``workspace__lint`` (``workspace_lint``) and ``workspace__docs_build``
(``workspace_docs_build``) are family X and are offered apart
(``offer_runs``, called by ``developer_tools.toolbox_for`` only where the
step allows runs); this module lists their schemas and routes their calls
when they were offered.

``offer`` is called on every toolbox that reads, whatever the step: the
execution turn's (``toolbox_for``), the read-only toolbox of the Architect's
brief, the PO's planning and the ScrumMaster's triage
(``developer_tools.read_tools``), and the QA review's
(``review_tools.read_only_toolbox``). None of them writes: these tools never
change the project, and the daemon checks every path again.
"""

from __future__ import annotations

import logging
from typing import Any

from services.tools import (
    workspace_docs_build,
    workspace_documents,
    workspace_facts,
    workspace_git,
    workspace_lint,
    workspace_navigation,
)

logger = logging.getLogger("fabrique-task-worker")

TOOLS = frozenset(
    {
        workspace_git.TOOL,
        workspace_documents.TOOL,
        *workspace_navigation.TOOLS,
        *workspace_facts.TOOLS,
        workspace_docs_build.TOOL,
    }
)


def offer(box: Any, company_id: int | None = None, engine: Any = None) -> None:
    """Offer on *box* what the daemons announce of read-only Git, a
    document's text, navigation and what the project declares (API surface,
    configuration variables, dependencies)."""
    if box is None:
        return
    workspace_navigation.offer(box)
    workspace_git.offer(box, company_id, engine)
    workspace_documents.offer(box)
    workspace_facts.offer(box)


def offer_runs(box: Any, loader: Any = None) -> None:
    """Family X beside the reads, where the step allows runs: the lint
    (``workspace_lint``) and the docs build (``workspace_docs_build``), both
    from the stack profile *loader* reads once."""
    if box is None:
        return
    workspace_lint.offer(box, loader)
    profile = workspace_lint.profile_of(box)
    if profile is None and loader is not None:
        try:
            profile = loader()
        except Exception as exc:  # noqa: BLE001 - No profile, no docs build offered.
            logger.warning(
                "Could not read the stack profile for the docs build: %s", exc
            )
    workspace_docs_build.offer(box, profile if isinstance(profile, dict) else None)


def schemas(box: Any) -> list[dict[str, Any]]:
    """The schemas offered on *box*: navigation (and the lint where offered),
    read-only Git, a document's text, what the project declares, and the
    docs build where offered."""
    return (
        workspace_navigation.schemas(box)
        + workspace_git.schemas(box)
        + workspace_documents.schemas(box)
        + workspace_facts.schemas(box)
        + workspace_docs_build.schemas(box)
    )


def run(box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call of a tool in ``TOOLS``; *extra* gets the observation."""
    if tool == workspace_git.TOOL:
        return workspace_git.run(box, arguments, extra)
    if tool == workspace_documents.TOOL:
        return workspace_documents.run(box, arguments, extra)
    if tool in workspace_facts.TOOLS:
        return workspace_facts.run(box, tool, arguments, extra)
    if tool == workspace_docs_build.TOOL:
        return workspace_docs_build.run(box, arguments, extra)
    return workspace_navigation.run(box, tool, arguments, extra)
