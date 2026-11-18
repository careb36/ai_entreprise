"""Reading a company's records into the snapshot a pack is built from.

The snapshot is plain data (JSON types only), so the builders stay pure and the
drift check can hash exactly what each document read. It holds no personal
data: who approved a document or wrote a draft is reduced to a role ("PO", "a
person"), and people are counted by their company role, never named.

Keys: ``project``, ``specification`` (the newest approved one),
``specifications`` (every approved one, oldest first, with the ids of its
versions: a pack documents them all, ``specs``), ``architecture`` (the
company's current version; issue #329, F3), ``stack``,
``sprints`` (each with its ``tasks``), ``events`` (the task events the
issue log, risk register and change log read), ``verifications`` (the
Verifier's verdict and risk tier per delivery), ``history`` (every version of
the specifications and architectures), ``roles``, ``members``,
``retrospectives``, ``procedures``, ``integrations`` (with the commit each
task was merged at), ``wstg_observations`` (the WSTG items the Verifier's
checks observed), ``wstg_reviews`` (a person's recorded reviews, without
the reviewer's name), ``effort`` and ``costs`` (the backend's effort
observation per task and each sprint's reported model usage,
``collect_status``) and ``statements_<document>`` (a person's current
statement per person section, with the author's role and never their
address; ``statements``). Each list is bounded (``LIMIT``), newest records kept.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from models import (
    Agent,
    AgentMessage,
    AgentRun,
    Architecture,
    BacklogItem,
    Company,
    CompanyMember,
    PersonStatement,
    Procedure,
    ProjectDiscovery,
    Specification,
    Sprint,
    SprintIntegration,
    TaskEvent,
    Verification,
    WstgReview,
)
from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from services.contracts import roles as role_catalogue
from services.planning import arch_versions
from services.planning.project_docs import collect_status, statements, texts
from services.planning.project_docs.refs import digest
from services.platform import wstg_catalogue

LIMIT = 200
EVENT_KINDS = ("blocked", "escalated", "decided", "risk_hold", "carried_over")
TEXT_LIMIT = 500


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _role_or_person(drafted_by: str | None, role: str) -> str | None:
    if not drafted_by:
        return None
    return role if drafted_by == role else "person"


def current_specification(session: Session, company_id: int) -> Specification | None:
    """The newest approved specification, else the newest one."""
    rows = session.exec(
        select(Specification)
        .where(Specification.company_id == company_id)
        .order_by(Specification.id.desc())
    ).all()
    return next((s for s in rows if s.status == "approved"), rows[0] if rows else None)


def approved_specifications(session: Session, company_id: int) -> list[Specification]:
    """Every approved specification of the company, oldest approval first."""
    rows = session.exec(
        select(Specification).where(
            Specification.company_id == company_id,
            Specification.status == "approved",
        )
    ).all()
    return sorted(rows, key=lambda s: (s.approved_at is None, s.approved_at, s.id))


def current_architecture(session: Session, company_id: int) -> Architecture | None:
    """The company's current architecture, else its newest version."""
    current = arch_versions.current(session, company_id)
    if current is not None:
        return current
    rows = arch_versions.versions(session, company_id)
    return rows[0] if rows else None


def _specification(spec: Specification | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    out = {
        "id": spec.id,
        "version": spec.version,
        "title": spec.title,
        "summary": spec.summary,
        "status": spec.status,
        "approved_at": _iso(spec.approved_at),
        "drafted_by_role": _role_or_person(spec.drafted_by, "PO"),
        "requirements": list(spec.requirements or []),
        "quality": list(spec.quality or []),
        "constraints": list(spec.constraints or []),
        "questions": list(spec.questions or []),
    }
    out["sha256"] = digest(out)
    return out


def _listed(session: Session, spec: Specification) -> dict[str, Any]:
    """An approved specification of the list, with the ids of its versions (the
    sprints planned from any of them are its sprints)."""
    out = _specification(spec) or {}
    return {**out, "lineage_ids": sorted(arch_versions.lineage(session, spec))}


def _architecture(arch: Architecture | None) -> dict[str, Any] | None:
    if arch is None:
        return None
    out = {
        "id": arch.id,
        "version": arch.version,
        "title": arch.title,
        "overview": arch.overview,
        "status": arch.status,
        "approved_at": _iso(arch.approved_at),
        "drafted_by_role": _role_or_person(arch.drafted_by, "Architect"),
        "modules": list(arch.modules or []),
        "interfaces": list(arch.interfaces or []),
        "decisions": list(arch.decisions or []),
        "conventions": list(arch.conventions or []),
    }
    out["sha256"] = digest(out)
    return out


def _stack(session: Session, company_id: int) -> dict[str, Any] | None:
    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == company_id)
        .order_by(ProjectDiscovery.id.desc())
    ).first()
    if discovery is None or not discovery.detected_stack:
        return None
    try:
        stack = json.loads(discovery.detected_stack)
    except (TypeError, ValueError):
        return None
    if not isinstance(stack, dict):
        return None
    infra = stack.get("infrastructure") or {}
    out = {
        "discovery_id": discovery.id,
        "language": stack.get("language"),
        "frameworks": sorted(str(f) for f in stack.get("frameworks") or []),
        "databases": sorted(str(d) for d in stack.get("databases") or []),
        "infrastructure": sorted(str(k) for k in infra)
        if isinstance(infra, dict)
        else [],
    }
    out["sha256"] = digest(out)
    return out


