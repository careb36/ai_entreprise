"""
AI Streaming Engine Re-export / Compatibility Layer.

Points to the canonical implementation at `backend.app.services.ai_streaming`
to maintain architectural consistency across existing and legacy package imports.
"""

from __future__ import annotations

from backend.app.services.ai_streaming import (
    AIStreamingEngine,
    StreamEvent,
    ai_streaming_engine,
)

__all__ = ["AIStreamingEngine", "StreamEvent", "ai_streaming_engine"]
