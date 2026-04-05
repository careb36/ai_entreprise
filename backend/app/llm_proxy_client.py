"""
LLMProxyClient — HTTP client for the internal LLM proxy service.

The proxy (``llm-proxy/app.py``) centralises all model routing, API-key
management, token-budget enforcement, and Gemini model-catalogue resolution.
This client provides a simple synchronous interface so that backend services
do not need to import or configure LLM SDKs directly.

All methods return a plain dict so callers can inspect ``result.get("error")``
without catching exceptions for common failure paths.
"""

import os
import requests
import logging
from typing import Optional, Dict, Any
from opentelemetry import propagate

logger = logging.getLogger("fabrique-backend")

class LLMProxyClient:
    """
    Synchronous HTTP client for the Fabrique LLM proxy with Governance support.

    Args:
        base_url: Base URL of the LLM proxy service (default: ``http://localhost:5100``).
                  Override via the ``LLM_PROXY_URL`` environment variable in production.
    """

    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url
        self.timeout = 60  # Increased for complex agentic prompts

    def _get_headers(self, role: str = "Developer", bu_id: Optional[int] = None) -> Dict[str, str]:
        """Construct headers for trace propagation and governance."""
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Role": role,
        }
        if bu_id:
            headers["X-Business-Unit-ID"] = str(bu_id)
        
        # Propagate OpenTelemetry context (TraceParent)
        propagate.inject(headers)
        return headers

    def chat(self, messages: list, model: str = "gemini-pro", role: str = "Developer", bu_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Send a multi-turn chat request to the LLM proxy (OpenAI-compatible).
        """
        try:
            headers = self._get_headers(role, bu_id)
            payload = {"messages": messages, "model": model}
            
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"LLM Proxy call failed: {e}")
            return {"error": str(e), "ok": False}

    def embed(self, text: str, role: str = "Developer", bu_id: Optional[int] = None) -> Optional[list]:
        """Retrieve a dense vector embedding."""
        try:
            headers = self._get_headers(role, bu_id)
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": text},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            # The proxy returns {"data": [{"embedding": [...]}]} (OpenAI style)
            data = response.json().get("data", [])
            if data:
                return data[0].get("embedding")
            return None
        except requests.RequestException as e:
            logger.error(f"LLM Proxy embedding failed: {e}")
            return None