def _sprints(session: Session, company_id: int) -> list[dict[str, Any]]:
    sprints = session.exec(
        select(Sprint)
        .where(Sprint.company_id == company_id, Sprint.archived_at.is_(None))
        .order_by(Sprint.id.desc())
        .limit(LIMIT)
    ).all()
    ids = [s.id for s in sprints]
    items = (
        session.exec(
            select(BacklogItem)
            .where(BacklogItem.sprint_id.in_(ids))
            .order_by(BacklogItem.id)
        ).all()
        if ids
        else []
    )
    spans = (
        session.exec(
            select(
                TaskEvent.sprint_id,
                func.min(TaskEvent.created_at),
                func.max(TaskEvent.created_at),
            )
            .where(TaskEvent.sprint_id.in_(ids))
            .group_by(TaskEvent.sprint_id)
        ).all()
        if ids
        else []
    )
    span = {row[0]: (row[1], row[2]) for row in spans}
    out = []
    for sprint in sorted(sprints, key=lambda s: s.id):
        first, last = span.get(sprint.id, (None, None))
        out.append(
            {
                "id": sprint.id,
                "name": sprint.sprint_name,
                "goal": sprint.goal,
                "status": sprint.status,
                "start_date": sprint.start_date,
                "specification_id": sprint.specification_id,
                "team": list(sprint.team or []),
                "first_event_at": _iso(first),
                "last_event_at": _iso(last),
                "tasks": [
                    {
                        "id": i.id,
                        "title": i.title,
                        "status": i.status,
                        "assignee": i.assignee,
                        "priority": i.priority,
                        "covers": list(i.covers or []),
                        "kind": i.kind,
                    }
                    for i in items
                    if i.sprint_id == sprint.id
                ],
            }
        )
    return out


def _events(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(TaskEvent)
        .where(
            TaskEvent.company_id == company_id,
            TaskEvent.event_type.in_(EVENT_KINDS),
        )
        .order_by(TaskEvent.id.desc())
        .limit(LIMIT)
    ).all()
    return [
        {
            "id": e.id,
            "task_id": e.task_id,
            "sprint_id": e.sprint_id,
            "kind": e.event_type,
            "actor": e.actor,
            "created_at": _iso(e.created_at),
            "chain_hash": e.chain_hash,
            "reason": str(
                (e.payload or {}).get("reason") or (e.payload or {}).get("text") or ""
            )[:TEXT_LIMIT],
            "direction": (e.payload or {}).get("direction"),
        }
        for e in sorted(rows, key=lambda e: e.id)
    ]


