"""The tools of a task's execution turn (ADR 0014, the task worker's execute stage).

Moved out of ``execute`` (which stays under the file size limit). The turn's
step is the task's (``contracts/tool_steps.execution_step``: a task whose
kind is documentation is the documentation step, which writes documentation
only; every other task is the execution step), and the toolbox is the one
``developer_tools.toolbox_for`` makes for that step and the role, within the
model's limits, with the task's record tools on a rework (``board_record``).
What was offered, the step and its families, and where the attempt's tools
ran go on the run's evidence (``tools_offered``): the backend's observation.
"""

from typing import Any

from services.contracts import roles, tool_steps
from services.loop.worker import board_record
from services.tools import developer_tools, workspace_edits


def task_toolbox(
    company_id: int | None,
    role: str,
    item: dict[str, Any],
    agent: dict[str, Any],
    limits: dict[str, Any],
    *,
    rework: bool,
    prepared: Any,
    engine: Any,
) -> Any:
    """The execution turn's toolbox; sets ``agent["tools_offered"]``."""
    step = tool_steps.execution_step(item.get("kind"))
    toolbox = developer_tools.toolbox_for(
        company_id, role, engine=engine, item_id=item.get("id"), step=step
    )
    developer_tools.apply_limits(
        toolbox,
        rounds=limits["tool_rounds"],
        result_max_chars=limits["result_max_chars"],
    )
    if rework:  # A rework reads its own record (``board_record``).
        toolbox = board_record.on_rework(toolbox, company_id, item, limits, engine)
    # The tools offered (and withheld, with why) go on the evidence.
    agent["tools_offered"] = workspace_edits.offered(toolbox)
    if agent["tools_offered"]:  # The step and the families it allows (tool_steps).
        agent["tools_offered"].update(tool_steps.record(step, roles.canonical(role)))
    if prepared:  # The daemon's reply: where this attempt's tools ran, or why not.
        agent["tools_offered"]["task_worktree"] = prepared
    return toolbox
