"""The Architect's brief through the tool loop, with the project's read tools.

``execute._consult_architect`` asks the company's Architect for a brief. In
sprint 7 (2026-11-24), asked in one plain call with only the repository map,
the Architect wanted to look at the project's workflows first and could only
type a tool call as text. The brief now runs through the Developer's loop
(``mcp_tools.chat_with_tools``) with the workspace's read tools alone
(``review_tools.for_step``: list, read, search and find_file; no edit,
move, delete or test run) on the task's root, for at most
``ARCHITECT_TOOL_ROUNDS`` rounds, each answer capped at ``BRIEF_MAX_TOKENS``.

Each call is the backend's observation (the file's sha256 for a read),
recorded on the Architect's turns; the final text stays a claim, and a tool
call typed as text is still no brief. Without the daemon, a project path or
rounds (``ARCHITECT_TOOL_ROUNDS=0``) it is one plain call, as before.
"""

from collections.abc import Callable
from typing import Any

from config import settings
from logging_config import setup_logger

from services.contracts import tool_steps
from services.models import model_routing
from services.platform.orchestration_metrics import KPI_ROLE_ARCHITECT
from services.tools import mcp_tools, review_tools

logger = setup_logger("fabrique-task-worker")

READ_NOTE = (
    "\n\n## Reading the project\n"
    "You may read the project before you answer, with the workspace tools "
    "(list, read, search, find_file). You cannot edit, move, delete or run "
    "anything. Once you have read enough, answer with the brief itself, "
    "never with a tool call written as text."
)


def architect_agent(row: Any, company_model: str | None) -> dict[str, Any]:
    """The company's Architect agent *row* as the brief's agent: its name,
    seniority, specialty and prompt, on its own model or the company's."""
    return {
        "role": row.role or KPI_ROLE_ARCHITECT,
        "name": row.name or KPI_ROLE_ARCHITECT,
        "model": model_routing.resolve_agent_model(
            {"model": row.model or company_model, "role": KPI_ROLE_ARCHITECT}
        ),
        "seniority": row.seniority,
        "specialty": row.specialty,
        "system_prompt": row.system_prompt,
    }


def system_prompt(architect: dict[str, Any]) -> str:
    """The Architect's own system prompt, or one from its seniority and specialty."""
    return architect["system_prompt"] or (
        f"You are a {architect.get('seniority') or 'Senior'} Software Architect "
        f"specializing in {architect.get('specialty') or 'system design'}. "
        "You advise developers with concise, decisive technical briefs."
    )


def read_tools(company_id: int | None, item_id: int | None, engine: Any) -> Any:
    """The Architect's read tools for the task, at most ``ARCHITECT_TOOL_ROUNDS``
    rounds; ``None`` when the rounds are 0 or there is nothing to read. The
    brief's step never writes nor runs (``contracts/tool_steps``)."""
    return review_tools.for_step(
        tool_steps.ARCHITECT_BRIEF,
        KPI_ROLE_ARCHITECT,
        company_id,
        item_id,
        engine=engine,
        rounds=settings.ARCHITECT_TOOL_ROUNDS,
    )


def ask(
    client: Any,
    system_prompt: str,
    request: str,
    architect: dict[str, Any],
    toolbox: Any,
    record: Callable[..., None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The Architect's final answer and the observations of its tool calls.

    Without a toolbox this is the one plain call it always was; with one, the
    request says what the tools may do, the loop answers each call and
    *record* gets each round that made calls, as the Developer's turns."""
    request += READ_NOTE if toolbox is not None else ""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request},
    ]

    def on_round(result: dict[str, Any], calls: list[dict[str, Any]]) -> None:
        if record is not None:
            record(result, "tool calls: " + mcp_tools.summarize(calls))

    try:
        return mcp_tools.chat_with_tools(
            client,
            messages,
            architect["model"],
            architect["role"],
            toolbox,
            on_round=on_round,
        )
    finally:
        if toolbox is not None:
            toolbox.close()