def _verifications(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The Verifier's readings of the company's deliveries, with the task each was
    for (through its ``verified`` event), the verdict and the risk tier.

    The event names its verification (``verification_id``, written by the
    task worker since the #329 retrospective); an older event that names only
    its run speaks for that run's verifications."""
    rows = session.exec(
        select(Verification, AgentRun.artifact_digest, TaskEvent.task_id)
        .join(AgentRun, AgentRun.id == Verification.agent_run_id)
        .join(
            TaskEvent,
            or_(
                TaskEvent.verification_id == Verification.id,
                and_(
                    TaskEvent.verification_id.is_(None),
                    TaskEvent.run_id == Verification.agent_run_id,
                ),
            ),
        )
        .where(
            Verification.company_id == company_id, TaskEvent.event_type == "verified"
        )
        .order_by(Verification.id.desc())
        .limit(LIMIT)
    ).all()
    out = []
    seen: set[int] = set()
    for verification, artifact_digest, task_id in sorted(rows, key=lambda r: r[0].id):
        if verification.id in seen:
            continue
        seen.add(verification.id)
        tier = next(
            (
                c
                for c in verification.checks or []
                if isinstance(c, dict) and c.get("name") == "risk_tier"
            ),
            {},
        )
        out.append(
            {
                "verification_id": verification.id,
                "run_id": verification.agent_run_id,
                "task_id": task_id,
                "verdict": verification.verdict,
                "tier": tier.get("tier"),
                "reasons": [str(r)[:200] for r in tier.get("reasons") or []][:10],
                "artifact_sha256": artifact_digest,
                "observed_at": _iso(verification.observed_at),
            }
        )
    return out


def _history(session: Session, company_id: int) -> list[dict[str, Any]]:
    """Every version of the company's specifications and architectures: the change log."""
    out: list[dict[str, Any]] = []
    for kind, model in (
        ("specification", Specification),
        ("architecture", Architecture),
    ):
        rows = session.exec(
            select(model)
            .where(model.company_id == company_id)
            .order_by(model.id.desc())
            .limit(LIMIT)
        ).all()
        for row in sorted(rows, key=lambda r: r.id):
            entry = {
                "kind": kind,
                "id": row.id,
                "version": row.version,
                "status": row.status,
                "supersedes_id": row.supersedes_id,
                "created_at": _iso(row.created_at),
                "approved_at": _iso(row.approved_at),
            }
            if kind == "specification":
                entry["requirement_ids"] = [
                    str(r.get("id"))
                    for r in row.requirements or []
                    if isinstance(r, dict)
                ]
            out.append(entry)
    return out


def _roles(session: Session, company_id: int) -> list[str]:
    rows = session.exec(select(Agent.role).where(Agent.company_id == company_id)).all()
    return sorted({role_catalogue.canonical(r) for r in rows if r})


def _members(session: Session, company_id: int) -> dict[str, int]:
    rows = session.exec(
        select(CompanyMember.human_role, func.count())
        .where(CompanyMember.company_id == company_id)
        .group_by(CompanyMember.human_role)
    ).all()
    return {str(role): int(count) for role, count in rows}


def _retrospectives(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(AgentMessage)
        .where(
            AgentMessage.company_id == company_id, AgentMessage.kind == "retrospective"
        )
        .order_by(AgentMessage.id.desc())
        .limit(LIMIT)
    ).all()
    return [
        {
            "id": m.id,
            "sprint_id": m.sprint_id,
            "created_at": _iso(m.created_at),
            "from_role": m.from_role,
            "body": (m.body or "")[: TEXT_LIMIT * 2],
        }
        for m in sorted(rows, key=lambda m: m.id)
    ]


def _procedures(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(Procedure)
        .where(
            Procedure.company_id == company_id, Procedure.source_sprint_id.is_not(None)
        )
        .order_by(Procedure.id.desc())
        .limit(LIMIT)
    ).all()
    return [
        {
            "id": p.id,
            "title": p.title,
            "role": p.role,
            "status": p.status,
            "source_sprint_id": p.source_sprint_id,
            "result": (p.outcome or {}).get("result")
            if isinstance(p.outcome, dict)
            else None,
        }
        for p in sorted(rows, key=lambda p: p.id)
    ]


def _integrations(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(SprintIntegration)
        .where(SprintIntegration.company_id == company_id)
        .order_by(SprintIntegration.id.desc())
        .limit(LIMIT)
    ).all()
    return [
        {
            "id": i.id,
            "sprint_id": i.sprint_id,
            "status": i.status,
            "commit": i.commit,
            "tested_commit": i.tested_commit,
            "created_at": _iso(i.created_at),
            "tasks": [
                {
                    "task_id": t.get("task_id"),
                    "commit": t.get("merge_commit") or t.get("source_commit"),
                }
                for t in i.tasks or []
                if isinstance(t, dict)
            ],
        }
        for i in sorted(rows, key=lambda i: i.id)
    ]


def _wstg_observations(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The WSTG items the Verifier's checks observed (the security probe, #540,
    and tests named after a WSTG test, ``evidence/wstg_tests.py``), per
    verification, with the run's artifact digest: what the checklist cites."""
    rows = session.exec(
        select(Verification, AgentRun.artifact_digest)
        .join(AgentRun, AgentRun.id == Verification.agent_run_id)
        .where(Verification.company_id == company_id)
        .order_by(Verification.id.desc())
        .limit(LIMIT)
    ).all()
    out = []
    for verification, artifact_digest in sorted(rows, key=lambda r: r[0].id):
        for check in verification.checks or []:
            if not isinstance(check, dict) or not isinstance(check.get("items"), list):
                continue
            items = [
                {
                    "id": wstg_catalogue.versioned(str(i.get("id"))),
                    "status": str(i.get("status") or ""),
                    "observation": str(i.get("observation") or "")[:240],
                }
                for i in check["items"]
                if isinstance(i, dict) and wstg_catalogue.versioned(str(i.get("id")))
            ]
            if not items:
                continue
            tool = check.get("tool") or check.get("name")
            if check.get("wstg_commit"):
                tool = f"{tool} (WSTG {str(check['wstg_commit'])[:7]})"
            out.append(
                {
                    "verification_id": verification.id,
                    "run_id": verification.agent_run_id,
                    "check": str(check.get("name") or ""),
                    "tool": str(tool or "")[:160],
                    "observed_by": str(check.get("observed_by") or ""),
                    "artifact_sha256": artifact_digest,
                    "observed_at": _iso(verification.observed_at),
                    "items": items,
                }
            )
    return out


def _wstg_reviews(session: Session, company_id: int) -> list[dict[str, Any]]:
    """The people's recorded reviews of WSTG tests; the reviewer's name is left out."""
    rows = session.exec(
        select(WstgReview)
        .where(WstgReview.company_id == company_id)
        .order_by(WstgReview.id.desc())
        .limit(LIMIT)
    ).all()
    return [
        {
            "id": r.id,
            "wstg_id": r.wstg_id,
            "result": r.result,
            "reviewed_on": r.reviewed_on,
            "artifact": (r.artifact or "")[:TEXT_LIMIT],
            "note": (r.note or "")[:TEXT_LIMIT],
            "reviewer_role": r.reviewer_role,
            "created_at": _iso(r.created_at),
            "record_sha256": r.record_sha256,
        }
        for r in sorted(rows, key=lambda r: r.id)
    ]


def current_statements(session: Session, company_id: int) -> list[PersonStatement]:
    """The newest row per person section (a withdrawal included), in catalogue order."""
    rows = session.exec(
        select(PersonStatement)
        .where(PersonStatement.company_id == company_id)
        .order_by(PersonStatement.id.desc())
    ).all()
    newest: dict[str, PersonStatement] = {}
    for row in rows:
        newest.setdefault(row.section, row)
    return [newest[k] for k in statements.SECTIONS if k in newest]


def _person_statements(session: Session, company_id: int) -> dict[str, Any]:
    """The current statements per document, without the author's address."""
    return statements.by_document(
        [
            {
                "id": r.id,
                "section": r.section,
                "text": r.text,
                "author_role": r.author_role,
                "created_at": _iso(r.created_at),
                "statement_sha256": r.statement_sha256,
            }
            for r in current_statements(session, company_id)
        ]
    )


def snapshot(session: Session, company: Company) -> dict[str, Any]:
    """Everything a pack reads about *company*, as plain data."""
    spec = current_specification(session, company.id)
    sprints = _sprints(session, company.id)
    return {
        "project": {"company_id": company.id, "name": company.company_name},
        # Not an input of any document's digest: the lint compares it apart.
        "language": texts.language(company.doc_language),
        "specification": _specification(spec),
        "specifications": [
            _listed(session, s) for s in approved_specifications(session, company.id)
        ][-LIMIT:],
        "architecture": _architecture(current_architecture(session, company.id)),
        "stack": _stack(session, company.id),
        "sprints": sprints,
        "events": _events(session, company.id),
        "verifications": _verifications(session, company.id),
        "history": _history(session, company.id),
        "roles": _roles(session, company.id),
        "members": _members(session, company.id),
        "retrospectives": _retrospectives(session, company.id),
        "procedures": _procedures(session, company.id),
        "integrations": _integrations(session, company.id),
        "wstg_observations": _wstg_observations(session, company.id),
        "wstg_reviews": _wstg_reviews(session, company.id),
        "effort": collect_status.effort(session, company.id),
        "costs": collect_status.costs(session, company.id, [s["id"] for s in sprints]),
        **_person_statements(session, company.id),
    }
