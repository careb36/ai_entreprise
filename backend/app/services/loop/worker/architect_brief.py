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

The record tools (``board_record``: ``board__task``, ``board__evidence``) go
beside the reads, scoped to the task and the tasks it depends on, so a brief
on a rework reads what was asked and what the Verifier observed. The brief's
step also allows research (``tool_steps.ARCHITECT_BRIEF``: R and S), so the
research tools (``research__package``, ``research__advisories`` and a fetch
of the allowed hosts: ``research_tools.company_toolbox``) go beside the reads
too, within the same rounds (``board_tools.attach``); like the record tools
they never turn the plain call into a tool loop.
"""

from collections.abc import Callable
from typing import Any

from config import settings
from logging_config import setup_logger

from services.contracts import tool_calls, tool_steps
from services.loop.worker import board_record
from services.models import model_routing
from services.platform.orchestration_metrics import KPI_ROLE_ARCHITECT
from services.tools import board_tools, mcp_tools, research_tools, review_tools

logger = setup_logger("fabrique-task-worker")

READ_NOTE = (
    "\n\n## Reading the project\n"
    "You may read the project before you answer, with the workspace tools "
    "(list, read, search, find_file). You cannot edit, move, delete or run "
    "anything. Once you have read enough, answer with the brief itself, "
    "never with a tool call written as text."
)
RESEARCH_NOTE = (
    "You may also look up packages and their known advisories, and fetch "
    "pages of the allowed hosts, with the research tools."
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
    rounds = settings.ARCHITECT_TOOL_ROUNDS
    box = review_tools.for_step(
        tool_steps.ARCHITECT_BRIEF,
        KPI_ROLE_ARCHITECT,
        company_id,
        item_id,
        engine=engine,
        rounds=rounds,
    )
    # Research where the step allows it (family S), beside the reads only.
    if box is not None and tool_steps.allows(
        tool_steps.ARCHITECT_BRIEF, KPI_ROLE_ARCHITECT, tool_steps.RESEARCH
    ):
        research = research_tools.company_toolbox(company_id, engine=engine)
        box = board_tools.attach(box, research)
    # The task's record and its runs' evidence (``board_record``), own task.
    return board_record.offer(box, company_id, item_id, rounds=rounds, engine=engine)


def note_for(toolbox: Any) -> str:
    """What the request says the tools may do: the workspace reads, and the
    record tools beside them when offered; nothing without a toolbox."""
    if toolbox is None:
        return ""
    note = READ_NOTE
    if tool_calls.RESEARCH_SERVER in _servers(toolbox):
        note += " " + RESEARCH_NOTE
    if board_tools.has_board(toolbox):
        note += " " + board_tools.NOTE
    return note


def _servers(toolbox: Any) -> set[str]:
    """The servers whose tools *toolbox* offers, from their schemas' names."""
    names = (s.get("function", {}).get("name", "") for s in toolbox.schemas())
    return {str(n).partition(mcp_tools.TOOL_NAME_SEP)[0] for n in names}


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
    request += note_for(toolbox)
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
