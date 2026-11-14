"""The kickoff graph's conversation nodes after the PO's (``graph``).

The Architect, the Scrum Master, the Developer, the PO's wrap-up and the
rejection node that re-routes a PO's feedback. The nodes that reach the
OpenProject tool or the engram processor (kickoff, PO, burn) stay in
``graph``, where the tests replace those collaborators; ``graph`` re-exports
every name.
"""

from typing import Any

from langchain_core.messages import AIMessage
from opentelemetry.trace import StatusCode

from services.producers.graph_agents import (
    AgentState,
    _invoke_agent,
    _node_failed,
    _publish,
    _transcript,
    tracer,
)


async def architect_node(state: AgentState) -> dict[str, Any]:
    with tracer.start_as_current_span("graph.architect") as span:
        span.set_attribute("agent.role", "Architect")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        try:
            backlog_items = state.get("backlog_items") or []
            items_text = (
                "\n".join(f"- {i}" for i in backlog_items)
                if backlog_items
                else "(see sprint brief)"
            )
            prompt = (
                f"Sprint brief:\n{state['story']}\n\n"
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                f"Backlog items:\n{items_text}\n\n"
                "As Architect, define the technical approach for this sprint: "
                "key design decisions, data model or interface changes, integration points, "
                "and architectural constraints or risks the team must respect. "
                "Be concrete — one clear paragraph per concern. No verbose preamble."
            )
            content = await _invoke_agent(
                state, "Architect", "Define technical approach", prompt
            )
            return {
                "messages": [AIMessage(content=content, name="Architect")],
                "next_agent": "ScrumMaster",
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            return await _node_failed(
                state, span, exc, "Architect", "Define technical approach", kickoff=True
            )


async def scrum_master_node(state: AgentState) -> dict[str, Any]:
    with tracer.start_as_current_span("graph.scrum_master") as span:
        span.set_attribute("agent.role", "ScrumMaster")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        try:
            prompt = (
                f"Sprint brief:\n{state['story']}\n\n"
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                f"OpenProject ticket: {state.get('openproject_ticket_id') or 'pending'}\n"
                "As Scrum Master, coordinate sequencing, identify one blocker, and assign the first execution move to the Developer."
            )
            content = await _invoke_agent(
                state, "ScrumMaster", "Coordinate sprint execution", prompt
            )
            return {
                "messages": [AIMessage(content=content, name="ScrumMaster")],
                "next_agent": "Developer",
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            return await _node_failed(
                state,
                span,
                exc,
                "ScrumMaster",
                "Coordinate sprint execution",
                "Scrum Master",
            )


async def developer_node(state: AgentState) -> dict[str, Any]:
    with tracer.start_as_current_span("graph.developer") as span:
        span.set_attribute("agent.role", "Developer")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        try:
            prompt = (
                f"Sprint brief:\n{state['story']}\n\n"
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                "As Developer, pick up the first actionable item, state exactly what you will implement first, and mention any dependency or unblock request."
            )
            content = await _invoke_agent(
                state, "Developer", "Pick up first sprint task", prompt
            )
            return {
                "messages": [AIMessage(content=content, name="Developer")],
                "next_agent": "PO_WRAP",
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            return await _node_failed(
                state, span, exc, "Developer", "Pick up first sprint task"
            )


async def po_wrap_node(state: AgentState) -> dict[str, Any]:
    with tracer.start_as_current_span("graph.po_wrap") as span:
        span.set_attribute("agent.role", "PO")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        try:
            prompt = (
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                "As PO, close the kickoff by summarizing priority, owner, and next immediate action in 3 compact bullets."
            )
            content = await _invoke_agent(state, "PO", "Close sprint kickoff", prompt)
            return {
                "messages": [AIMessage(content=content, name="PO")],
                "next_agent": "burn",
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            return await _node_failed(
                state, span, exc, "PO", "Close sprint kickoff", "PO wrap-up"
            )


async def rejection_node(state: AgentState) -> dict[str, Any]:
    """
    Process PO rejection feedback and route to the appropriate agent for revision.

    Reads ``state["po_feedback"]``, announces the rejection in the GlassRoom,
    then delegates to the Developer (code changes) or Architect (design concerns)
    based on a lightweight keyword heuristic on the feedback text.
    """
    with tracer.start_as_current_span("graph.rejection") as span:
        feedback = state.get("po_feedback", "")
        span.set_attribute("sprint.id", str(state.get("sprint_id") or ""))
        span.set_attribute("company.id", str(state.get("company_id") or ""))
        span.set_attribute("po_feedback.length", len(feedback))

        await _publish(
            state,
            {
                "type": "status",
                "semantic_type": "hitl.rejection_received",
                "agent": "System",
                "role": "System",
                "task_status": "rejected",
                "phase": "rejection",
                "task": "Process PO rejection feedback",
                "message": f"🔄 PO rejected task. Re-routing with feedback: {feedback[:200]}",
            },
        )

        # Route to Architect for design/scope concerns, Developer otherwise.
        design_keywords = {
            "architect",
            "design",
            "scope",
            "interface",
            "schema",
            "model",
            "api",
        }
        route_to = (
            "architect"
            if any(kw in feedback.lower() for kw in design_keywords)
            else "developer"
        )

        try:
            prompt = (
                f"Sprint brief:\n{state['story']}\n\n"
                f"Conversation so far:\n{_transcript(state['messages'])}\n\n"
                f"⚠️ PO REJECTION FEEDBACK: {feedback}\n\n"
                f"As {'Architect' if route_to == 'architect' else 'Developer'}, "
                "address the PO's specific concerns point-by-point and propose a revised approach."
            )
            role = "Architect" if route_to == "architect" else "Developer"
            content = await _invoke_agent(
                state, role, "Address PO rejection feedback", prompt
            )
            return {
                "messages": [AIMessage(content=content, name=role)],
                "next_agent": "po_wrap",
                "last_error": None,
                "po_feedback": None,  # Clear feedback after processing
            }
        except Exception as exc:  # noqa: BLE001 - A failed node is a failed kickoff, never a crash.
            span.set_status(StatusCode.ERROR, str(exc))
            return {"last_error": str(exc), "next_agent": "burn"}
