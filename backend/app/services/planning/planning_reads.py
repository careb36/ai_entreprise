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
keeps today's form and parser, with one optional key: with the reads the PO
is asked to check first what is already done (``already_done.NOTE``) and to
name it in ``already_done``. Without a toolbox (rounds 0, no daemon or
root, or a call made on an event loop, where a daemon request from here
would wait on that same loop) and when the loop's answer failed or came
back empty, the plan is today's single call; ``answer`` says whether the
reads were offered, so a claim is kept only where it was asked for.

The plan from an approved specification (``spec_planning.plan_from``) goes
through the same reads and the same check (``spec_answer``), with the PO's
system prompt and billing unit; without a toolbox it is ``spec_author.ask``,
today's single call.

The PO writing a specification and the Architect writing or revising an
architecture (``tool_steps.SPEC_AUTHOR``, ``ARCH_AUTHOR``: R and S) read the
project the same way beside their research tools (``author_turn``), for at
most ``PLANNING_TOOL_ROUNDS`` (the PO) or ``ARCHITECT_TOOL_ROUNDS`` (the
Architect) rounds; each round is an audit turn (``spec_author_read``,
``arch_author_read``) and the final one carries every call's observation.
Without a daemon or root, the research tools alone, as before.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from database import engine
from logging_config import logger

from services.contracts import roles, tool_steps
from services.planning import already_done
from services.platform.orchestration_metrics import (
    ACTION_ARCH_AUTHOR_READ,
    ACTION_PLAN_READ,
    ACTION_SPEC_AUTHOR_READ,
    record_agent_turn,
)
from services.tasks import task_records
from services.tools import board_tools, developer_tools, mcp_tools, review_tools

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
AUTHOR_NOTE = (
    "\n\n## Reading the project\n"
    "Before you write you may read the project with the read-only workspace "
    "tools (list, read, search, find_file), beside the research tools, for at "
    "most {rounds} round(s): what already exists, its names and its tests. "
    "You cannot edit, move, delete or run anything. Then answer in exactly "
    "the JSON format asked above, never with a tool call written as text."
)
AUTHOR_EXHAUSTED_NOTE = (
    "The tool budget is spent; answer now in exactly the JSON format asked above."
)
# Per author step: the setting of its reading rounds and its audit action.
AUTHOR_STEPS = {
    tool_steps.SPEC_AUTHOR: ("PLANNING_TOOL_ROUNDS", ACTION_SPEC_AUTHOR_READ),
    tool_steps.ARCH_AUTHOR: ("ARCHITECT_TOOL_ROUNDS", ACTION_ARCH_AUTHOR_READ),
}


def read_box(step: str, role: str, company_id: int | None, rounds: int) -> Any:
    """The read-only tools of *step* on the project, or ``None`` (no company,
    a call on an event loop, or ``review_tools.for_step``'s own reasons)."""
    if company_id is None:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # A worker thread: the daemon is reached on the worker's loop.
    else:
        return None
    return review_tools.for_step(step, role, company_id, rounds=rounds)


def toolbox(company_id: int | None) -> Any:
    """The PO's read-only tools on the project, or ``None`` (see the module)."""
    rounds = settings.PLANNING_TOOL_ROUNDS
    box = read_box(tool_steps.PLAN, roles.PO, company_id, rounds)
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


def recorder(company_id: int | None, role: str, model: str, action: str) -> Any:
    """``record(result, preview, calls=None)``: one audit turn of *role*'s
    reads, the observations of its tool calls in ``tool_calls``."""

    def record(result: dict[str, Any], preview: str, calls: Any = None) -> None:
        served = result.get("served_model") if isinstance(result, dict) else None
        record_agent_turn(
            company_id=company_id,
            sprint_id=None,
            backlog_item_id=None,
            agent={"role": role, "name": role, "model": served or model},
            action=action,
            llm_result=result,
            result_preview=preview,
            tool_calls=calls,
        )

    return record


def plan_note(box: Any) -> str:
    """What the planning request says the tools may do, and the check."""
    note = READ_NOTE.format(rounds=box.max_rounds) + already_done.NOTE
    if board_tools.has_board(box):  # The record tools, said once.
        note += " " + board_tools.NOTE
    return note


def answer(
    client: Any, prompt: str, model: str, company_id: int | None
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """The PO's answer to the planning *prompt*, the observations of its
    reads, and whether the reads (and the already-done check) were offered."""
    messages = [{"role": "user", "content": prompt}]
    box = toolbox(company_id)
    if box is None:
        return client.chat(messages=messages, model=model, role=roles.PO), [], False
    record = recorder(company_id, roles.PO, model, ACTION_PLAN_READ)
    try:
        result, reads = mcp_tools.chat_with_tools(
            client,
            [{"role": "user", "content": prompt + plan_note(box)}],
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
        return client.chat(messages=messages, model=model, role=roles.PO), reads, True
    return result, reads, True


def spec_answer(
    request: dict[str, Any], max_tokens: int
) -> tuple[str, list[dict[str, Any]], bool]:
    """The PO's plan from a ``spec_planning.prepare_plan``ned *request*: its
    text, the observations of its reads and whether they were offered.
    Without a toolbox, ``spec_author.ask``; an unusable answer after the reads
    is asked once more without tools. Raises ``SpecAuthorError`` as ``ask``."""
    from services.planning import spec_author

    company_id, model = request.get("company_id"), request["model"]
    plain: dict[str, Any] = {
        "max_tokens": max_tokens,
        "bu_id": request["bu_id"],
        "company_id": company_id,
    }
    box = toolbox(company_id)
    if box is None:
        text = spec_author.ask(
            model, request["system_prompt"], request["prompt"], **plain
        )
        return text, [], False
    result, reads = spec_author.tool_turn(
        model,
        request["system_prompt"],
        request["prompt"] + plan_note(box),
        toolbox=box,
        record=recorder(company_id, roles.PO, model, ACTION_PLAN_READ),
        exhausted_note=EXHAUSTED_NOTE,
        **plain,
    )
    if text := _content(result).strip():
        return text, reads, True
    logger.info("The PO's plan after its reads was unusable; asking once more")
    text = spec_author.ask(model, request["system_prompt"], request["prompt"], **plain)
    return text, reads, True


def author_turn(
    step: str, role: str, company_id: int | None, model: str, research: Any
) -> tuple[Any, str, Any]:
    """The toolbox, the request's note and the turn recorder of an author
    turn (*step*: ``SPEC_AUTHOR`` or ``ARCH_AUTHOR``): the read-only tools
    beside *research* (the research toolbox, or ``None``). Without reads,
    *research* alone, no note and no recorder: the turn as before."""
    setting, action = AUTHOR_STEPS[step]
    box = read_box(step, role, company_id, int(getattr(settings, setting)))
    if box is None:
        return research, "", None
    if research is not None and tool_steps.allows(
        step, roles.canonical(role), tool_steps.RESEARCH
    ):
        # Beside the reads, keeping its own rounds: the turn's limit is the larger.
        box = developer_tools.CombinedToolbox(box, None, research)
    elif research is not None:
        research.close()
    note = AUTHOR_NOTE.format(rounds=box.max_rounds)
    return box, note, recorder(company_id, role, model, action)
