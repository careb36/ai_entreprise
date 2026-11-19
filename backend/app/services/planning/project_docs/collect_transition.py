"""Reading what the ITIL transition set reads into the snapshot (``collect``, issue #696).

One snapshot key, ``transition``: per release, oldest first. A release here
is a sprint integration that left the factory: published as one request
(``SprintIntegration.publication``, ``services/release/integration_publish``)
or merged locally into the base branch (``details["local_merge"]``,
``services/release/local_merge``). Every field is an observation the
platform made itself:

- ``tasks``: each done task as the integration recorded it (its state, the
  commit, the run and the verification as they stood then), with the
  criteria the task covers on the board (``BacklogItem.covers``);
- ``suite``: the integration's status and the daemon's test run (command,
  exit code, timeout, the number of failing tests), never an agent's report
  of a run;
- ``publications`` and ``merge``: how the release left, the daemon's commits,
  and the audit row the backend wrote (its time and chain hash);
- ``review``: the integration's latest decision by a person (``approve`` or
  ``request_changes``; else its latest review), with the commit and the
  ``diff_sha256`` the backend computed, the segregation-of-duties outcome
  and its audit row. The review's words are a claim and are not read;
- ``readings``: the Verifier's reading of each task's delivery recorded at
  integration, with the run's artifact digest;
- ``undone``: the ``run_delivery_undone`` audit rows for the release's runs
  (``services/release/delivery_undo``);
- ``hold_events``: the release's sprint's ``blocked``, ``risk_hold``,
  ``escalated`` and ``decided`` events, and the platform's carries out of it
  (actor ``system``), written at or before the release, each with the task's
  status after its newest event at that time (``TaskEvent.task_status``).

Who approved or merged is reduced to a code: the account's effective company
role now (``Owner``, ``Manager``, ``Viewer``), ``former`` (no longer a
member) or ``no_account`` (not a signed-in account: the Manager agent, an
API token, a deployment without sign-in). Names and addresses are never
read; nor is the request's address. Each part carries the sha256 the backend
computed over it (``digest``). Plain data only, bounded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import (
    AgentRun,
    AuditLog,
    BacklogItem,
    CompanyMember,
    IntegrationReview,
    SprintIntegration,
    TaskEvent,
    Verification,
)
from sqlmodel import Session, select

from services.planning.project_docs.refs import digest
from services.platform import teams as team_roles
from services.tasks import task_events

KEY = "transition"
LIMIT = 200
# ``services/release/delivery_undo.UNDO_ACTION``; planning may not import release.
UNDO_ACTION = "run_delivery_undone"
DECISIONS = ("approve", "request_changes")
HOLD_KINDS = ("blocked", "risk_hold", "escalated", "decided")
CARRY_KIND = "carried_over"
FORMER, NO_ACCOUNT = "former", "no_account"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value not in (None, "") else None


def moment(value: Any) -> str:
    """A stored time to the second, so a naive and a UTC-suffixed time compare."""
    return str(value or "")[:19]


def _hashed(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "sha256": digest(entry)}


def _released(row: SprintIntegration) -> bool:
    pub = row.publication if isinstance(row.publication, dict) else {}
    merge = (row.details or {}).get("local_merge") if row.details else None
    return bool(pub.get("commit")) or (
        isinstance(merge, dict) and bool(merge.get("commit"))
    )


def _audits(session: Session, company_id: int, ids: set[int]) -> dict[int, AuditLog]:
    if not ids:
        return {}
    rows = session.exec(
        select(AuditLog).where(AuditLog.id.in_(ids), AuditLog.company_id == company_id)
    ).all()
    return {a.id: a for a in rows}


def _roles(session: Session, company_id: int, user_ids: set[int]) -> dict[int, str]:
    """The effective company role of each account that is a member now."""
    if not user_ids:
        return {}
    own = dict(
        session.exec(
            select(CompanyMember.user_id, CompanyMember.human_role).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id.in_(user_ids),
            )
        ).all()
    )
    out = {}
    for user_id in user_ids:
        role = team_roles.effective_role(
            own.get(user_id), team_roles.team_roles(session, company_id, user_id)
        )
        if role:
            out[user_id] = role
    return out


def _by(user_id: Any, roles: dict[int, str]) -> str:
    return NO_ACCOUNT if _int(user_id) is None else roles.get(user_id, FORMER)


def _audit_part(audit: AuditLog | None) -> dict[str, Any]:
    return {
        "audit_log_id": audit.id if audit else None,
        "chain_hash": audit.chain_hash if audit else None,
        "at": _iso(audit.created_at) if audit else None,
    }


def _suite(row: SprintIntegration) -> dict[str, Any]:
    run = row.test_run if isinstance(row.test_run, dict) else {}
    failing = run.get("failing_tests")
    return _hashed(
        {
            "status": row.status,
            "command": _str(run.get("command"), 200),
            "exit_code": _int(run.get("exit_code")),
            "timed_out": bool(run.get("timed_out")),
            "failing": len(failing) if isinstance(failing, list) else None,
            "tested_commit": row.tested_commit,
        }
    )


def _tasks(row: SprintIntegration, covers: dict[int, list]) -> list[dict[str, Any]]:
    out = []
    for task in row.tasks or []:
        if not isinstance(task, dict) or _int(task.get("task_id")) is None:
            continue
        out.append(
            _hashed(
                {
                    "task_id": task["task_id"],
                    "title": _str(task.get("title"), 200),
                    "state": _str(task.get("state"), 30),
                    "commit": _str(
                        task.get("merge_commit") or task.get("source_commit"), 64
                    ),
                    "run_id": _int(task.get("run_id")),
                    "verification_id": _int(task.get("verification_id")),
                    "covers": [str(c)[:60] for c in covers.get(task["task_id"], [])][
                        :30
                    ],
                }
            )
        )
    return out[:LIMIT]


def _review(session, row, roles_of) -> tuple[dict[str, Any] | None, int]:
    reviews = session.exec(
        select(IntegrationReview)
        .where(
            IntegrationReview.company_id == row.company_id,
            IntegrationReview.integration_id == row.id,
        )
        .order_by(IntegrationReview.id)
    ).all()
    decided = [r for r in reviews if r.event in DECISIONS]
    chosen = (decided or reviews or [None])[-1]
    if chosen is None:
        return None, 0
    audit = _audits(session, row.company_id, {chosen.audit_log_id} - {None}).get(
        chosen.audit_log_id
    )
    return (
        _hashed(
            {
                "id": chosen.id,
                "event": chosen.event,
                "outcome": chosen.outcome,
                "commit": chosen.commit,
                "diff_sha256": chosen.diff_sha256,
                "files": chosen.files,
                "sod": _str(chosen.sod, 40),
                "by": _by(chosen.author_user_id, roles_of),
                "recorded_at": _iso(chosen.created_at),
                "on_commit": bool(row.commit) and chosen.commit == row.commit,
                **_audit_part(audit),
            }
        ),
        len(reviews),
    )


def _readings(session, company_id, tasks) -> list[dict[str, Any]]:
    ids = {t["verification_id"] for t in tasks if t.get("verification_id")}
    if not ids:
        return []
    rows = session.exec(
        select(Verification, AgentRun.artifact_digest)
        .join(AgentRun, AgentRun.id == Verification.agent_run_id)
        .where(Verification.company_id == company_id, Verification.id.in_(ids))
    ).all()
    task_of = {t["verification_id"]: t["task_id"] for t in tasks}
    out = []
    for verification, artifact in sorted(rows, key=lambda r: r[0].id):
        tier = next(
            (
                c.get("tier")
                for c in verification.checks or []
                if isinstance(c, dict) and c.get("name") == "risk_tier"
            ),
            None,
        )
        out.append(
            {
                "verification_id": verification.id,
                "run_id": verification.agent_run_id,
                "task_id": task_of.get(verification.id),
                "verdict": verification.verdict,
                "tier": _str(tier, 20),
                "artifact_sha256": artifact,
                "observed_at": _iso(verification.observed_at),
            }
        )
    return out


def _undone(session, company_id, tasks) -> list[dict[str, Any]]:
    task_of = {t["run_id"]: t["task_id"] for t in tasks if t.get("run_id")}
    if not task_of:
        return []
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == company_id, AuditLog.action == UNDO_ACTION)
        .order_by(AuditLog.id)
    ).all()
    out = []
    for audit in rows:
        details = audit.details if isinstance(audit.details, dict) else {}
        run_id = _int(details.get("agent_run_id"))
        if run_id in task_of:
            out.append(
                {"run_id": run_id, "task_id": task_of[run_id], **_audit_part(audit)}
            )
    return out[:LIMIT]


def _hold_events(session, row, until: str | None) -> list[dict[str, Any]]:
    events = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == row.company_id, TaskEvent.sprint_id == row.sprint_id
        )
        .order_by(TaskEvent.id)
    ).all()
    events = [
        e
        for e in events
        if until is None or moment(_iso(e.created_at)) <= moment(until)
    ]
    status_then: dict[Any, str | None] = {}
    for event in events:
        if event.task_status:
            status_then[event.task_id] = event.task_status
    out = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        carry = (
            event.event_type == CARRY_KIND
            and event.actor == task_events.SYSTEM
            and payload.get("direction") == "to"
        )
        if event.event_type not in HOLD_KINDS and not carry:
            continue
        out.append(
            {
                "id": event.id,
                "task_id": event.task_id,
                "kind": event.event_type,
                "actor": _str(event.actor, 40),
                "created_at": _iso(event.created_at),
                "chain_hash": event.chain_hash,
                "task_status_then": status_then.get(event.task_id),
            }
        )
    return out[-LIMIT:]


def _entry(session, row, covers, audits, roles_of) -> dict[str, Any]:
    pub = row.publication if isinstance(row.publication, dict) else {}
    merge = (row.details or {}).get("local_merge") if row.details else None
    publications = []
    if pub.get("commit"):
        audit = audits.get(_int(pub.get("audit_log_id")))
        publications.append(
            _hashed(
                {
                    "how": _str(pub.get("how"), 20),
                    "provider": _str(pub.get("provider"), 20),
                    "base": _str(pub.get("base"), 120),
                    "commit": _str(pub.get("commit"), 64),
                    **_audit_part(audit),
                }
            )
        )
    merged = None
    if isinstance(merge, dict) and merge.get("commit"):
        audit = audits.get(_int(merge.get("audit_log_id")))
        merged = _hashed(
            {
                "base": _str(merge.get("base"), 120),
                "commit": _str(merge.get("commit"), 64),
                "before": _str(merge.get("before"), 64),
                "after": _str(merge.get("after"), 64),
                "fast_forward": bool(merge.get("fast_forward")),
                "already": bool(merge.get("already")),
                "by": _by(merge.get("merged_by_user_id"), roles_of),
                "review_id": _int(merge.get("review_id")),
                **_audit_part(audit),
            }
        )
    moments = [p["at"] for p in publications if p["at"]]
    if merged:
        moments.append(merged["at"] or _str(merge.get("merged_at"), 32))
    released_at = min((m for m in moments if m), key=moment, default=None)
    tasks = _tasks(row, covers)
    review, reviews = _review(session, row, roles_of)
    integration_audit = audits.get(row.audit_log_id)
    entry = {
        "integration_id": row.id,
        "sprint_id": row.sprint_id,
        "status": row.status,
        "base": _str(row.base, 120),
        "commit": row.commit,
        "integrated_at": _iso(row.created_at),
        "integration_chain_hash": integration_audit.chain_hash
        if integration_audit
        else None,
        "released_at": released_at,
        "tasks": tasks,
        "suite": _suite(row),
        "publications": publications,
        "merge": merged,
        "review": review,
        "reviews": reviews,
        "readings": _readings(session, row.company_id, tasks),
        "undone": _undone(session, row.company_id, tasks),
        "hold_events": _hold_events(session, row, released_at),
    }
    return _hashed(entry)


def releases(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The company's releases (published or merged locally), oldest first."""
    rows = session.exec(
        select(SprintIntegration)
        .where(SprintIntegration.company_id == company_id)
        .order_by(SprintIntegration.id.desc())
        .limit(LIMIT * 2)
    ).all()
    released = sorted((r for r in rows if _released(r)), key=lambda r: r.id)[-LIMIT:]
    if not released:
        return []
    task_ids = {
        t["task_id"]
        for r in released
        for t in r.tasks or []
        if isinstance(t, dict) and _int(t.get("task_id")) is not None
    }
    covers = {
        i.id: list(i.covers or [])
        for i in (
            session.exec(select(BacklogItem).where(BacklogItem.id.in_(task_ids))).all()
            if task_ids
            else []
        )
    }
    audit_ids: set[int] = set()
    user_ids: set[int] = set()
    for r in released:
        pub = r.publication if isinstance(r.publication, dict) else {}
        merge = (r.details or {}).get("local_merge")
        merge = merge if isinstance(merge, dict) else {}
        audit_ids |= {
            a
            for a in (
                _int(pub.get("audit_log_id")),
                _int(merge.get("audit_log_id")),
                r.audit_log_id,
            )
            if a
        }
        if _int(merge.get("merged_by_user_id")) is not None:
            user_ids.add(merge["merged_by_user_id"])
    user_ids |= {
        u
        for u in session.exec(
            select(IntegrationReview.author_user_id).where(
                IntegrationReview.company_id == company_id,
                IntegrationReview.integration_id.in_([r.id for r in released]),
            )
        ).all()
        if u is not None
    }
    audits = _audits(session, company_id, audit_ids)
    roles_of = _roles(session, company_id, user_ids)
    return [_entry(session, r, covers, audits, roles_of) for r in released]
