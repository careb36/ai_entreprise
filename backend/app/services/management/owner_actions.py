"""The catalogue of owner actions and their runs (``docs/design/owner-actions.md``).

An owner action is a step only the company's Owner takes, written as code:
a name, typed parameters checked by a schema (never a free command, path or
argument), what it does, what it costs and what it records. Nothing outside
``CATALOGUE`` can run. This module holds the catalogue, the confirmation
digest, and the run's record: start, cancel request and finish, each an
audit row in the company's hash chain attributed to the person, and the
run's observations (``models/owner_action.py``). The job itself is
``owner_action_runner.py``.

Rules kept here:

- One run of an action at a time in the deployment (``RunRefused`` 409).
- A start echoes the digest of what the confirmation showed (action,
  validated parameters, effect and cost); anything else is a 409.
- A run's ``observations`` hold what the platform created or measured; the
  runner writes them, never a producer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from models import OwnerActionRun, Sprint
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, select

from services.management import owner_git_actions as git
from services.platform import locks
from services.platform.agent_service import AgentService

SELF_HOST = "self_host_container_run"
RUNNING = "running"
FINISHED = ("succeeded", "failed", "cancelled")
LAST_RUNS = 5
# One folder name under the daemon's own root; never a path (O5).
CHECKOUT_NAME = r"^[a-z0-9][a-z0-9._-]{0,62}$"
CHECKOUT_SECONDS = 600  # the daemon's copy, at most


class RunRefused(Exception):
    """A start or a cancel the catalogue refuses; ``status`` follows HTTP."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


