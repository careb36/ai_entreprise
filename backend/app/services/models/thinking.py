"""Whether a model call reasons before it answers: one explicit decision per call.

DeepSeek's models think by default: a request that omits ``thinking`` (and
``reasoning_effort``) runs in thinking mode, writes its chain of thought to
``reasoning_content`` and counts it against the call's budget
(https://api-docs.deepseek.com/guides/thinking_mode,
https://api-docs.deepseek.com/api/create-chat-completion, read 2026-11-20:
``max_tokens`` defaults to 8K without thinking and 64K with it). A call capped
at a few thousand tokens can spend all of them reasoning and come back with
an empty answer, which is what the planner, the QA review and the engram
extraction got once the default team moved to DeepSeek (issues #739, #748).

So no call is left to the provider's default. Every call sends
``thinking: {"type": "enabled" | "disabled"}``, decided here:

- a **structured** call (its answer is parsed: JSON, a verdict word, a one
  token probe) runs with thinking **off**, whatever its role;
- a **free-form** call runs with thinking **on** for the roles whose answer
  is a plan, a design or a deliverable (``REASONING_ROLES``) and **off** for
  the rest; with thinking on, ``REASONING_RESERVE_TOKENS`` is added to the
  call's ``max_tokens`` so the reasoning cannot eat the answer's budget;
- ``LLM_THINKING`` (``auto``, the default; ``on``; ``off``) lets an operator
  force every call one way. Forcing ``on`` keeps the proxy's safety net: a
  structured call whose reasoning exhausted the budget is retried once with
  thinking off (``llm-proxy/providers.py``).

The proxy forwards the field to DeepSeek only; Gemini, Qwen, Nebius and a
company's own connection never see it.
"""

from __future__ import annotations

from typing import Any

from config import Settings, settings

ENABLED = "enabled"
DISABLED = "disabled"

# The roles whose free-form answer is worth reasoning for: the plans, the
# designs and the deliverables. Their calls still go off when structured.
REASONING_ROLES = frozenset(
    {
        "po",
        "architect",
        "secops",
        "developer",
        "backenddeveloper",
        "frontenddeveloper",
        "devops",
        "dataengineer",
        "designer",
    }
)
# The roles every call of which is parsed (the engram extraction reads JSON).
STRUCTURED_ROLES = frozenset({"engramprocessor"})

# Tokens added to a thinking call's ``max_tokens`` for the reasoning, so a
# role's answer budget stays its own. DeepSeek's own default leaves 56K of the
# 64K for it; 16K keeps a call within the 300 s upstream budget at the speeds
# observed, and an exhausted one comes back as a named error, never empty.
DEFAULT_REASONING_RESERVE_TOKENS: int = Settings.model_fields[
    "LLM_THINKING_RESERVE_TOKENS"
].default

_ON = {"on", "true", "1", "yes", ENABLED}
_OFF = {"off", "false", "0", "no", DISABLED}


def _key(role: str | None) -> str:
    return "".join(ch for ch in (role or "").lower() if ch not in " -_")


def override() -> str | None:
    """``ENABLED`` or ``DISABLED`` when ``LLM_THINKING`` forces one, else ``None``."""
    value = str(settings.LLM_THINKING or "auto").strip().lower()
    if value in _ON:
        return ENABLED
    if value in _OFF:
        return DISABLED
    return None


def reasoning_reserve() -> int:
    """``LLM_THINKING_RESERVE_TOKENS``, or the default when unset or not a positive count."""
    value = settings.LLM_THINKING_RESERVE_TOKENS
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DEFAULT_REASONING_RESERVE_TOKENS


def mode_for(role: str | None, *, structured: bool) -> str:
    """``ENABLED`` or ``DISABLED`` for one call of *role*."""
    forced = override()
    if forced:
        return forced
    if structured or _key(role) in STRUCTURED_ROLES:
        return DISABLED
    return ENABLED if _key(role) in REASONING_ROLES else DISABLED


def max_tokens_for(mode: str, max_tokens: int | None) -> int | None:
    """The cap to send: the answer's own, plus the reasoning reserve when thinking.

    ``None`` stays ``None``: the provider's own default (64K with thinking) applies.
    """
    if not max_tokens:
        return max_tokens
    return int(max_tokens) + (reasoning_reserve() if mode == ENABLED else 0)


def request_fields(
    role: str | None, *, structured: bool, max_tokens: int | None
) -> dict[str, Any]:
    """The fields a chat request carries for this policy: ``thinking`` and, when
    capped, ``max_tokens``."""
    mode = mode_for(role, structured=structured)
    fields: dict[str, Any] = {"thinking": {"type": mode}}
    capped = max_tokens_for(mode, max_tokens)
    if capped:
        fields["max_tokens"] = capped
    return fields
