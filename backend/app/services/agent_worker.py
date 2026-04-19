"""
Agent Worker — NATS consumer for LangGraph workflow execution.

Listens for ``workspace.generate`` events and invokes the corresponding
LangGraph agent graph.  This worker decouples the HTTP request from the
long-running AI generation process.

Concurrency model
-----------------
``run_fabrique_workflow()`` is a synchronous, CPU/IO-bound function (LangGraph
graph traversal + LLM HTTP calls).  Calling it directly inside a coroutine
would block the event loop and stall every other NATS callback, SSE fan-out,
and HTTP request handled by the same process.

``asyncio.to_thread()`` offloads the call to the default ``ThreadPoolExecutor``
so the event loop remains free to process other coroutines while the workflow
runs in a worker thread.  Each concurrent ``workspace.generate`` message gets
its own thread, up to the executor's thread limit (default: ``min(32, cpu+4)``).
"""

import asyncio
import functools
import json
import logging

from agents.graph import run_fabrique_workflow
from nats_client import NATSClient

logger = logging.getLogger("fabrique-worker")


async def process_agent_tasks(nats_client: NATSClient) -> None:
    """
    Background loop that subscribes to ``workspace.generate`` and runs workflows.

    Each incoming message is processed in a dedicated thread via
    ``asyncio.to_thread()`` so the event loop is never blocked by the
    synchronous LangGraph execution.
    """

    async def task_callback(msg, data=None):
        if data is None:
            data = {}
        try:
            if not data:
                data = json.loads(msg.data.decode())
            subject = msg.subject

            if subject == "workspace.generate":
                story = data.get("story")
                interaction_id = data.get("interaction_id")
                bu_id = data.get("business_unit_id")

                logger.info(f"Received generation task: {interaction_id} (BU: {bu_id})")

                # Offload the synchronous LangGraph workflow to a thread so the
                # event loop stays free for other NATS callbacks and HTTP requests.
                result = await asyncio.to_thread(
                    functools.partial(run_fabrique_workflow, story=story, bu_id=bu_id)
                )

                logger.info(
                    "Workflow completed for %s. tests_passed=%s engram_id=%s",
                    interaction_id,
                    result.get("tests_passed"),
                    result.get("engram_id"),
                )

        except Exception as e:
            logger.error(
                "Error processing agent task (interaction_id=%s): %s",
                data.get("interaction_id", "unknown"),
                e,
                exc_info=True,
            )

    logger.info("Agent Worker subscribed to workspace.generate")
    await nats_client.subscribe("workspace.generate", task_callback)
