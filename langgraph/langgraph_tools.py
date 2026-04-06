"""
LangGraph Tools for Factory System

Global Circuit Breaker Policy:
- Max retries per subgraph: 3
- Max time without measurable progress: 10 minutes
- Max iterations without progress: 5
- Token budget per sprint: 2,000,000 tokens (enforced by LLM proxy)

NATS Subjects:
- task.assign
- task.update
- task.progress
- workspace.generate
- glassroom.{sprint_id}
- audit.write

Production Notes:
- In production, use Vault/KMS for token retrieval instead of environment variables
- Enable mTLS between backend, LangGraph runtime, and NATS
- All agent commits must include interaction_id and decision_signature
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, Optional, List
from uuid import uuid4
from datetime import datetime

from langchain_core.tools import tool
import requests

logger = logging.getLogger(__name__)

# NATS client will be initialized by the backend
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# TODO: In production, replace with Vault/KMS token retrieval
# OPENPROJECT_TOKEN_PO should be retrieved from Vault in production
OPENPROJECT_TOKEN_PO = os.getenv("OPENPROJECT_TOKEN_PO", "")
OPENPROJECT_TOKEN_DEV = os.getenv("OPENPROJECT_TOKEN_DEV", "")
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://localhost:8080")


def get_audit_entry(interaction_id: str, agent_id: str, action: str, 
                    response: Dict[str, Any], cost_tokens: int = 0) -> Dict[str, Any]:
    """Create audit entry payload."""
    return {
        "interaction_id": interaction_id,
        "agent_id": agent_id,
        "action": action,
        "details": response,
        "result": response,
        "cost_tokens": cost_tokens
    }


def write_audit(interaction_id: str, agent_id: str, action: str, 
                response: Dict[str, Any], cost_tokens: int = 0):
    """Write audit entry to backend."""
    try:
        audit_entry = get_audit_entry(interaction_id, agent_id, action, response, cost_tokens)
        requests.post(f"{BACKEND_URL}/v1/audit", json=audit_entry, timeout=5)
    except Exception as e:
        logger.error(f"Failed to write audit: {e}")


@tool
def create_work_package(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    PO Agent: Create a work package (issue) in OpenProject.
    
    Auth: Uses OPENPROJECT_TOKEN_PO (in production, retrieve from Vault/KMS)
    
    Args:
        payload: Dict with keys:
            - project_id: OpenProject project ID
            - subject: Work package title
            - description: Work package description
            - interaction_id: UUID for tracking
            - sprint_id: Optional sprint ID
    
    Returns:
        Dict with work_package_id and openproject_url, or error
    """
    project_id = payload.get("project_id", 1)
    subject = payload.get("subject", "")
    description = payload.get("description", "")
    interaction_id = payload.get("interaction_id", str(uuid4()))
    sprint_id = payload.get("sprint_id")
    
    if not subject:
        return {"ok": False, "error": "subject is required"}
    
    # TODO: Replace with Vault/KMS token retrieval in production
    token = OPENPROJECT_TOKEN_PO
    if not token:
        return {"ok": False, "error": "OPENPROJECT_TOKEN_PO not configured"}
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Include interaction_id in custom field (customField14 is a placeholder)
    work_package_data = {
        "subject": subject,
        "description": {"raw": description},
        "projectId": project_id,
        "type": 1,
        "customField14": interaction_id  # Custom field for interaction_id
    }
    
    if sprint_id:
        work_package_data["versionId"] = sprint_id
    
    try:
        response = requests.post(
            f"{OPENPROJECT_URL}/api/v3/work_packages",
            headers=headers,
            json=work_package_data,
            timeout=10
        )
        
        if response.status_code not in [200, 201]:
            error_result = {
                "ok": False,
                "error": f"OpenProject API error: {response.status_code}",
                "details": response.text
            }
            write_audit(interaction_id, "PO", "create_work_package", error_result)
            return error_result
        
        wp_data = response.json()
        work_package_id = wp_data.get("id")
        openproject_url = f"{OPENPROJECT_URL}/work_packages/{work_package_id}"
        
        result = {
            "ok": True,
            "work_package_id": work_package_id,
            "openproject_url": openproject_url,
            "interaction_id": interaction_id
        }
        
        # Publish task.assigned event to NATS
        try:
            import asyncio
            from backend.app.nats_client import NATSClient
            
            async def publish_event():
                nc = NATSClient(NATS_URL)
                await nc.connect()
                await nc.publish("task.assign", {
                    "work_package_id": work_package_id,
                    "interaction_id": interaction_id,
                    "agent": "PO"
                })
                await nc.close()
            
            asyncio.run(publish_event())
        except Exception as e:
            logger.warning(f"Failed to publish NATS event: {e}")
        
        write_audit(interaction_id, "PO", "create_work_package", result)
        return result
        
    except requests.RequestException as e:
        error_result = {"ok": False, "error": str(e)}
        write_audit(interaction_id, "PO", "create_work_package", error_result)
        return error_result


