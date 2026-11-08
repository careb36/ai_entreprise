"""Choosing the model by the task, not only by the role.

A deterministic difficulty estimate is computed from what is known before a
task runs (criteria covered, modules touched, whether it is a rework); a
routing policy maps difficulty bands to models (or to the proxy's
``fast``/``powerful`` tiers), with the role defaults as the fallback. The
model actually served stays recorded on the run (``served_model``).

Nothing here asks a model: the estimate is a function of the plan, and the
policy is data a Manager writes. When no policy is configured the role
default is used, so nothing changes for existing deployments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import settings

logger = logging.getLogger("fabrique-task-worker")


def estimate_difficulty(
    item: Any,
    spec: dict[str, Any] | None = None,
    arch: dict[str, Any] | None = None,
    rework: bool = False,
) -> int:
    """A 1..5 difficulty estimate for *item*.

    Criteria covered, modules the architecture maps to those criteria, and
    rework history each add weight. The result is clamped to 1..5.
    """
    covers = (
        item.get("covers") if isinstance(item, dict) else getattr(item, "covers", None)
    )
    covers = covers if isinstance(covers, list) else []
    score = 1 + min(len(covers), 3)
    if arch and isinstance(arch.get("modules"), list):
        covered_reqs = {str(c).rsplit(".", 1)[0] for c in covers}
        touched = 0
        for mod in arch["modules"]:
            mod_cov = {str(c) for c in (mod.get("covers") or [])}
            if mod_cov & covered_reqs:
                touched += 1
        score += min(touched, 2)
    if rework:
        score += 1
    return max(1, min(5, score))


def _policy() -> dict[str, Any]:
    raw = (getattr(settings, "AGENT_MODEL_POLICY", "") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def model_for(
    role: str, difficulty: int, policy: dict[str, Any] | None = None
) -> str | None:
    """The model id a *role* should use at *difficulty*, or None to fall back.

    *policy* maps ``{"bands": {"1-2": "fast", "3-5": "powerful"}}`` or
    ``{"bands": {"1-2": "<model id>"}}``. Band keys are ``"lo-hi"``.
    """
    pol = policy if policy is not None else _policy()
    bands = pol.get("bands") if isinstance(pol.get("bands"), dict) else {}
    _TIERS = {"fast", "balanced", "code", "strategic", "powerful", "preview"}
    for band, model in bands.items():
        try:
            lo, hi = band.split("-", 1)
            if int(lo) <= difficulty <= int(hi):
                value = str(model).strip() if model else ""
                if not value or value.lower() in _TIERS:
                    return None
                return value
        except (ValueError, AttributeError):
            continue
    return None


def company_model(session: Any, company_id: int | None, role: str | None) -> str:
    """The company's model for *role*, or its default (``company_models``); ``""``.

    The task worker puts it where the agent's own model is empty, so a choice
    the company made is never re-routed by difficulty.
    """
    from services import company_models

    return company_models.model_for(session, company_id, role) or ""


def resolve_agent_model(
    agent: dict[str, Any],
    default_role: str = "Dev",
    item: dict[str, Any] | None = None,
    rework: bool = False,
) -> str:
    from llm.model_factory import default_model_for_role

    configured = (agent.get("model") or "").strip()
    if configured:
        return configured
    role = agent.get("role") or default_role
    base = default_model_for_role(role)
    if item is None:
        return base
    difficulty = estimate_difficulty(item, rework=rework)
    routed = model_for(role, difficulty)
    if routed:
        return routed
    return base
