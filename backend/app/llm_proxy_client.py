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
import time
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
        self.max_retries = int(os.getenv("LLM_PROXY_MAX_RETRIES", "2"))
        self.retry_backoff_sec = float(os.getenv("LLM_PROXY_RETRY_BACKOFF_SEC", "1.0"))

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
        headers = self._get_headers(role, bu_id)
        payload = {"messages": messages, "model": model}

        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )

                if response.ok:
                    return response.json()

                # Retry transient proxy/provider failures (5xx).
                if response.status_code >= 500 and attempt < self.max_retries:
                    delay = self.retry_backoff_sec * (attempt + 1)
                    logger.warning(
                        "LLM proxy transient error %s for model=%s role=%s (attempt %s/%s), retrying in %.1fs",
                        response.status_code,
                        model,
                        role,
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                body = response.text[:500]
                logger.error(
                    "LLM proxy request failed: status=%s model=%s role=%s body=%s",
                    response.status_code,
                    model,
                    role,
                    body,
                )
                return {
                    "error": f"HTTP {response.status_code}: {body}",
                    "ok": False,
                }

            except requests.RequestException as e:
                if attempt < self.max_retries:
                    delay = self.retry_backoff_sec * (attempt + 1)
                    logger.warning(
                        "LLM proxy request exception for model=%s role=%s (attempt %s/%s): %s; retrying in %.1fs",
                        model,
                        role,
                        attempt + 1,
                        self.max_retries + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error(f"LLM Proxy call failed after retries: {e}")
                return {"error": str(e), "ok": False}

    def get_available_models(self) -> dict:
        """
        Fetch the live model catalogue from the proxy.

        Returns a dict with keys:
          - ``models``: list of dicts with id, provider, tier, description
          - ``orchestrator``: recommended model for meta-tasks (team suggestion, routing)

        Returns an empty dict on error so callers can fall back gracefully.
        """
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch model catalogue: {e}")
            return {}

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
