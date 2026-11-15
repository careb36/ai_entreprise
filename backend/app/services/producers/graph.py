"""Multi-agent LangGraph orchestration for sprint kickoff conversations.

The state and the steps every node shares are in ``graph_agents``, the
conversation nodes after the PO's in ``graph_nodes``; this module keeps the
nodes that reach the OpenProject tool and the engram processor, the graph
and its run, and re-exports the other names.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph
from llm_proxy_client import LLMProxyClient  # noqa: F401 - re-exported; tests patch it here

from services.models.model_factory import get_llm_for_role
from services.platform.engram_processor import get_engram_processor
from services.producers.graph_agents import (  # noqa: F401 - re-exported
    AgentState,
    PublishEvent,
    _extract_backlog,
    _invoke_agent,
    _message_name,
    _message_text,
    _node_failed,
    _publish,
    _resolve_model_name,
    _transcript,
    tracer,
)
from services.producers.graph_nodes import (
    architect_node,
    developer_node,
    po_wrap_node,
    rejection_node,
    scrum_master_node,
)
from services.producers.openproject_tools import create_work_package

logger = logging.getLogger("fabrique-agents")


async def kickoff_node(state: AgentState) -> dict[str, Any]:
    await _publish(
        state,
        {
            "type": "status",
            "semantic_type": "kickoff.started",
            "agent": "System",
            "role": "System",
            "task_status": "kickoff",
            "phase": "kickoff",
            "task": "Sprint kickoff",
            "message": "Sprint orchestration started. PO, Architect, Scrum Master, and Developer are joining the conversation.",
        },
    )
    messages: list[AnyMessage] = [SystemMessage(content="Sprint kickoff started.")]

    # Retrieve relevant memory from past sprints and inject into planning context.
    try:
        processor = get_engram_processor()
        memory_context = processor.query_relevant_engrams(
            state["story"], limit=4, company_id=state.get("company_id")
        )
        if memory_context:
            messages.append(
                SystemMessage(
                    content=f"Relevant memory from past sprints:\n{memory_context}"
                )
            )
            await _publish(
                state,
                {
                    "type": "status",
                    "semantic_type": "kickoff.memory_retrieved",
                    "agent": "System",
                    "role": "System",
                    "task_status": "kickoff",
                    "phase": "memory_retrieved",
                    "task": "Sprint kickoff",
                    "message": "Memory retrieved from past sprints — context injected into agent planning.",
                },
            )
    except Exception as exc:  # noqa: BLE001 - Memory is context; the kickoff goes on without it.
        logger.warning("Memory retrieval at kickoff failed (non-fatal): %s", exc)

    return {
        "messages": messages,
        "backlog_items": state.get("backlog_items") or _extract_backlog(state["story"]),
        "next_agent": "PO",
        "last_error": None,
    }


async def po_node(state: AgentState) -> dict[str, Any]:
    with tracer.start_as_current_span("graph.po") as span:
        span.set_attribute("agent.role", "PO")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        try:
            backlog_items = state.get("backlog_items") or _extract_backlog(
                state["story"]
            )
            first_ticket = (
                backlog_items[0] if backlog_items else state["story"].splitlines()[0]
            )
            tool_result = await create_work_package(
                {
                    "project_id": 1,
                    "subject": first_ticket[:120],
                    "description": state["story"],
                    "interaction_id": state["interaction_id"],
                    "sprint_id": state.get("sprint_id"),
                },
                publish_event=lambda event: _publish(state, event),
            )
            prompt = (
                f"Sprint brief:\n{state['story']}\n\n"
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                f"Backlog anchor ticket: {tool_result.get('work_package_id')}\n"
                "As PO, prioritize the sprint, call out the riskiest item, and give one clear instruction to the Scrum Master."
            )
            content = await _invoke_agent(
                state, "PO", "Prioritize sprint backlog", prompt
            )
            return {
                "messages": [AIMessage(content=content, name="PO")],
                "next_agent": "ScrumMaster",
                "openproject_ticket_id": str(tool_result.get("work_package_id")),
                "backlog_items": backlog_items,
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            return await _node_failed(
                state, span, exc, "PO", "Prioritize sprint backlog", kickoff=True
            )


async def burn_node(state: AgentState) -> dict[str, Any]:
    summary = _transcript(state["messages"])
    last_error = state.get("last_error")
    sprint_id = state.get("sprint_id")

    try:
        result = get_engram_processor().burn_engram(
            company_id=state.get("company_id"),
            agent_id=f"orchestrator_{state['interaction_id']}",
            role="PO",
            decision_summary=summary[:4000],
            metadata={
                "interaction_id": state["interaction_id"],
                "sprint_id": sprint_id,
                "needs_split": False,
                "kickoff_failed": bool(last_error),
            },
            llm=get_llm_for_role("EngramProcessor"),
        )
        engram_id = result.get("engram_id")
    except Exception as exc:  # noqa: BLE001 - A lost engram never fails the kickoff.
        logger.warning(
            "Could not burn engram for interaction_id=%s: %s",
            state["interaction_id"],
            exc,
        )
        engram_id = None

    if last_error:
        # A node error is a failed kickoff, not a decision to split the sprint.
        # The agent worker reports it to the Glassroom and keeps the sprint
        # active, because backlog tasks run independently of the kickoff.
        logger.warning(
            "Sprint kickoff failed sprint_id=%s interaction_id=%s error=%s",
            sprint_id,
            state["interaction_id"],
            last_error,
        )
    else:
        await _publish(
            state,
            {
                "type": "status",
                "semantic_type": "kickoff.completed",
                "agent": "System",
                "role": "System",
                "task": "Sprint kickoff",
                "task_status": "kickoff_complete",
                "phase": "kickoff",
                "message": "Sprint kickoff conversation finished. Background sprint execution continues on the board and task workers.",
            },
        )

    return {"engram_id": engram_id, "needs_split": False}


def _route_from(state: AgentState, fallback: str, success: str) -> str:
    if state.get("last_error"):
        return fallback
    return success


def _route_entry(state: AgentState) -> str:
    """Route to rejection_node if PO feedback is present, otherwise start with kickoff."""
    if state.get("po_feedback"):
        return "rejection"
    return "kickoff"


def create_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("kickoff", kickoff_node)
    workflow.add_node("po", po_node)
    workflow.add_node("architect", architect_node)
    workflow.add_node("scrum_master", scrum_master_node)
    workflow.add_node("developer", developer_node)
    workflow.add_node("po_wrap", po_wrap_node)
    workflow.add_node("burn", burn_node)
    workflow.add_node("rejection", rejection_node)

    workflow.set_conditional_entry_point(
        _route_entry, {"kickoff": "kickoff", "rejection": "rejection"}
    )

    def on_success(success: str) -> Callable[[AgentState], str]:
        return lambda state: _route_from(state, "burn", success)

    workflow.add_edge("kickoff", "po")
    workflow.add_conditional_edges("po", on_success("architect"))
    workflow.add_conditional_edges("architect", on_success("scrum_master"))
    workflow.add_conditional_edges("scrum_master", on_success("developer"))
    workflow.add_conditional_edges("developer", on_success("po_wrap"))
    workflow.add_conditional_edges("rejection", on_success("po_wrap"))
    workflow.add_edge("po_wrap", "burn")
    workflow.add_edge("burn", END)
    return workflow.compile()


async def run_fabrique_workflow(
    story: str,
    role: str = "Developer",
    bu_id: int | None = None,
    sprint_id: int | None = None,
    company_id: int | None = None,
    nats_client: Any = None,
    interaction_id: str | None = None,
    po_feedback: str | None = None,
) -> dict[str, Any]:
    interaction_id = interaction_id or str(uuid4())

    async def publish_event(event: dict[str, Any]) -> None:
        if not nats_client or sprint_id is None:
            return
        await nats_client.publish(f"glassroom.{sprint_id}", event)

    initial_state: AgentState = {
        "interaction_id": interaction_id,
        "company_id": company_id,
        "business_unit_id": bu_id,
        "sprint_id": sprint_id,
        "story": story,
        "backlog_items": _extract_backlog(story),
        "messages": [HumanMessage(content=story, name="User")],
        "next_agent": role,
        "openproject_ticket_id": None,
        "engram_id": None,
        "last_error": None,
        "needs_split": False,
        "created_at": datetime.now(UTC).isoformat(),
        "publish_event": publish_event,
        "po_feedback": po_feedback,
    }

    logger.info(
        "Starting multi-agent workflow interaction_id=%s sprint_id=%s role=%s story=%.80s",
        interaction_id,
        sprint_id,
        role,
        story,
    )

    try:
        graph = create_agent_graph()
        result = await graph.ainvoke(
            initial_state,
            config={
                "configurable": {"thread_id": interaction_id},
                # 6 fixed nodes × 2 safety margin = 12. Keep limit at execution time
                # for compatibility with older LangGraph versions.
                "recursion_limit": 16,
            },
        )
        logger.info(
            "Workflow completed interaction_id=%s sprint_id=%s engram_id=%s needs_split=%s",
            interaction_id,
            sprint_id,
            result.get("engram_id"),
            result.get("needs_split"),
        )
        if company_id is None:
            logger.info(
                "Skipping AgentRun ingestion without company_id interaction_id=%s",
                interaction_id,
            )
        else:
            try:
                import database
                from models.agent_run import GRAPH_PRODUCER
                from schemas import AgentRunCreate
                from sqlmodel import Session

                from services.evidence import agent_run_service

                summary_message = next(
                    (
                        message
                        for message in reversed(result.get("messages", []))
                        if _message_name(message) == "PO"
                    ),
                    result.get("messages", [])[-1] if result.get("messages") else None,
                )
                payload = AgentRunCreate(
                    company_id=company_id,
                    external_ref=f"graph:{interaction_id}",
                    producer=GRAPH_PRODUCER,
                    model=_resolve_model_name("Developer"),
                    agent_role="Developer",
                    subject={
                        "sprint_id": sprint_id,
                        "business_unit_id": bu_id,
                        "backlog_items": result.get("backlog_items"),
                    },
                    claims={
                        "summary": _message_text(summary_message)
                        if summary_message
                        else "",
                        "engram_id": result.get("engram_id"),
                        "needs_split": result.get("needs_split"),
                    },
                    artifact=_transcript(result.get("messages", [])),
                    started_at=datetime.fromisoformat(initial_state["created_at"]),
                    finished_at=datetime.now(UTC),
                )
                with Session(database.engine) as session:
                    agent_run_service.ingest_agent_run(session, payload)
            except Exception as exc:
                logger.warning(
                    "AgentRun ingestion failed for graph interaction_id=%s: %s",
                    interaction_id,
                    exc,
                    exc_info=True,
                )
        return result
    except GraphRecursionError as exc:
        # A runaway kickoff conversation is a failed kickoff, not a split
        # decision; the agent worker reports it and keeps the sprint active.
        logger.error(
            "Workflow RECURSION LIMIT hit interaction_id=%s sprint_id=%s — kickoff failed",
            interaction_id,
            sprint_id,
        )
        return {
            **initial_state,
            "error": f"GraphRecursionError: {exc}",
            "engram_id": None,
            "needs_split": False,
        }
    except Exception as exc:
        logger.exception(
            "Workflow FATAL ERROR interaction_id=%s: %s",
            interaction_id,
            exc,  # noqa: TRY401 - Preserve the existing log message as well as its traceback.
        )
        if nats_client and sprint_id is not None:
            await nats_client.publish(
                f"glassroom.{sprint_id}",
                {
                    "type": "error",
                    "agent": "System",
                    "role": "System",
                    "task": "Sprint kickoff",
                    "message": f"LangGraph workflow failed: {type(exc).__name__}: {exc}",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        return {
            **initial_state,
            "error": f"{type(exc).__name__}: {exc}",
            "engram_id": None,
            "needs_split": False,
        }
