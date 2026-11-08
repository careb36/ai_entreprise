"""Model suggestions for the company's roles (issue #356, step 6).

A deterministic rule, never a model: which model suits each role best based on
its declared capabilities. The suggestion is offered, never applied.
"""

from __future__ import annotations

from typing import Any

_DECIDING_ROLES = ("PO", "Architect", "ScrumMaster", "SecOps")
_CODE_ROLES = ("Developer", "QA")
_DOC_ROLES = ("TechWriter",)


def _tool_call(m: dict[str, Any]) -> bool:
    if "tool_call" in m:
        return bool(m.get("tool_call"))
    caps = m.get("capabilities") or {}
    if isinstance(caps, dict):
        return bool(caps.get("tool_call") or caps.get("tool_calls"))
    return False


def _reasoning(m: dict[str, Any]) -> bool:
    if "reasoning" in m:
        return bool(m.get("reasoning"))
    caps = m.get("capabilities") or {}
    if isinstance(caps, dict):
        return bool(caps.get("reasoning"))
    return False


def _context(m: dict[str, Any]) -> int:
    if "context" in m:
        return int(m.get("context") or 0)
    limits = m.get("limit") or m.get("limits") or {}
    if isinstance(limits, dict):
        return int(limits.get("context") or limits.get("context_window") or 0)
    return 0


def _cost(m: dict[str, Any]) -> float:
    if "cost_input" in m:
        return float(m.get("cost_input") or 0.0)
    cost = m.get("cost") or {}
    if isinstance(cost, dict):
        return float(cost.get("input") or 0.0)
    return 0.0


def suggest(models: list[dict[str, Any]]) -> dict[str, str | None]:
    """Return {role: model_id} suggestions, or None when nothing fits."""
    usable = [m for m in models if _tool_call(m) and m.get("id")]
    if not usable:
        return {}

    def best_for_deciding() -> str | None:
        candidates = [m for m in usable if _reasoning(m)]
        if not candidates:
            return None
        candidates = sorted(candidates, key=lambda m: (-_context(m), _cost(m)))
        return candidates[0]["id"] if candidates else None

    def best_for_code() -> str | None:
        candidates = sorted(usable, key=lambda m: (-_context(m), _cost(m)))
        return candidates[0]["id"] if candidates else None

    def cheapest() -> str | None:
        candidates = sorted(usable, key=lambda m: (_cost(m), -_context(m)))
        return candidates[0]["id"] if candidates else None

    result: dict[str, str | None] = {}
    deciding = best_for_deciding()
    code = best_for_code()
    cheap = cheapest()
    for role in _DECIDING_ROLES:
        result[role] = deciding
    for role in _CODE_ROLES:
        result[role] = code
    for role in _DOC_ROLES:
        result[role] = cheap
    return result
