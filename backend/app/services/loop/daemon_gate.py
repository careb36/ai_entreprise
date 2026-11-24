"""No agent turn runs blind: without a workspace daemon the sprint pauses.

On 2026-11-24 (sprint 6) the daemon process had died; a QA task ran anyway,
every workspace tool call failed with "nats: no responders", QA delivered a
report without reading a file, the review sent it back and a rework was spent.
Nobody was told the daemon was gone.

Two places ask, both on what the backend observed (never on what an agent
says about the workspace):

- before the worker claims a task (``worker/intake._claim``), ``absent``:
  when no heartbeat was heard within ``DAEMON_HEARTBEAT_OFFLINE_SECONDS``
  (``daemon_liveness``), a ``workspace.status`` request asks, and only NATS's
  "no responders" (``workspace_daemon.daemon_presence``: ``absent``) counts. A
  timeout or an unreachable NATS is unknown, not absent, and the task runs;
- after a run (``worker/task_run._run_claimed_task``), ``blind``: the run made
  workspace tool calls and every one was answered "no responders"
  (``daemon_absent`` on its observation, ``tools/developer_tools``). Its
  answer is discarded instead of going to review.

Either way ``hold`` pauses the sprint with ``REASON`` (``sprint_control.
pause``), puts the task back to ``todo`` with a ``paused`` event (not a
``NEEDS WORK:``, so no rework is counted), records the incident in the audit
chain (``sprint_daemon_absent``, once per pause) and ``publish`` says it on the
sprint's Glassroom. Resuming the sprint once the daemon runs claims the task
again; resuming it without one pauses it again. Mirrors ``sprint_budget``.
"""

from __future__ import annotations

from typing import Any

from models import BacklogItem, Sprint
from sqlmodel import Session

from services.contracts.tool_calls import WORKSPACE_SERVER
from services.loop import sprint_control
from services.platform.agent_service import AgentService
from services.platform.glassroom import publish_glassroom
from services.tasks import task_events
from services.workspace import daemon_liveness, workspace_daemon

ACTION = "sprint_daemon_absent"
REASON = (
    "The workspace daemon is not answering (NATS: no responders), so no agent "
    "can read or change the project. Start it on the computer that holds the "
    "projects with ./scripts/start-daemon.sh (.\\scripts\\start-daemon.ps1 on "
    "Windows), then resume the sprint."
)


async def absent(nats_client: Any) -> bool:
    """True only when NATS says no daemon listens and none was heard lately."""
    if nats_client is None:
        return False
    if daemon_liveness.liveness()["state"] in ("online", "stale"):
        return False
    presence = await workspace_daemon.daemon_presence(nats_client)
    return presence.get("state") == "absent"


def blind(tool_calls: Any) -> int:
    """How many workspace calls the run made when NATS found no daemon for
    every one of them; ``0`` otherwise (none made, or one answered)."""
    calls = [
        c
        for c in tool_calls or []
        if isinstance(c, dict) and c.get("server") == WORKSPACE_SERVER
    ]
    if calls and all(c.get("daemon_absent") for c in calls):
        return len(calls)
    return 0


def hold(engine: Any, item_id: int, observed: str) -> int | None:
    """Pause *item_id*'s sprint and put the task back to ``todo``; the sprint's
    id when it paused now (for the Glassroom), else ``None``."""
    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        sprint = session.get(Sprint, item.sprint_id) if item else None
        if item is None or sprint is None:
            return None
        paused = sprint_control.pause(session, sprint, REASON)
        if paused:
            AgentService().audit_interaction(
                session=session,
                company_id=sprint.company_id,
                interaction_id=f"sprint:{sprint.id}:daemon",
                agent_id="system:daemon-gate",
                action=ACTION,
                details={"company_id": str(sprint.company_id), "sprint_id": sprint.id},
                result={"reason": REASON, "item_id": item.id, "observed": observed},
                decision_signature=None,
                cost_tokens=0,
                commit=False,
            )
        if paused or item.status == "in_progress":
            task_events.record(
                session,
                item,
                "paused",
                task_events.SYSTEM,
                status="todo",
                text=f"Paused: {REASON}",
            )
        session.commit()
        return sprint.id if paused else None


async def publish(nats_client: Any, sprint_id: int) -> None:
    """The pause on the sprint's Glassroom, with what to do."""
    await publish_glassroom(
        nats_client,
        sprint_id,
        {
            "type": "error",
            "semantic_type": "sprint.paused_daemon_absent",
            "source": "task_worker",
            "agent": "System",
            "role": "System",
            "task_status": sprint_control.PAUSED,
            "message": f"⏸ {REASON}",
        },
    )
