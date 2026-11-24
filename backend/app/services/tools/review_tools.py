"""QA's read-only workspace tools: look at a delivery before the verdict.

The review turn (``services/loop/worker/qa_review_reads.py``) sees the branch
diff and the agent's message in excerpts; with these tools it can read the
files themselves before it decides. The toolbox is the Developer's
(``developer_tools.WorkspaceToolbox``) with nothing that writes or runs:
``workspace__list``, ``workspace__read``, ``workspace__search`` and
``workspace__find_file``. No edit (``can_edit`` stays false), no move or
delete, and no ``run_tests`` (no test command is given, so the schema is not
offered and a call to it is refused as an unknown tool).

The tools look where the delivery went: the ``workspace_root`` recorded on the
task's latest verification (the task worktree under the Git workflow, else the
project the files were written to), as the backend observed it. Without a
recorded delivery, a daemon bridge or a positive round budget there is no
toolbox, and the review is today's single call.

The same read-only toolbox serves every step whose ceiling is reading
(``contracts/tool_steps``): ``for_step`` gives it to the PO's sprint planning
and the ScrumMaster's decision on a blocked task, on the task's root
(``developer_tools.tool_root``), and refuses a step that does not allow
reading. No step reached through here offers W or X, whatever the role.
"""

from __future__ import annotations

import logging
from typing import Any

from services.contracts import roles, tool_steps
from services.tools import developer_tools

logger = logging.getLogger("fabrique-task-worker")


def delivery_root(company_id: int, item_id: int, *, engine: Any) -> str | None:
    """The folder the task's latest delivery was written to, as recorded on
    its verification's evidence; ``None`` when there is none."""
    try:
        from services.evidence import delivery_gate

        verification = delivery_gate.latest_verification(engine, company_id, item_id)
    except Exception as exc:  # noqa: BLE001 - No recorded delivery, no tools.
        logger.warning("Could not read the task's last delivery: %s", exc)
        return None
    evidence = (verification.evidence if verification is not None else None) or {}
    root = evidence.get("workspace_root")
    return root if isinstance(root, str) and root else None


def read_only_toolbox(
    company_id: int | None,
    item_id: int | None,
    *,
    engine: Any,
    rounds: int,
    role: str | None = None,
) -> developer_tools.WorkspaceToolbox | None:
    """The read-only workspace tools on the delivery's root, for at most
    *rounds* rounds; ``None`` when they cannot be offered (or the review step
    keeps no reading for *role*: ``contracts/tool_steps``)."""
    if company_id is None or item_id is None or rounds <= 0:
        return None
    if not tool_steps.allows(
        tool_steps.QA_REVIEW, roles.canonical(role), tool_steps.READ
    ):
        return None
    if "nats" not in developer_tools._bridge:
        return None
    root = delivery_root(company_id, item_id, engine=engine)
    if not root:
        return None
    box = developer_tools.WorkspaceToolbox(
        developer_tools._request_from_thread, root, can_edit=False
    )
    box.max_rounds = rounds
    return box


def for_step(
    step: str,
    role: str | None,
    company_id: int | None,
    item_id: int | None = None,
    *,
    engine: Any = None,
    rounds: int,
) -> developer_tools.WorkspaceToolbox | None:
    """The read-only workspace tools for a turn of *step* by *role* on the
    task's root (the project without a task), for at most *rounds* rounds;
    ``None`` when the step and role keep no reading, the rounds are 0, or
    there is no daemon or root. Never edits or runs, whatever the step allows."""
    if company_id is None or rounds <= 0:
        return None
    if not tool_steps.allows(step, roles.canonical(role), tool_steps.READ):
        return None
    try:
        box = developer_tools.read_tools(company_id, item_id, engine)
    except Exception as exc:  # noqa: BLE001 - The turn goes on without tools.
        logger.warning("No read tools for %s on item=%s: %s", step, item_id, exc)
        return None
    if box is not None:
        box.max_rounds = rounds
    return box
