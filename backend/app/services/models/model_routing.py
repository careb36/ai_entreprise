"""Choosing the model by the task, not only by the role (issue #319).

A deterministic difficulty estimate is computed from what is known before a
task runs (``services/models/difficulty.py``: criteria covered, modules and
files touched, dependencies, the attempt and its rework history); a routing
policy (``services/models/routing_policy.py``: the company's, else the
deployment's ``AGENT_MODEL_POLICY``) maps difficulty bands to models (or to
the proxy's ``fast``/``powerful`` tiers), with the role defaults as the
fallback. The model actually served stays recorded on the run
(``served_model``); ``route`` says what was requested and why.

Nothing here asks a model: the estimate is a function of the plan, and the
policy is data a person writes. When no policy is configured the role
default is used, so nothing changes for existing deployments.

This module is the context's interface for routing (ADR 0018): the task
worker gets a task's estimate from its plan and board (``estimate_task``) and
the policy its company runs under (``effective_policy``) here, and passes both
to ``route``; ``difficulty`` and ``routing_policy`` stay internal.
"""

from __future__ import annotations

import logging
from typing import Any

from services.models import difficulty, routing_policy

logger = logging.getLogger("fabrique-task-worker")

# Why a model was requested (``routing.reason``).
REASON_CONFIGURED = "configured"  # an agent's or the company's choice: never re-routed
REASON_NO_TASK = "no_task"
REASON_NO_POLICY = "no_policy"
REASON_NO_BAND = "no_band"  # the policy has no band holding the score
REASON_BAND = "band"
REASON_TIER = "tier_role_default"  # a tier name: the proxy serves no such model id
REASON_EDITOR = "editor_plan"


def estimate_difficulty(
    item: Any,
    spec: dict[str, Any] | None = None,
    arch: dict[str, Any] | None = None,
    rework: bool = False,
) -> int:
    """A 1..5 difficulty estimate for *item* (``difficulty.estimate``'s score).

    Criteria covered, modules the architecture maps to those criteria, the
    tasks it waits for and rework history each add weight.
    """
    return difficulty.estimate_item(item, arch, rework=rework)["score"]


def _deliveries(session: Any, task_id: int) -> int:
    from models import TaskEvent
    from sqlmodel import func, select

    return int(
        session.exec(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type == "delivered")
        ).one()
    )


def estimate_task(
    session: Any,
    row: Any,
    architecture: dict[str, Any] | None,
    *,
    rework: bool,
) -> dict[str, Any]:
    """The difficulty of *row*'s next attempt, from its plan and its board.

    *architecture* is the approved architecture's content for the task's
    specification (the caller reads it; ``None`` without one). The attempt is
    the task's deliveries so far plus one; the rework history is the
    ``NEEDS WORK:`` requests and a person's send-backs on its board.
    """
    from services.tasks import board

    comments = board.comment_list(row.comments)
    return difficulty.estimate_item(
        row,
        architecture,
        attempt=_deliveries(session, row.id) + 1,
        rework=rework,
        rework_requests=board.rework_request_count(row.comments),
        send_backs=sum(1 for c in comments if board.is_send_back(c)),
    )


def effective_policy(
    session: Any, company_id: int | None
) -> tuple[dict[str, Any], str | None]:
    """The routing policy a company's tasks run under and where it came from
    (``routing_policy.effective``): ``(policy, "company")``,
    ``(policy, "deployment")`` or ``({}, None)``."""
    return routing_policy.effective(session, company_id)


def _policy() -> dict[str, Any]:
    return routing_policy.deployment_policy()


def current_policy() -> dict[str, Any]:
    """The deployment's ``AGENT_MODEL_POLICY``, parsed; ``{}`` when none or unreadable."""
    return _policy()


def _bands(policy: dict[str, Any] | None) -> list[tuple[int, int, str, str]]:
    """The policy's readable bands as ``(lo, hi, key, model)``, lowest first.

    A key is a difficulty band (``low``, ``medium``, ``high``) or a range of
    scores (``"1-2"``); an unreadable one is left out.
    """
    pol = policy if policy is not None else _policy()
    bands = pol.get("bands") if isinstance(pol.get("bands"), dict) else {}
    out = []
    for band, model in bands.items():
        scores = routing_policy.band_range(band)
        if scores is not None:
            out.append((*scores, str(band), str(model).strip() if model else ""))
    return sorted(out)


