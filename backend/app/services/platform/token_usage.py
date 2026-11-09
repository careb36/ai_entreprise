"""What an LLM turn's audit row keeps of the call's usage, beyond its total.

``audit_log.cost_tokens`` holds the total the provider reported (0 when it
reported none) and ``details.tokens`` says how that count is known
(``orchestration_metrics.usage_status``). Pricing a turn needs more: the
input and output tokens apart, since a model's price differs for each, and
the provider that served it, since one model id can be listed by several.
``usage_fields`` returns those keys for ``details``:

- ``input_tokens`` / ``output_tokens``: the provider's own prompt and
  completion counts, only when the usage is ``reported`` and both are counts;
- ``estimated_tokens``: the proxy's characters/4 estimate, only when the usage
  is ``estimated``. It is kept apart and never priced (``run_cost``);
- ``provider``: the proxy's ``provider`` (``deepseek``, ``gemini``,
  ``connection:<name>``...), when it said.

Pure: no database, no I/O.
"""

from __future__ import annotations

from typing import Any


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def usage_fields(llm_result: dict[str, Any] | None) -> dict[str, Any]:
    """The ``details`` keys a turn's cost is computed from (see the module)."""
    if not isinstance(llm_result, dict):
        return {}
    fields: dict[str, Any] = {}
    provider = llm_result.get("provider")
    if isinstance(provider, str) and provider.strip():
        fields["provider"] = provider.strip()
    if llm_result.get("usage_status") == "estimated":
        estimate = _count(llm_result.get("usage_estimate"))
        if estimate is not None:
            fields["estimated_tokens"] = estimate
        return fields
    usage = llm_result.get("usage")
    if isinstance(usage, dict):
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        if prompt is not None and completion is not None:
            fields["input_tokens"] = prompt
            fields["output_tokens"] = completion
    return fields
