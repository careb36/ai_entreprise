"""The kickoff graph's state and the steps every node shares (``graph``).

The state the LangGraph kickoff carries, the transcript and backlog readers,
the Glassroom publisher, one agent's turn through the LLM proxy and the
failure every node reports the same way. ``graph`` re-exports every name.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from llm_proxy_client import LLMProxyClient
from opentelemetry.trace import StatusCode
from typing_extensions import TypedDict

from services.models.model_factory import default_model_for_role
from telemetry import configure_telemetry, get_tracer

configure_telemetry("agent-worker")
# The spans keep the instrumentation name they had when every node lived in
# ``graph``.
tracer = get_tracer("services.producers.graph")
logger = logging.getLogger("fabrique-agents")

PublishEvent = Callable[[dict[str, Any]], Awaitable[None]]


class AgentState(TypedDict):
    interaction_id: str
    company_id: int | None
    business_unit_id: int | None
    sprint_id: int | None
    story: str
    backlog_items: list[str]
    messages: Annotated[list[AnyMessage], add_messages]
    next_agent: str
    openproject_ticket_id: str | None
    engram_id: Any | None
    last_error: str | None
    needs_split: bool | None
    created_at: str
    publish_event: PublishEvent | None
    po_feedback: str | None


def _resolve_model_name(role: str) -> str:
    """The role's model, through the same selector every other caller uses
    (``AGENT_MODEL_<ROLE>``, ``NEMOTRON_ROLES``, ``AGENT_MODEL``, then the
    map); an unknown role is treated as the Developer."""
    return default_model_for_role(role)


def _message_text(message: AnyMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _message_name(message: AnyMessage) -> str:
    explicit_name = getattr(message, "name", None)
    if explicit_name:
        return str(explicit_name)
    message_type = getattr(message, "type", "message")
    if message_type == "human":
        return "User"
    if message_type == "system":
        return "System"
    return "Agent"


def _transcript(messages: list[AnyMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        lines.append(f"{_message_name(message)}: {_message_text(message)}")
    return "\n".join(lines[-12:])


def _extract_backlog(story: str) -> list[str]:
    items: list[str] = []
    for raw_line in story.splitlines():
        line = raw_line.strip()
        if line.startswith(("- ", "* ")):
            items.append(line[2:].strip())
    return items


async def _publish(state: AgentState, event: dict[str, Any]) -> None:
    publish_event = state.get("publish_event")
    sprint_id = state.get("sprint_id")
    if not publish_event or sprint_id is None:
        return
    payload = dict(event)
    payload.setdefault("event_id", str(uuid4()))
    payload.setdefault("source", "langgraph")
    payload.setdefault("semantic_version", 1)
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())
    await publish_event(payload)


async def _invoke_agent(
    state: AgentState,
    role: str,
    task: str,
    prompt: str,
) -> str:
    async def emit(kind: str, semantic: str, message: str) -> None:
        await _publish(
            state,
            {
                "type": kind,
                "semantic_type": semantic,
                "agent": role,
                "role": role,
                "task_status": "kickoff",
                "phase": "kickoff",
                "task": task,
                "message": message,
            },
        )

    await emit(
        "agent_start", "kickoff.agent_started", f"{role} is thinking about: {task}"
    )

    client = LLMProxyClient()
    result = await client.chat_async(
        messages=[
            {
                "role": "system",
                "content": f"You are {role} in a sprint orchestration. Keep answers concrete, operational, and aware of prior messages.",
            },
            {"role": "user", "content": prompt},
        ],
        model=_resolve_model_name(role),
        role=role,
        bu_id=state.get("business_unit_id"),
    )

    if result.get("error"):
        raise RuntimeError(str(result["error"]))

    if result.get("quota_warning"):
        await _publish(
            state,
            {
                "type": "quota.warning",
                "semantic_type": "quota.warning",
                "agent": "System",
                "role": "System",
                "task_status": "kickoff",
                "phase": "kickoff",
                "task": task,
                "message": f"⚠️ Token quota usage is above 80% (used={result.get('quota_used')}, limit={result.get('quota_limit')}). Consider resetting the quota or splitting the sprint.",
                "quota_used": result.get("quota_used"),
                "quota_limit": result.get("quota_limit"),
            },
        )

    content = (
        result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    )

    await emit("message", "kickoff.agent_message", content)
    await emit("agent_complete", "kickoff.agent_completed", f"{role} finished: {task}")
    return content


async def _node_failed(
    state: AgentState,
    span: Any,
    exc: Exception,
    role: str,
    task: str,
    label: str | None = None,
    kickoff: bool = False,
) -> dict[str, Any]:
    """Publish a node's failure, mark its span and route the graph to burn.

    Only the PO's and the Architect's failures carry the kickoff fields."""
    event: dict[str, Any] = {"type": "error"}
    if kickoff:
        event["semantic_type"] = "kickoff.agent_failed"
    event |= {"agent": role, "role": role}
    if kickoff:
        event |= {"task_status": "kickoff", "phase": "kickoff"}
    event |= {"task": task, "message": f"{label or role} failed: {exc}"}
    await _publish(state, event)
    span.set_status(StatusCode.ERROR, str(exc))
    return {"last_error": str(exc), "next_agent": "burn"}
