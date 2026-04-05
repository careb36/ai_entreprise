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
from typing import Optional, Dict, Any


class LLMProxyClient:
    """
    Synchronous HTTP client for the Fabrique LLM proxy.

    Args:
        base_url: Base URL of the LLM proxy service (default: ``http://localhost:5100``).
                  Override via the ``LLM_PROXY_URL`` environment variable in production.
    """

    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url
        # 30-second timeout balances LLM latency with request reliability.
        self.timeout = 30

    def complete(self, prompt: str, model: str = "gemini-pro", **kwargs) -> Dict[str, Any]:
        """
        Send a single-turn completion request to the LLM proxy.

        Args:
            prompt: The prompt text to complete.
            model:  Model name; the proxy resolves aliases to available models.
            **kwargs: Additional fields forwarded verbatim in the JSON body
                      (e.g. ``temperature``, ``max_tokens``).

        Returns:
            Parsed JSON response dict on success.
            ``{"error": ..., "ok": False}`` on network or HTTP errors.
        """
        try:
            response = requests.post(
                f"{self.base_url}/v1/completions",
                json={"prompt": prompt, "model": model, **kwargs},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "ok": False}

    def embed(self, text: str) -> Optional[list]:
        """
        Retrieve a dense vector embedding for the given text.

        Uses the ``text-embedding-004`` Gemini model (resolved dynamically by
        the proxy to the best available embedding model).

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector, or ``None`` on error.
        """
        try:
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                json={"text": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("embedding")
        except requests.RequestException as e:
            print(f"Error getting embeddings: {e}")
            return None

    def chat(self, messages: list, model: str = "gemini-pro") -> Dict[str, Any]:
        """
        Send a multi-turn chat request to the LLM proxy.

        The proxy maps the ``messages`` array to the Gemini or DeepSeek format
        depending on the model prefix.

        Args:
            messages: List of ``{"role": ..., "content": ...}`` dicts.
            model:    Model name; resolved by the proxy.

        Returns:
            Parsed JSON response dict on success.
            ``{"error": ..., "ok": False}`` on network or HTTP errors.
        """
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat",
                json={"messages": messages, "model": model},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "ok": False}