@tool
def transition_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dev Agent: Update work package status and add journal comment.
    
    Auth: Uses OPENPROJECT_TOKEN_DEV (in production, retrieve from Vault/KMS)
    
    Args:
        payload: Dict with keys:
            - work_package_id: OpenProject work package ID
            - status: New status (e.g., "in_progress", "resolved", "closed")
            - description: Updated description (optional)
            - comment: Journal comment
            - interaction_id: UUID for tracking
            - retry_count: Current retry count (for circuit breaker)
    
    Returns:
        Dict with status update result, or circuit_breaker response if retries >= 3
    """
    work_package_id = payload.get("work_package_id")
    status = payload.get("status")
    description = payload.get("description")
    comment = payload.get("comment", "")
    interaction_id = payload.get("interaction_id", str(uuid4()))
    retry_count = payload.get("retry_count", 0)
    
    # Circuit breaker: if retry_count >= 3, return circuit breaker response
    if retry_count >= 3:
        circuit_breaker_response = {
            "ok": False,
            "error": "circuit_breaker_open",
            "message": "Max retries (3) exceeded for this subgraph",
            "retry_count": retry_count,
            "circuit_breaker": True
        }
        write_audit(interaction_id, "Dev", "transition_ticket", circuit_breaker_response)
        return circuit_breaker_response
    
    if not work_package_id or not status:
        return {"ok": False, "error": "work_package_id and status are required"}
    
    # TODO: Replace with Vault/KMS token retrieval in production
    token = OPENPROJECT_TOKEN_DEV
    if not token:
        return {"ok": False, "error": "OPENPROJECT_TOKEN_DEV not configured"}
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Build update payload
    update_payload = {
        "lockVersion": payload.get("lockVersion", 0)
    }
    
    # Status transition via API
    status_mapping = {
        "in_progress": 2,
        "resolved": 3,
        "closed": 4,
        "feedback": 7
    }
    
    if status in status_mapping:
        update_payload["status"] = status_mapping[status]
    
    if description:
        update_payload["description"] = {"raw": description}
    
    if comment:
        update_payload["comment"] = {"raw": comment}
    
    try:
        # PATCH to update work package
        response = requests.patch(
            f"{OPENPROJECT_URL}/api/v3/work_packages/{work_package_id}",
            headers=headers,
            json=update_payload,
            timeout=10
        )
        
        if response.status_code not in [200, 201]:
            # On failure, increment retry and return
            error_result = {
                "ok": False,
                "error": f"OpenProject API error: {response.status_code}",
                "retry_count": retry_count + 1
            }
            write_audit(interaction_id, "Dev", "transition_ticket", error_result)
            return error_result
        
        wp_data = response.json()
        
        result = {
            "ok": True,
            "work_package_id": work_package_id,
            "new_status": status,
            "interaction_id": interaction_id,
            "retry_count": retry_count
        }
        
        # Publish task.progress event to NATS
        try:
            import asyncio
            from backend.app.nats_client import NATSClient
            
            async def publish_event():
                nc = NATSClient(NATS_URL)
                await nc.connect()
                await nc.publish("task.progress", {
                    "work_package_id": work_package_id,
                    "new_status": status,
                    "interaction_id": interaction_id,
                    "agent": "Dev"
                })
                await nc.close()
            
            asyncio.run(publish_event())
        except Exception as e:
            logger.warning(f"Failed to publish NATS event: {e}")
        
        write_audit(interaction_id, "Dev", "transition_ticket", result)
        return result
        
    except requests.RequestException as e:
        error_result = {
            "ok": False,
            "error": str(e),
            "retry_count": retry_count + 1
        }
        write_audit(interaction_id, "Dev", "transition_ticket", error_result)
        return error_result


# Additional helper tools for LangGraph orchestration

@tool
def search_memory(query: str) -> List[Dict[str, Any]]:
    """
    Search both pgvector and Neo4j memory stores for content relevant to *query*.

    Delegates to the ``/v1/memory/search`` backend endpoint, which runs a
    pgvector ANN search and returns the top results with similarity scores.

    Args:
        query: Natural-language search string.

    Returns:
        List of result dicts (``content``, ``metadata``, ``similarity``),
        or an empty list on error.
    """
    try:
        response = requests.get(
            f"{BACKEND_URL}/v1/memory/search",
            params={"query": query},
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception as e:
        logger.error(f"Memory search failed: {e}")
    return []


@tool
def store_memory(content: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Persist arbitrary content to the dual-store memory (pgvector + Neo4j).

    The backend endpoint embeds the content and writes it to both stores.

    Args:
        content:  Text to store (will be embedded for similarity search).
        metadata: Optional key-value metadata attached to the memory entry.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": ...}`` on failure.
    """
    try:
        response = requests.post(
            f"{BACKEND_URL}/v1/memory/store",
            json={"content": content, "metadata": metadata},
            timeout=10
        )
        return response.json() if response.status_code == 200 else {"ok": False}
    except Exception as e:
        logger.error(f"Memory store failed: {e}")
        return {"ok": False, "error": str(e)}


