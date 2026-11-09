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

The candidates are the models of the company's enabled, validated connections
that the provider listed and the catalogue describes (models.dev:
``tool_call``, ``reasoning``, ``context``, ``cost_input``): the catalogue says
what a model can do, the provider says whether it serves it. A connection not
validated, or whose validation failed, suggests nothing, and one that listed
no model suggests nothing either. Each suggestion is the connection's model reference
(``conn:<id>:<model>``), so choosing it routes through the company's key.

Once the company has run a candidate on enough of its tasks in a role
(``model_record.MIN_TASKS``, more done than not), its own record decides that
role instead: the least rework per task, then the most done
(``services/model_record.py``, counted from the boards of completed sprints).
The suggestion says which: the rule, or the record with its numbers.
"""

from __future__ import annotations

from typing import Any

from services import model_catalog, model_record, provider_connections

DECIDING_ROLES = ("PO", "Architect", "ScrumMaster", "SecOps", "Designer")
# The specialised coding roles are suggested as the Developer is.
CODE_ROLES = (
    "Developer",
    "BackendDeveloper",
    "FrontendDeveloper",
    "DevOps",
    "DataEngineer",
    "QA",
    "Reviewer",
)
WRITING_ROLES = ("TechWriter",)


def candidates(rows: list[Any]) -> list[dict[str, Any]]:
    """The models the rule may suggest, each with its connection's reference."""
    out = []
    for row in rows:
        if row.validation_ok is not True:
            continue
        for model in model_catalog.connection_models(row)["models"]:
            if not model.get("listed") or not model.get("in_catalog"):
                continue
            if not model.get("tool_call"):
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


def _pick(
    models: list[dict[str, Any]],
    key,
    reason: str,
    role: str,
    record: dict[tuple[str, str], dict[str, int]],
) -> dict[str, Any] | None:
    if not models:
        return None
    # The company's own record decides once it has one: the least rework per
    # task, then the most done; the catalogue rule breaks the remaining ties.
    proven = [m for m in models if model_record.measured(record.get((role, m["id"])))]

    def track(m: dict[str, Any]) -> tuple[float, float]:
        row = record[(role, m["id"])]
        return row["rework"] / row["tasks"], -row["done"] / row["tasks"]

    if proven:
        best = min(proven, key=lambda m: (*track(m), *key(m), m["ref"]))
        reason = model_record.describe(role, record[(role, best["id"])])
    else:
        best = min(models, key=lambda m: (*key(m), m["ref"]))
    return {
        "model": best["ref"],
        "name": best.get("name") or best["id"],
        "connection": best["connection"],
        "reason": reason,
        "measured": record.get((role, best["id"])),
    }


def suggest(
    models: list[dict[str, Any]],
    record: dict[tuple[str, str], dict[str, int]] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """``{role: suggestion or None}`` for every role a company can set.

    *record* is ``model_record.record``: how each model did on the company's
    tasks per role. Without one, the catalogue rule alone.
    """
    record = record or {}
    reasoning = [m for m in models if m.get("reasoning")]
    rules = {
        **dict.fromkeys(
            DECIDING_ROLES,
            (
                reasoning,
                lambda m: (-_context(m), _cost(m)),
                "reasons and calls tools; largest context, then cheapest",
            ),
        ),
        **dict.fromkeys(
            CODE_ROLES,
            (
                models,
                lambda m: (-_context(m), _cost(m)),
                "calls tools; largest context, then cheapest",
            ),
        ),
        **dict.fromkeys(
            WRITING_ROLES,
            (
                models,
                lambda m: (_cost(m), -_context(m)),
                "the cheapest that calls tools",
            ),
        ),
    }
    return {
        role: _pick(pool, key, reason, role, record)
        for role, (pool, key, reason) in rules.items()
    }
