"""The PO reads the project before its sprint plan (``contracts/tool_steps``).

The planning step's ceiling is reading (``tool_steps.PLAN``: R alone). The PO
planned from one daemon listing of the project's two top levels; where the
daemon answers and the company's project path is known, the planning call
now runs through the tool loop (``mcp_tools.chat_with_tools``) with the
read-only workspace tools (``review_tools.for_step``: list, read, search,
find_file; never edit, move, delete or run_tests), for at most
``PLANNING_TOOL_ROUNDS`` rounds. The listing stays in the prompt.

The record tools (``board_tools``: ``board__task``, ``board__evidence``) go
beside the reads, scoped to the company's board (``Scope`` without task ids:
any task of the company, never another's), read by
``services/tasks/task_records.py``, so the PO can read what earlier tasks
asked, attempted and what the Verifier observed; never without the reads, and
off with ``BOARD_TOOLS_ENABLED``.

Each reading round is an audit turn (``ACTION_PLAN_READ``) and the final
answer's turn carries every call's observation (``tool_calls``): what the
PO says it read is a claim, the observations are the backend's. The answer
keeps today's form and parser. Without a toolbox (rounds 0, no daemon or
root, or a call made on an event loop, where a daemon request from here
would wait on that same loop) and when the loop's answer failed or came
back empty, the plan is today's single call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from database import engine
from logging_config import logger

from services.contracts import roles, tool_steps
from services.platform.orchestration_metrics import ACTION_PLAN_READ, record_agent_turn
from services.tasks import task_records
from services.tools import board_tools, mcp_tools, review_tools

READ_NOTE = (
    "\n\n## Reading the project\n"
    "Before you plan you may read the project with the read-only workspace "
    "tools (list, read, search, find_file), for at most {rounds} round(s). "
    "You cannot edit, move, delete or run anything. Then answer in exactly "
    "the JSON format asked above, never with a tool call written as text."
)
EXHAUSTED_NOTE = (
    "The reading budget is spent; answer now with the plan, in exactly the "
    "JSON format asked above."
)


def toolbox(company_id: int | None) -> Any:
    """The PO's read-only tools on the project, or ``None`` (see the module)."""
    if company_id is None:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # A worker thread: the daemon is reached on the worker's loop.
    else:
        return None
    rounds = settings.PLANNING_TOOL_ROUNDS
    box = review_tools.for_step(tool_steps.PLAN, roles.PO, company_id, rounds=rounds)
    return board_tools.attach(box, board(company_id, rounds) if box else None)


def board(company_id: int, rounds: int) -> board_tools.BoardToolbox | None:
    """The record tools over the company's board, or ``None`` when off."""
    if not settings.BOARD_TOOLS_ENABLED or rounds <= 0:
        return None
    return board_tools.BoardToolbox(
        board_tools.Scope(company_id),
        lambda c, t, a: task_records.read_task(engine, c, t, a),
        lambda c, t, a: task_records.read_evidence(engine, c, t, a),
        rounds=rounds,
    )


def _content(result: Any) -> str:
    if not isinstance(result, dict) or result.get("error"):
        return ""
    choices = result.get("choices") or [{}]
    return str(((choices[0] or {}).get("message") or {}).get("content") or "")


def answer(
    client: Any, prompt: str, model: str, company_id: int | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The PO's answer to the planning *prompt* and the observations of its reads."""
    messages = [{"role": "user", "content": prompt}]
    box = toolbox(company_id)
    if box is None:
        return client.chat(messages=messages, model=model, role=roles.PO), []
    agent = {"role": roles.PO, "name": roles.PO}

    def record(result: dict[str, Any], preview: str, calls: Any = None) -> None:
        record_agent_turn(
            company_id=company_id,
            sprint_id=None,
            backlog_item_id=None,
            agent={**agent, "model": result.get("served_model") or model},
            action=ACTION_PLAN_READ,
            llm_result=result,
            result_preview=preview,
            tool_calls=calls,
        )

    note = READ_NOTE.format(rounds=box.max_rounds)
    if board_tools.has_board(box):  # The record tools, said once.
        note += " " + board_tools.NOTE
    try:
        result, reads = mcp_tools.chat_with_tools(
            client,
            [{"role": "user", "content": prompt + note}],
            model,
            roles.PO,
            box,
            max_rounds=box.max_rounds,
            on_round=lambda r, calls: record(
                r, "tool calls: " + mcp_tools.summarize(calls)
            ),
            exhausted_note=EXHAUSTED_NOTE,
        )
    finally:
        box.close()
    if reads:
        record(result, f"plan after {len(reads)} read(s)", reads)
    if not _content(result).strip():
        logger.info("The PO's plan after its reads was unusable; asking once more")
        return client.chat(messages=messages, model=model, role=roles.PO), reads
    return result, reads
