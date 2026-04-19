import asyncio
import json

import pytest

from app.routers.agents import (
    generate_glassroom_sse,
    publish_sse_event,
    register_sse_connection,
    sse_connections,
    unregister_sse_connection,
)


@pytest.mark.asyncio
async def test_publish_sse_event_fans_out_to_all_subscribers():
    sprint_id = "fanout-1"
    queue_a: asyncio.Queue[str] = asyncio.Queue()
    queue_b: asyncio.Queue[str] = asyncio.Queue()

    register_sse_connection(sprint_id, queue_a)
    register_sse_connection(sprint_id, queue_b)

    payload = json.dumps({"type": "message", "agent": "PO", "message": "fanout"})
    await publish_sse_event(sprint_id, payload)

    assert await queue_a.get() == payload
    assert await queue_b.get() == payload

    unregister_sse_connection(sprint_id, queue_a)
    unregister_sse_connection(sprint_id, queue_b)


@pytest.mark.asyncio
async def test_glassroom_stream_emits_final_po_close_before_completion():
    sprint_id = "po-close-1"
    queue: asyncio.Queue[str] = asyncio.Queue()
    register_sse_connection(sprint_id, queue)

    async def empty_history():
        return []

    stream = generate_glassroom_sse(
        queue=queue,
        sprint_id=sprint_id,
        nats_available=True,
        history_loader=empty_history,
    )

    connected = await anext(stream)
    assert '"type": "connected"' in connected

    final_po_message = json.dumps(
        {
            "type": "message",
            "agent": "PO",
            "role": "PO",
            "task": "Close sprint kickoff",
            "message": "PO closing summary",
        }
    )
    po_complete = json.dumps(
        {
            "type": "agent_complete",
            "agent": "PO",
            "role": "PO",
            "task": "Close sprint kickoff",
            "message": "PO finished: Close sprint kickoff",
        }
    )
    system_finish = json.dumps(
        {
            "type": "status",
            "agent": "System",
            "role": "System",
            "task": "Sprint kickoff",
            "message": "Sprint kickoff conversation finished.",
        }
    )

    await queue.put(final_po_message)
    await queue.put(po_complete)
    await queue.put(system_finish)

    emitted = [await anext(stream), await anext(stream), await anext(stream)]
    decoded = [json.loads(chunk.removeprefix("data: ").strip()) for chunk in emitted]

    assert [event["type"] for event in decoded] == ["message", "agent_complete", "status"]
    assert decoded[0]["agent"] == "PO"
    assert decoded[0]["task"] == "Close sprint kickoff"
    assert decoded[2]["message"] == "Sprint kickoff conversation finished."

    await stream.aclose()
    assert sprint_id not in sse_connections