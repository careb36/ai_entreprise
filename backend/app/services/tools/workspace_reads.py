"""The workspace tools beyond list, read, search and find_file (issue #766).

Family R, read (``contracts/tool_steps``), is more than the four base reads
of ``developer_tools.WorkspaceToolbox``: read-only Git, ``blame`` included
(``workspace_git``), a document's text (``workspace_documents``) and code
navigation (``workspace_navigation``), each offered only when every daemon
heard announces it. ``workspace__lint`` (``workspace_lint``) is family X and
is offered apart, by ``developer_tools.toolbox_for``, only where the step
allows runs; this module only lists its schema and routes its call when it
was offered.

``offer`` is called on every toolbox that reads, whatever the step: the
execution turn's (``toolbox_for``), the read-only toolbox of the Architect's
brief, the PO's planning and the ScrumMaster's triage
(``developer_tools.read_tools``), and the QA review's
(``review_tools.read_only_toolbox``). None of them writes: these tools never
change the project, and the daemon checks every path again.
"""

from __future__ import annotations

from typing import Any

from services.tools import workspace_documents, workspace_git, workspace_navigation

TOOLS = frozenset(
    {workspace_git.TOOL, workspace_documents.TOOL, *workspace_navigation.TOOLS}
)


def offer(box: Any, company_id: int | None = None, engine: Any = None) -> None:
    """Offer on *box* what the daemons announce of read-only Git and a
    document's text (navigation is offered when the box is made)."""
    if box is None:
        return
    workspace_navigation.offer(box)
    workspace_git.offer(box, company_id, engine)
    workspace_documents.offer(box)


def schemas(box: Any) -> list[dict[str, Any]]:
    """The schemas offered on *box*: navigation (and the lint where offered),
    read-only Git, a document's text."""
    return (
        workspace_navigation.schemas(box)
        + workspace_git.schemas(box)
        + workspace_documents.schemas(box)
    )


def run(box: Any, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call of a tool in ``TOOLS``; *extra* gets the observation."""
    if tool == workspace_git.TOOL:
        return workspace_git.run(box, arguments, extra)
    if tool == workspace_documents.TOOL:
        return workspace_documents.run(box, arguments, extra)
    return workspace_navigation.run(box, tool, arguments, extra)
