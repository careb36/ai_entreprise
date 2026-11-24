"""The ScrumMaster reads the blocked task's files before deciding (``contracts/tool_steps``).

The ScrumMaster's decision on a blocked task (``scrum_master.prepare_triage``
and ``decide``) was one plain call over the task's record. Its step's ceiling
is reading (``tool_steps.SCRUM_DECISION``: R alone): where the daemon answers
and the task has a root (its worktree, its last delivery's folder or the
project: ``developer_tools.tool_root``), the question runs through the tool
loop (``mcp_tools.chat_with_tools``) with the read-only workspace tools
(``review_tools.for_step``: list, read, search, find_file; never edit, move,
delete or run_tests), for at most ``SCRUM_TRIAGE_TOOL_ROUNDS`` rounds.

The record tools (``board_record``: ``board__task``, ``board__evidence``) go
beside the reads, scoped to the blocked task's sprint (``sprint_scope``), so
the decision can read what was asked, attempted and observed on that task
and the ones around it; never without the reads.

Each reading round is an audit turn (``ACTION_SCRUM_TRIAGE_READ``) and the
final answer's turn carries every call's observation (``tool_calls``); the
decision stays a claim, parsed as before. Without a toolbox (rounds 0, no
daemon or root), or when the answer after the reads failed or carried no
decision, it is today's single call (``scrum_master.ask_triage``).
"""

from __future__ import annotations

from typing import Any

from config import settings
from database import engine
from logging_config import setup_logger

from services.contracts import roles, tool_steps
from services.learning import scrum_master
from services.loop.worker import board_record
from services.platform.orchestration_metrics import (
    ACTION_SCRUM_TRIAGE_READ,
    record_agent_turn,
)
from services.tools import board_tools, mcp_tools, review_tools

SCRUM_MASTER_ROLE = roles.SCRUM_MASTER

logger = setup_logger("fabrique-task-worker")

READ_NOTE = (
    "\n\nBefore you decide you may read the task's files with the read-only "
    "workspace tools (list, read, search, find_file), for at most {rounds} "
    "round(s). You cannot edit, move, delete or run anything. Then answer in "
    "exactly the format asked above."
)
EXHAUSTED_NOTE = (
    "The reading budget is spent; answer now with your decision, in exactly "
    "the format asked above."
)


def toolbox(company_id: int | None, item_id: int | None) -> Any:
    """The ScrumMaster's read-only tools on the task's root, with the sprint's
    record tools beside them, or ``None``."""
    rounds = settings.SCRUM_TRIAGE_TOOL_ROUNDS
    box = review_tools.for_step(
        tool_steps.SCRUM_DECISION,
        SCRUM_MASTER_ROLE,
        company_id,
        item_id,
        engine=engine,
        rounds=rounds,
    )
    return board_record.offer(
        box, company_id, item_id, rounds=rounds, engine=engine, sprint=True
    )


def answer(
    model: str,
    system_prompt: str | None,
    prompt: str,
    company_id: int | None,
    item_id: int | None,
    sprint_id: int | None = None,
) -> str:
    """The ScrumMaster's answer to the triage *prompt*, after reading where it can."""
    box = toolbox(company_id, item_id)
    if box is None:
        return scrum_master.ask_triage(model, system_prompt, prompt, company_id)
    agent = {"role": SCRUM_MASTER_ROLE, "name": SCRUM_MASTER_ROLE}

    def record(result: dict[str, Any], preview: str, calls: Any = None) -> None:
        if company_id is not None:
            record_agent_turn(
                company_id=company_id,
                sprint_id=sprint_id,
                backlog_item_id=item_id,
                agent={**agent, "model": result.get("served_model") or model},
                action=ACTION_SCRUM_TRIAGE_READ,
                llm_result=result,
                result_preview=preview,
                tool_calls=calls,
            )

    client = scrum_master.client_for(
        timeout=scrum_master.TRIAGE_TIMEOUT_SECONDS, retries=0, company_id=company_id
    )
    note = READ_NOTE.format(rounds=box.max_rounds)
    if board_tools.has_board(box):  # The record tools, said once.
        note += " " + board_tools.NOTE
    try:
        result, reads = mcp_tools.chat_with_tools(
            client,
            scrum_master.messages_for(system_prompt, prompt + note),
            model,
            SCRUM_MASTER_ROLE,
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
        record(result, f"decision after {len(reads)} read(s)", reads)
    try:
        text = scrum_master.content_of(result)
    except scrum_master.ScrumMasterError as exc:
        logger.info("ScrumMaster's answer after its reads failed: %s", exc)
        text = ""
    if text and scrum_master.parse_answer(text) is not None:
        return text
    # No decision after the reads: today's call, which the parser expects.
    return scrum_master.ask_triage(model, system_prompt, prompt, company_id)