class SelfHostParams(BaseModel):
    """``self_host_container_run``'s parameters; nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")

    checkout_name: str = Field(pattern=CHECKOUT_NAME, max_length=63)
    scope: Literal["S", "M"] = "S"
    timeout_seconds: int = Field(default=3600, ge=60, le=21600)
    token_budget: int = Field(default=2_000_000, ge=10_000, le=10_000_000)
    urgent: bool = False


def _self_host_effect(p: SelfHostParams) -> list[dict[str, Any]]:
    """What the run does, as the dialog shows it: i18n keys and their values."""
    effect = [
        {"key": "copy", "values": {"name": p.checkout_name}},
        {"key": "company", "values": {}},
        {"key": "sprint", "values": {"scope": p.scope}},
        {"key": "wait", "values": {"minutes": p.timeout_seconds // 60}},
        {"key": "sandbox", "values": {}},
    ]
    if p.urgent:
        effect.append({"key": "urgent", "values": {}})
    return effect


def _self_host_cost(p: SelfHostParams) -> dict[str, Any]:
    return {
        "models": ["PO", "Architect", "Developer", "QA", "ScrumMaster", "TechWriter"],
        "token_budget": p.token_budget,
        "max_seconds": p.timeout_seconds + CHECKOUT_SECONDS,
        "budget_checked_between_calls": True,
    }


@dataclass(frozen=True)
class Action:
    """A catalogue entry. With ``facts`` (async ``(ctx, params) -> (params, facts)``),
    the preview observes before it shows: effect, cost and ``blockers`` then take
    ``(params, facts)`` and the digest covers the facts. ``step_up(facts)`` says a
    start needs a fresh re-authentication (``step_up.py``)."""

    name: str
    issue: int
    params: type[BaseModel]
    effect: Any
    cost: Any
    records: tuple[str, ...]
    conflicts: tuple[str, ...] = field(default_factory=tuple)
    facts: Any = None
    blockers: Any = None
    step_up: Any = None
    no_active_sprint: bool = False


CATALOGUE: dict[str, Action] = {
    SELF_HOST: Action(
        name=SELF_HOST,
        issue=701,
        params=SelfHostParams,
        effect=_self_host_effect,
        cost=_self_host_cost,
        records=(
            "started_at",
            "finished_at",
            "checkout",
            "company_id",
            "sprint_id",
            "agent_run_ids",
            "sandbox",
            "tokens_observed",
            "exit_status",
        ),
        conflicts=(git.LAND,),
    ),
    git.LAND: Action(
        name=git.LAND,
        issue=701,
        params=git.LandParams,
        effect=git.land_effect,
        cost=git.land_cost,
        records=(
            "branch",
            "land_owner",
            "verification_record",
            "check_summary",
            "main_before",
            "main_after",
            "exit_code",
        ),
        conflicts=(SELF_HOST, git.PUSH),
        facts=git.land_facts,
        blockers=git.land_blockers,
        step_up=lambda facts: bool(facts.get("needs_owner")),
        no_active_sprint=True,
    ),
    git.PUSH: Action(
        name=git.PUSH,
        issue=701,
        params=git.PushParams,
        effect=git.push_effect,
        cost=git.push_cost,
        records=("pushed_range", "notes_ref", "remote_url"),
        conflicts=(git.LAND,),
        facts=git.push_facts,
        blockers=git.push_blockers,
    ),
    git.CLOSE: Action(
        name=git.CLOSE,
        issue=701,
        params=git.CloseParams,
        effect=git.close_effect,
        cost=git.close_cost,
        records=(
            "issue_url",
            "comment_url",
            "comment_sent",
            "closed",
            "fix_commit",
            "fix_on_origin",
            "override_not_on_origin",
        ),
        facts=git.close_facts,
        blockers=git.close_blockers,
    ),
}


# A blocked task or an issue step whose text names one of these is a step the
# owner can run from the app (the inbox shows "Run from the app"). A plain
# text match: it only offers the dialog, it never starts anything.
MENTIONS: dict[str, tuple[str, ...]] = {
    SELF_HOST: ("--container-mode", "self_host.py", "#701"),
    git.PUSH: ("land.sh --push", "push main to github", "push to github"),
    git.LAND: ("scripts/land.sh", "land.sh "),
    git.CLOSE: ("close the issue", "close issue #"),
}


def matching(text: str | None) -> str | None:
    """The catalogue action *text* mentions, or ``None``."""
    lowered = (text or "").lower()
    for name, words in MENTIONS.items():
        if any(w.lower() in lowered for w in words):
            return name
    return None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def action(name: str) -> Action:
    found = CATALOGUE.get(name)
    if found is None:
        raise RunRefused(404, f"No owner action named {name!r}")
    return found


def validate(spec: Action, raw: dict[str, Any]) -> BaseModel:
    try:
        return spec.params.model_validate(raw)
    except ValidationError as exc:
        raise RunRefused(
            422, json.loads(exc.json(include_url=False, include_context=False))
        ) from exc


def preview(
    spec: Action, params: BaseModel, facts: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The confirmation: effect, cost and the digest a start must echo (O2).
    An action with facts shows them, and the digest covers them."""
    args = (params,) if spec.facts is None else (params, facts or {})
    body = {
        "action": spec.name,
        "params": params.model_dump(),
        "effect": spec.effect(*args),
        "cost": spec.cost(*args),
    }
    if spec.facts is not None:
        body["facts"] = facts or {}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {**body, "confirmation": hashlib.sha256(canonical.encode()).hexdigest()}


async def observe_and_preview(
    spec: Action, params: BaseModel, ctx: Any
) -> tuple[BaseModel, dict[str, Any]]:
    """The facts read now, and the preview with its ``blockers`` (not digested:
    a blocker refuses the start, it is not something the Owner confirms)."""
    facts = None
    if spec.facts is not None:
        try:
            params, facts = await spec.facts(ctx, params)
        except git.Refused as exc:
            raise RunRefused(exc.status, exc.detail) from exc
    shown = preview(spec, params, facts)
    shown["blockers"] = spec.blockers(params, facts) if spec.blockers else []
    shown["step_up"] = bool(spec.step_up and spec.step_up(facts or {}))
    return params, shown


