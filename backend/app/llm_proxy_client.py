import os
import requests
from typing import Optional, Dict, Any


class LLMProxyClient:
    """Client for LLM proxy service that routes to Gemini."""
    
    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url
        self.timeout = 30
    
    def complete(self, prompt: str, model: str = "gemini-pro", **kwargs) -> Dict[str, Any]:
        """Send completion request to LLM proxy."""
        try:
            response = requests.post(
                f"{self.base_url}/v1/completions",
                json={
                    "prompt": prompt,
                    "model": model,
                    **kwargs
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "ok": False}
    
    def embed(self, text: str) -> Optional[list]:
        """Get embeddings for text."""
        try:
            response = requests.post(
                f"{self.base_url}/v1/embeddings",
                json={"text": text},
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get("embedding")
        except requests.RequestException as e:
            print(f"Error getting embeddings: {e}")
            return None
    
    def chat(self, messages: list, model: str = "gemini-pro") -> Dict[str, Any]:
        """Send chat request to LLM proxy."""
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat",
                json={
                    "messages": messages,
                    "model": model
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e), "ok": False}