@tool
def publish_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish a NATS event from within a LangGraph tool context.

    Creates a short-lived ``NATSClient``, publishes the event, and closes the
    connection.  For high-throughput use cases, prefer sharing a persistent
    client via the backend's singleton instead.

    Args:
        event_type: NATS subject string (e.g. ``"task.progress"``).
        payload:    JSON-serialisable dict to publish.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": ...}`` on failure.
    """
    try:
        import asyncio
        from backend.app.nats_client import NATSClient
        
        async def publish():
            nc = NATSClient(NATS_URL)
            await nc.connect()
            await nc.publish(event_type, payload)
            await nc.close()
        
        asyncio.run(publish())
        return {"ok": True}
    except Exception as e:
        logger.error(f"Publish event failed: {e}")
        return {"ok": False, "error": str(e)}


@tool
def llm_complete(prompt: str, model: str = "gemini-pro") -> Dict[str, Any]:
    """
    Execute a single-turn LLM completion via the internal proxy.

    Wraps ``LLMProxyClient.complete()`` so LangGraph tools don't need to
    import SDK-specific classes.

    Args:
        prompt: Prompt text.
        model:  Model name (resolved by the proxy to the best available model).

    Returns:
        Proxy response dict, or ``{"ok": False, "error": ...}`` on failure.
    """
    try:
        from backend.app.llm_proxy_client import LLMProxyClient
        client = LLMProxyClient()
        return client.complete(prompt, model)
    except Exception as e:
        logger.error(f"LLM complete failed: {e}")
        return {"ok": False, "error": str(e)}


# LangGraph Graph Definition (pseudocode - actual implementation requires LangGraph setup)
"""
from langgraph.graph import StateGraph, END

class FactoryState(TypedDict):
    interaction_id: str
    company_id: Optional[int]
    task_type: str
    payload: Dict
    result: Optional[Dict]
    history: List[Dict]

def create_factory_graph() -> StateGraph:
    workflow = StateGraph(FactoryState)
    
    workflow.add_node("create_work_package", create_work_package)
    workflow.add_node("transition_ticket", transition_ticket)
    workflow.add_node("search_memory", search_memory)
    workflow.add_node("store_memory", store_memory)
    workflow.add_node("llm_complete", llm_complete)
    
    workflow.set_entry_point("route_task")
    
    # Add conditional edges based on task type
    workflow.add_conditional_edges(
        "route_task",
        lambda state: state["task_type"],
        {
            "create": "create_work_package",
            "update": "transition_ticket",
            "search": "search_memory",
            "store": "store_memory",
            "generate": "llm_complete"
        }
    )
    
    return workflow.compile()
"""