def active_sprints(session: Session) -> list[int]:
    """The sprints agents may be working in now, in any company (a land refuses)."""
    return list(
        session.exec(
            select(Sprint.id).where(
                Sprint.status == "active",
                Sprint.archived_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
    )


def describe(spec: Action) -> dict[str, Any]:
    schema = spec.params.model_json_schema()
    return {
        "name": spec.name,
        "issue": spec.issue,
        "observes": spec.facts is not None,
        "params": schema.get("properties", {}),
        "required": schema.get("required", []),
        "records": list(spec.records),
    }


def serialize(run: OwnerActionRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "action": run.action,
        "status": run.status,
        "params": run.params,
        "started_by_user_id": run.started_by_user_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "cancel_requested_at": (
            run.cancel_requested_at.isoformat() if run.cancel_requested_at else None
        ),
        "exit_status": run.exit_status,
        "reason": run.reason,
        "observations": run.observations or {},
    }


def last_runs(session: Session, company_id: int, name: str) -> list[OwnerActionRun]:
    return list(
        session.exec(
            select(OwnerActionRun)
            .where(
                OwnerActionRun.company_id == company_id, OwnerActionRun.action == name
            )
            .order_by(OwnerActionRun.id.desc())  # type: ignore[union-attr]
            .limit(LAST_RUNS)
        ).all()
    )


def running(session: Session, names: tuple[str, ...]) -> OwnerActionRun | None:
    """The running run of any of *names*, in any company."""
    return session.exec(
        select(OwnerActionRun).where(
            OwnerActionRun.action.in_(names),  # type: ignore[attr-defined]
            OwnerActionRun.status == RUNNING,
        )
    ).first()


def _audit(
    session: Session, run: OwnerActionRun, user_id: int, kind: str, result: Any
) -> None:
    AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"owner_action:{run.id}",
        agent_id=f"human:{user_id}",
        action=f"owner_action_{kind}",
        details={
            "run_id": run.id,
            "action": run.action,
            "params": run.params,
            "user_id": user_id,
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )


def start(
    session: Session,
    company_id: int,
    user_id: int,
    spec: Action,
    params: BaseModel,
    confirmation: str,
    facts: dict[str, Any] | None = None,
    audit_extra: dict[str, Any] | None = None,
) -> OwnerActionRun:
    """Record a run's start (audited), or refuse it. The facts confirmed are
    kept as the run's ``confirmed`` observation."""
    if confirmation != preview(spec, params, facts)["confirmation"]:
        raise RunRefused(
            409, "The confirmation does not match these parameters: preview again"
        )
    locks.in_transaction(session, "owner_action", spec.name)
    busy = running(session, (spec.name, *spec.conflicts))
    if busy is not None:
        raise RunRefused(
            409,
            {
                "message": f"{busy.action} run {busy.id} is still running",
                "running_run_id": busy.id,
            },
        )
    run = OwnerActionRun(
        company_id=company_id,
        action=spec.name,
        params=params.model_dump(),
        started_by_user_id=user_id,
        observations={} if facts is None else {"confirmed": facts},
    )
    session.add(run)
    session.flush()
    _audit(session, run, user_id, "started", {"status": RUNNING, **(audit_extra or {})})
    session.commit()
    session.refresh(run)
    return run


def request_cancel(
    session: Session, run: OwnerActionRun, user_id: int
) -> OwnerActionRun:
    """Ask a running run to stop (audited); the runner stops at its next call."""
    if run.status != RUNNING:
        raise RunRefused(409, f"Run {run.id} is {run.status}, not running")
    if run.cancel_requested_at is None:
        run.cancel_requested_at = _now()
        run.cancel_requested_by_user_id = user_id
        session.add(run)
        _audit(session, run, user_id, "cancel_requested", {"status": RUNNING})
        session.commit()
        session.refresh(run)
    return run


def observe(session: Session, run_id: int, observations: dict[str, Any]) -> None:
    """Store what the runner observed so far (the page shows it live)."""
    run = session.get(OwnerActionRun, run_id)
    if run is None:
        return
    run.observations = {**(run.observations or {}), **observations}
    session.add(run)
    session.commit()


def finish(
    session: Session,
    run_id: int,
    status: str,
    exit_status: int | None,
    reason: str | None,
    observations: dict[str, Any],
) -> OwnerActionRun | None:
    """Close a run (audited, attributed to the person who started it)."""
    run = session.get(OwnerActionRun, run_id)
    if run is None or run.status != RUNNING:
        return run
    run.status = status if status in FINISHED else "failed"
    run.finished_at = _now()
    run.exit_status = exit_status
    run.reason = reason
    run.observations = {
        **(run.observations or {}),
        **observations,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
    }
    session.add(run)
    _audit(
        session,
        run,
        run.started_by_user_id,
        "finished",
        {"status": run.status, "exit_status": exit_status, "reason": reason},
    )
    session.commit()
    session.refresh(run)
    return run


def cancel_requested(session: Session, run_id: int) -> bool:
    run = session.get(OwnerActionRun, run_id)
    if run is not None:
        session.refresh(run)
    return run is None or run.cancel_requested_at is not None
