"""
Agent Worker — NATS consumer for LangGraph workflow execution.

Listens for ``workspace.generate`` events and invokes the corresponding
LangGraph agent graph.  This worker decouples the HTTP request from the
long-running AI generation process.
"""

import json
import asyncio
import logging
from agents.graph import run_fabrique_workflow
from nats_client import NATSClient

logger = logging.getLogger("fabrique-worker")

async def process_agent_tasks(nats_client: NATSClient) -> None:
    """
    Background loop that subscribes to agent subjects and runs workflows.
    """
    async def task_callback(msg):
        try:
            data = json.loads(msg.data.decode())
            subject = msg.subject
            
            if subject == "workspace.generate":
                story = data.get("story")
                interaction_id = data.get("interaction_id")
                bu_id = data.get("business_unit_id")
                
                logger.info(f"Received generation task: {interaction_id} (BU: {bu_id})")
                
                # Run the LangGraph workflow
                # This is a blocking call in this coroutine, for high concurrency
                # consider running in a ThreadPoolExecutor or separate processes.
                result = run_fabrique_workflow(
                    story=story,
                    bu_id=bu_id
                )
                
                logger.info(f"Workflow completed for {interaction_id}. Status: {result.get('tests_passed')}")
                
        except Exception as e:
            logger.error(f"Error processing agent task: {e}")
        finally:
            await msg.ack()

    logger.info("Agent Worker subscribed to workspace.generate")
    await nats_client.subscribe("workspace.generate", task_callback)
