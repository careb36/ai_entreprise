"""The model La Fabrique suggests for each role (issue #356, step 6).

A deterministic rule anyone can read, never a model's opinion, and never
applied on its own: the Models screen shows it beside the choice a Manager
makes.

- The roles that decide (PO, Architect, ScrumMaster, SecOps): a model that
  reasons and calls tools, the largest context first, then the cheapest.
- Developer, QA and the Reviewer: a model that calls tools (the Developer's
  workspace tools and MCP need them), the largest context first, then the
  cheapest.
- TechWriter: the cheapest model that calls tools.

The candidates are the models of the company's enabled connections that the
catalogue describes (models.dev: ``tool_call``, ``reasoning``, ``context``,
``cost_input``). When a connection was validated, only the ids its provider
listed count: the catalogue says what a model can do, the provider says
whether it serves it. Each suggestion is the connection's model reference
(``conn:<id>:<model>``), so choosing it routes through the company's key.
"""

from __future__ import annotations

from typing import Any

from services import model_catalog, provider_connections

DECIDING_ROLES = ("PO", "Architect", "ScrumMaster", "SecOps")
CODE_ROLES = ("Developer", "QA", "Reviewer")
WRITING_ROLES = ("TechWriter",)


def candidates(rows: list[Any]) -> list[dict[str, Any]]:
    """The models the rule may suggest, each with its connection's reference."""
    out = []
    for row in rows:
        entry = model_catalog.connection_models(row)
        validated = bool(row.validation_ok) and bool(row.listed_models)
        for model in entry["models"]:
            if not model.get("in_catalog") or not model.get("tool_call"):
                continue
            if validated and not model.get("listed"):
                continue
            out.append(
                {
                    **model,
                    "ref": provider_connections.model_ref(row.id, model["id"]),
                    "connection": row.name,
                }
            )
    return out


def _context(model: dict[str, Any]) -> int:
    return int(model.get("context") or 0)


def _cost(model: dict[str, Any]) -> float:
    # A model without a price sorts after the priced ones: unknown is not free.
    value = model.get("cost_input")
    return float(value) if value is not None else float("inf")


def _pick(models: list[dict[str, Any]], key, reason: str) -> dict[str, Any] | None:
    if not models:
        return None
    best = min(models, key=lambda m: (*key(m), m["ref"]))
    return {
        "model": best["ref"],
        "name": best.get("name") or best["id"],
        "connection": best["connection"],
        "reason": reason,
    }


def suggest(models: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """``{role: suggestion or None}`` for every role a company can set."""
    reasoning = [m for m in models if m.get("reasoning")]
    deciding = _pick(
        reasoning,
        lambda m: (-_context(m), _cost(m)),
        "reasons and calls tools; largest context, then cheapest",
    )
    coding = _pick(
        models,
        lambda m: (-_context(m), _cost(m)),
        "calls tools; largest context, then cheapest",
    )
    writing = _pick(
        models,
        lambda m: (_cost(m), -_context(m)),
        "the cheapest that calls tools",
    )
    out: dict[str, dict[str, Any] | None] = {}
    for role in DECIDING_ROLES:
        out[role] = deciding
    for role in CODE_ROLES:
        out[role] = coding
    for role in WRITING_ROLES:
        out[role] = writing
    return out
