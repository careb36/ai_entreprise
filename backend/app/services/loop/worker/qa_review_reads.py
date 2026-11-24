"""QA looks at the delivery before its verdict (issue #766).

The review prompt (``review_prompt.build``) shows the branch diff, the agent's
message, the Verifier's checks and the end of the test output, each cut to a
budget; reviews said the excerpts stopped where it mattered, or that they could
not confirm a claim. Where the task's delivery was recorded and the daemon
answers, the review turn gets the read-only workspace tools on the folder the
delivery was written to (``services/tools/review_tools.py``: list, read,
search, find_file; never edit or run_tests), through the Developer's tool loop
(``mcp_tools.chat_with_tools``), for at most ``QA_REVIEW_TOOL_ROUNDS`` rounds.

The verdict keeps today's form: the same system and user messages, the same
client (``structured``: thinking off, ``REVIEW_MAX_TOKENS``) and the same
parser in ``qa_review``. An answer from the loop that carries no verdict falls
back to today's single call, tool-less and unchanged, so the reads can never
cost a parseable verdict. Without a toolbox (no recorded delivery, no daemon,
rounds 0) the review is that single call alone.

Each reading round is an audit turn of its own (``ACTION_QA_REVIEW_READ``:
priced, never counted in the QA rate), and every tool call's observation goes
on the verdict's turn (``tool_calls``); what the reviewer says it read stays a
claim, the observations are the backend's.

The record tools (``board_record``: ``board__task``, ``board__evidence``) go
beside the reads, scoped to the task and the tasks it depends on, within the
same rounds; never without them.
"""

from __future__ import annotations

from typing import Any

from config import settings
from database import engine
from logging_config import setup_logger

from services.loop.answers import llm_content, llm_failure
from services.loop.worker import board_record
from services.platform.orchestration_metrics import (
    ACTION_QA_REVIEW_READ,
    classify_review_result,
    record_agent_turn,
)
from services.tools import board_tools, mcp_tools, review_tools

logger = setup_logger("fabrique-task-worker")

LOOK_NOTE = (
    "Before your verdict you may look at the delivery itself with the "
    "read-only workspace tools (list, read, search, find_file) on the folder "
    "it was written to, for at most {rounds} round(s). Read where the excerpts "
    "above are cut, or where a claim cannot be confirmed from them. Then answer "
    "in exactly the format asked above."
)
EXHAUSTED_NOTE = (
    "The reading budget is spent; answer now with your verdict, in exactly the "
    "format asked above."
)


def _toolbox(company_id: int | None, item_id: int | None) -> Any:
    rounds = settings.QA_REVIEW_TOOL_ROUNDS
    try:
        box = review_tools.read_only_toolbox(
            company_id, item_id, engine=engine, rounds=rounds
        )
    except Exception as exc:  # noqa: BLE001 - Without tools, today's single call.
        logger.warning("QA review tools unavailable for item=%s: %s", item_id, exc)
        box = None
    # The task's record and the Verifier's evidence (``board_record``).
    return board_record.offer(box, company_id, item_id, rounds=rounds, engine=engine)


def answer(
    client: Any,
    request: list[dict[str, Any]],
    model: str,
    agent: dict[str, Any],
    company_id: int | None,
    sprint_id: int | None,
    item_id: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The proxy's answer to the review and the observations of the reads."""
    role = agent.get("role", "QA")
    toolbox = _toolbox(company_id, item_id)
    if toolbox is None:
        return client.chat(model=model, messages=request, role=role), []

    def record_round(result: dict[str, Any], calls: list[dict[str, Any]]) -> None:
        if company_id is not None:
            record_agent_turn(
                company_id=company_id,
                sprint_id=sprint_id,
                backlog_item_id=item_id,
                agent={**agent, "model": result.get("served_model") or model},
                action=ACTION_QA_REVIEW_READ,
                llm_result=result,
                result_preview="tool calls: " + mcp_tools.summarize(calls),
            )

    look = LOOK_NOTE
    if board_tools.has_board(toolbox):  # The record tools, said once.
        look += " " + board_tools.NOTE
    note = {"role": "user", "content": look.format(rounds=toolbox.max_rounds)}
    try:
        result, reads = mcp_tools.chat_with_tools(
            client,
            [*request, note],
            model,
            role,
            toolbox,
            max_rounds=toolbox.max_rounds,
            on_round=record_round,
            exhausted_note=EXHAUSTED_NOTE,
        )
    finally:
        toolbox.close()
    if llm_failure(result, model) or classify_review_result(llm_content(result)):
        return result, reads  # A failure is reported as today's would be.
    # No verdict after the reads: today's call, which the parser expects.
    return client.chat(model=model, messages=request, role=role), reads