def _concrete(value: str) -> str | None:
    """A model id, never a proxy tier name (those fall back to the role default)."""
    return None if not value or routing_policy.is_tier(value) else value


def _band_entry(
    score: int, policy: dict[str, Any] | None
) -> tuple[int, int, str, str] | None:
    return next((b for b in _bands(policy) if b[0] <= score <= b[1]), None)


def model_for(
    role: str, difficulty: int, policy: dict[str, Any] | None = None
) -> str | None:
    """The model id a *role* should use at *difficulty*, or None to fall back.

    *policy* maps ``{"bands": {"low": "fast", "high": "<model id>"}}`` or
    ``{"bands": {"1-2": "<model id>"}}``.
    """
    entry = _band_entry(difficulty, policy)
    return _concrete(entry[3]) if entry else None


def band_of(difficulty: int, policy: dict[str, Any] | None = None) -> str | None:
    """The policy's band key holding *difficulty* (``"3-5"``, ``"high"``), or None."""
    entry = _band_entry(difficulty, policy)
    return entry[2] if entry else None


def cheapest_model(policy: dict[str, Any] | None = None) -> str | None:
    """The model of the policy's lowest band that names one: the editor's."""
    return next((m for *_r, m in _bands(policy) if _concrete(m)), None)


def company_model(session: Any, company_id: int | None, role: str | None) -> str:
    """The company's model for *role*, or its default (``company_models``); ``""``.

    The task worker puts it where the agent's own model is empty, so a choice
    the company made is never re-routed by difficulty.
    """
    from services.models import company_models

    return company_models.model_for(session, company_id, role) or ""


def route(
    agent: dict[str, Any],
    default_role: str = "Dev",
    item: dict[str, Any] | None = None,
    rework: bool = False,
    editor: bool = False,
    *,
    policy: dict[str, Any] | None = None,
    source: str | None = None,
    estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The model to request for *agent* on *item*, and why.

    Returns ``{"model", "difficulty", "routing"}``: ``difficulty`` is the
    estimate (*estimate*, else computed from *item*; ``None`` without a
    task), ``routing`` is ``{policy, source, band, requested, chosen,
    reason}``: the policy's label and where it came from, the band key that
    held the score, the value the band names (a model or a tier), the model
    requested and why. *policy* ``None`` reads the deployment's.
    The request is never the observation: the run records the served model.
    """
    from services.models.model_factory import default_model_for_role

    if policy is None:
        policy = _policy()
        source = routing_policy.DEPLOYMENT if policy else None
    routing: dict[str, Any] = {
        "policy": policy.get("label") if policy else None,
        "source": source if policy else None,
        "band": None,
        "requested": None,
        "chosen": None,
        "reason": REASON_NO_POLICY,
    }

    def done(model: str, reason: str, est: dict[str, Any] | None) -> dict[str, Any]:
        routing.update(chosen=model, reason=reason)
        return {"model": model, "difficulty": est, "routing": routing}

    if item is not None and estimate is None:
        estimate = difficulty.estimate_item(item, rework=rework)
    configured = (agent.get("model") or "").strip()
    if configured:
        return done(configured, REASON_CONFIGURED, estimate)
    base = default_model_for_role(agent.get("role") or default_role)
    if item is None:
        return done(base, REASON_NO_TASK, None)
    editor_model = cheapest_model(policy) if editor else None
    if editor_model:
        return done(editor_model, REASON_EDITOR, estimate)
    if not _bands(policy):
        return done(base, REASON_NO_POLICY, estimate)
    entry = _band_entry(estimate["score"], policy)
    if entry is None:
        return done(base, REASON_NO_BAND, estimate)
    routing.update(band=entry[2], requested=entry[3] or None)
    routed = _concrete(entry[3])
    if routed:
        return done(routed, REASON_BAND, estimate)
    return done(base, REASON_TIER, estimate)


def resolve_agent_model(
    agent: dict[str, Any],
    default_role: str = "Dev",
    item: dict[str, Any] | None = None,
    rework: bool = False,
    editor: bool = False,
) -> str:
    """The agent's model, else the company's, else routed by difficulty.

    *editor*: the task carries an Architect's edit plan to apply
    (``services/loop/edit_plan.py``), so the policy's cheapest band model runs it.
    A model a person chose is never re-routed.
    """
    return route(agent, default_role, item, rework, editor)["model"]
