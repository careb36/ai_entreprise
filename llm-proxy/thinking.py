"""DeepSeek's thinking mode on the way out: always explicit, and DeepSeek's only.

DeepSeek runs a request that omits ``thinking`` in thinking mode
(https://api-docs.deepseek.com/api/create-chat-completion: "When ``thinking``
is omitted or ``null``, the mode follows ``reasoning_effort``; if
``reasoning_effort`` is also omitted, thinking is enabled"). The backend
chooses the mode per call (``backend/app/services/models/thinking.py``) and
sends ``thinking: {"type": "enabled" | "disabled"}``
(https://api-docs.deepseek.com/guides/thinking_mode). Here:

- a DeepSeek request keeps the caller's valid ``thinking``; one without it
  gets ``disabled``, so the provider's default never applies by omission;
- ``LLM_THINKING=on|off`` forces every DeepSeek request one way (``auto``,
  the default, keeps the caller's choice);
- every other provider gets the request without the field: Qwen, Nebius and
  a company's connection have their own (or none), and Gemini builds its own
  body;
- a structured call (``X-LLM-Purpose: structured``) whose thinking answer
  came back empty because the reasoning used the whole ``max_tokens``
  (``empty_content_reasoning_exhausted``) is retried once with thinking off.
"""

import os

from upstream import EMPTY_CONTENT_REASONING_EXHAUSTED

FIELD = "thinking"
ENABLED = "enabled"
DISABLED = "disabled"
STRUCTURED = "structured"

_ON = {"on", "true", "1", "yes", ENABLED}
_OFF = {"off", "false", "0", "no", DISABLED}


def override(env=None) -> str | None:
    """``ENABLED`` or ``DISABLED`` when ``LLM_THINKING`` forces one, else ``None``."""
    env = os.environ if env is None else env
    value = str(env.get("LLM_THINKING", "auto")).strip().lower()
    if value in _ON:
        return ENABLED
    if value in _OFF:
        return DISABLED
    return None


def mode_of(payload: dict) -> str | None:
    """The ``thinking`` type *payload* names, when it names a valid one."""
    field = payload.get(FIELD)
    mode = field.get("type") if isinstance(field, dict) else None
    return mode if mode in (ENABLED, DISABLED) else None


def prepare(provider: str, data: dict, env=None) -> dict:
    """The body to send *provider*: the thinking field explicit for DeepSeek, absent elsewhere."""
    out = dict(data)
    if provider != "deepseek":
        out.pop(FIELD, None)
        return out
    mode = override(env) or mode_of(out) or DISABLED
    out[FIELD] = {"type": mode}
    return out


def without_thinking(payload: dict) -> dict:
    """*payload* with thinking off: the structured call's one retry."""
    return {**payload, FIELD: {"type": DISABLED}}


def retry_without(provider: str, payload: dict, reason: str | None, structured: bool) -> bool:
    """Whether a failed call is retried once with thinking off.

    Only a structured DeepSeek call that ran with thinking on and came back
    empty because the reasoning used the whole budget: its answer is parsed,
    so the reasoning was never the point.
    """
    return (
        provider == "deepseek"
        and structured
        and reason == EMPTY_CONTENT_REASONING_EXHAUSTED
        and mode_of(payload) == ENABLED
    )
