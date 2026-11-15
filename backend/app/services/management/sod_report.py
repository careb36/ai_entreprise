"""The segregation-of-duties matrix and violation report (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

Computed from records, never claimed:

- **approvals**: every approval a person or an agent recorded — a held
  delivery accepted and another producer's run approved (``run_reviews``), a
  specification or an architecture approved (``approved_by``), a run or a
  sprint's integration merged into the base branch (``run_publications`` with
  ``how=local``, the integration's ``local_merge``) — each beside who asked
  for the work (``services/loop/sod_requesters.py``). Each is ``separated``
  (requester and approver differ), ``exception`` (the same person, with the
  ``sod_exception_applied`` audit row of the single-person exception),
  ``violation`` (the same person with no such row: approved before the rule,
  or while it was off), ``unattributed`` (no recorded requester) or
  ``not_a_person`` (an agent approved);
- **refusals**: the ``sod_refused`` audit rows;
- **matrix**: per member (with their effective role), per effective role and
  per agent role, how many times each did each duty: request (sprints
  started), plan (drafts written), deliver (agents' deliveries), verify (the
  Verifier's verifications, QA's reviews, a person's review comments and
  change requests), approve and release (merges and publications).

``approved_by`` and ``drafted_by`` are written by the backend from the
session; ``AgentRunApproval.approver_name`` is a typed name, a claim, and is
not read.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from models import (
    AgentRun,
    Architecture,
    AuditLog,
    CompanyMember,
    RunPublication,
    RunReview,
    Specification,
    Sprint,
    SprintIntegration,
    TaskEvent,
    User,
    Verification,
)
from sqlalchemy import func
from sqlmodel import Session, select

from services.loop import sod_requesters
from services.loop.run_review import task_of
from services.platform import sod, teams
from services.platform.sod import Person, Requester

DUTIES = ("request", "plan", "deliver", "verify", "approve", "release")
AGENT_ACTORS_SKIPPED = {"system", "gate", "person"}
REFUSALS_LIMIT = 100


def status_of(
    approver: Person, requesters: list[Requester], excepted: bool
) -> tuple[str, list[str]]:
    """Pure: an approval's status and the conflicts behind it."""
    if not approver.known():
        return "not_a_person", []
    known = [r for r in requesters if r.person.known()]
    if not known:
        return "unattributed", []
    conflicts = [r.why for r in known if sod.same_person(approver, r.person)]
    if not conflicts:
        return "separated", []
    return ("exception" if excepted else "violation"), conflicts


def _exceptions(session: Session, company_id: int) -> set[tuple]:
    rows = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id,
            AuditLog.action == "sod_exception_applied",
        )
    ).all()
    out = set()
    for row in rows:
        d = row.details or {}
        subject = d.get("subject") or {}
        out.add(
            (
                d.get("action"),
                subject.get("kind"),
                subject.get("id"),
                d.get("actor_user_id"),
            )
        )
    return out


def _person(p: Person) -> dict[str, Any]:
    """A person by id and name; an email only when it is all the record has."""
    return {"user_id": p.user_id, "label": p.label or p.email or None}


def _approvals(session: Session, company_id: int) -> list[dict[str, Any]]:
    """``(action, subject_kind, subject_id, approver, requesters, at, label)`` rows."""
    out = []
    for review in session.exec(
        select(RunReview).where(
            RunReview.company_id == company_id,
            RunReview.event == "approve",
            RunReview.outcome.in_(("accepted", "approval_recorded")),
        )
    ).all():
        run = session.get(AgentRun, review.agent_run_id)
        if run is None:
            continue
        item = task_of(session, run) if review.outcome == "accepted" else None
        uid = review.author_user_id if (review.author_user_id or 0) > 0 else None
        out.append(
            (
                sod.ACCEPT_HELD if item else sod.APPROVE_RUN,
                "task" if item else "agent_run",
                item.id if item else run.id,
                sod.person_of_user_id(session, uid),
                sod_requesters.of_run(session, run),
                review.created_at,
                item.title if item else run.external_ref,
            )
        )
    for model, action, kind, of in (
        (
            Specification,
            sod.APPROVE_SPEC,
            "specification",
            sod_requesters.of_specification,
        ),
        (
            Architecture,
            sod.APPROVE_ARCH,
            "architecture",
            sod_requesters.of_architecture,
        ),
    ):
        for doc in session.exec(
            select(model).where(
                model.company_id == company_id, model.approved_by.is_not(None)
            )
        ).all():
            approver = sod.person_of_email(session, doc.approved_by)
            if not approver.known():
                approver = Person(label=doc.approved_by or "")
            out.append(
                (
                    action,
                    kind,
                    doc.id,
                    approver,
                    of(session, doc),
                    doc.approved_at,
                    f"{doc.title} v{doc.version}",
                )
            )
    for pub in session.exec(
        select(RunPublication).where(
            RunPublication.company_id == company_id, RunPublication.how == "local"
        )
    ).all():
        run = session.get(AgentRun, pub.agent_run_id)
        if run is None:
            continue
        approver = sod.person_of_user_id(session, pub.published_by_user_id)
        if not approver.known():
            approver = Person(label=pub.published_by)
        out.append(
            (
                sod.RELEASE,
                "agent_run",
                run.id,
                approver,
                sod_requesters.of_run(session, run),
                pub.created_at,
                f"{run.external_ref} → {pub.base}",
            )
        )
    for row in session.exec(
        select(SprintIntegration).where(SprintIntegration.company_id == company_id)
    ).all():
        merged = (row.details or {}).get("local_merge")
        if not isinstance(merged, dict):
            continue
        approver = sod.person_of_user_id(session, merged.get("merged_by_user_id"))
        if not approver.known():
            approver = Person(label=str(merged.get("merged_by") or ""))
        sprint = session.get(Sprint, row.sprint_id)
        out.append(
            (
                sod.RELEASE,
                "sprint_integration",
                row.id,
                approver,
                sod_requesters.of_sprint(session, sprint),
                row.created_at,
                f"{sprint.sprint_name if sprint else row.sprint_id} → {row.base}",
            )
        )
    return out


def _refusals(session: Session, company_id: int) -> list[dict[str, Any]]:
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.company_id == company_id, AuditLog.action == "sod_refused")
        .order_by(AuditLog.id.desc())
        .limit(REFUSALS_LIMIT)
    ).all()
    return [
        {
            "audit_log_id": r.id,
            "at": r.created_at.isoformat() if r.created_at else None,
            # The person's name, never the email the audit row's agent id carries.
            "by": _person(
                sod.person_of_user_id(session, (r.details or {}).get("actor_user_id"))
            )["label"],
            "action": (r.details or {}).get("action"),
            "subject": (r.details or {}).get("subject"),
            "conflict": (r.result or {}).get("conflict"),
        }
        for r in rows
    ]


def _matrix(session, company_id, approvals) -> dict[str, Any]:
    people: dict[int, Counter] = defaultdict(Counter)
    agents: dict[str, Counter] = defaultdict(Counter)
    for uid, n in session.exec(
        select(Sprint.started_by_user_id, func.count())
        .where(Sprint.company_id == company_id, Sprint.started_by_user_id.is_not(None))
        .group_by(Sprint.started_by_user_id)
    ).all():
        people[uid]["request"] += n
    for model in (Specification, Architecture):
        for drafted in session.exec(
            select(model.drafted_by).where(model.company_id == company_id)
        ).all():
            who = sod.person_of_email(session, drafted)
            if who.user_id is not None:
                people[who.user_id]["plan"] += 1
            elif drafted:
                agents[drafted if "@" not in drafted else "unknown person"]["plan"] += 1
    for uid, n in session.exec(
        select(RunReview.author_user_id, func.count())
        .where(
            RunReview.company_id == company_id,
            RunReview.event.in_(("comment", "request_changes")),
            RunReview.author_user_id.is_not(None),
        )
        .group_by(RunReview.author_user_id)
    ).all():
        if uid > 0:
            people[uid]["verify"] += n
    for role, n in session.exec(
        select(AgentRun.agent_role, func.count())
        .where(AgentRun.company_id == company_id, AgentRun.agent_role.is_not(None))
        .group_by(AgentRun.agent_role)
    ).all():
        agents[role]["deliver"] += n
    verifications = session.exec(
        select(func.count())
        .select_from(Verification)
        .where(Verification.company_id == company_id)
    ).one()
    if verifications:
        agents["Verifier"]["verify"] += verifications
    for actor, n in session.exec(
        select(TaskEvent.actor, func.count())
        .where(TaskEvent.company_id == company_id, TaskEvent.event_type == "reviewed")
        .group_by(TaskEvent.actor)
    ).all():
        if actor not in AGENT_ACTORS_SKIPPED:
            agents[actor]["verify"] += n
    for action, _kind, _id, approver, _req, _at, _label in approvals:
        duty = "release" if action == sod.RELEASE else "approve"
        if approver.user_id is not None:
            people[approver.user_id][duty] += 1
        elif approver.label:
            agents[approver.label][duty] += 1
    for uid, n in session.exec(
        select(RunPublication.published_by_user_id, func.count())
        .where(
            RunPublication.company_id == company_id,
            RunPublication.how != "local",
            RunPublication.published_by_user_id.is_not(None),
        )
        .group_by(RunPublication.published_by_user_id)
    ).all():
        people[uid]["release"] += n
    roles = teams.members_with_role(session, company_id, "Viewer")
    members = {
        m.user_id
        for m in session.exec(
            select(CompanyMember).where(CompanyMember.company_id == company_id)
        ).all()
    }
    member_rows = []
    by_role: dict[str, Counter] = defaultdict(Counter)
    for uid in sorted(set(people) | members):
        user = session.get(User, uid)
        role = roles.get(uid)
        counts = {d: people[uid][d] for d in DUTIES}
        by_role[role or "former member"].update(counts)
        member_rows.append(
            {
                "user_id": uid,
                "label": (user.full_name or user.email) if user else f"#{uid}",
                "effective_role": role,
                "member": uid in members,
                **counts,
                "holds_request_and_approval": bool(
                    counts["request"] and (counts["approve"] or counts["release"])
                ),
            }
        )
    return {
        "duties": list(DUTIES),
        "members": member_rows,
        "roles": [
            {"role": r, **{d: by_role[r][d] for d in DUTIES}} for r in sorted(by_role)
        ],
        "agents": [
            {"role": a, **{d: agents[a][d] for d in DUTIES}} for a in sorted(agents)
        ],
    }


def report(session: Session, company_id: int) -> dict[str, Any]:
    """The matrix, every approval with its status, and the refusals."""
    excepted = _exceptions(session, company_id)
    raw = _approvals(session, company_id)
    approvals = []
    for action, kind, subject_id, approver, requesters, at, label in raw:
        status, conflicts = status_of(
            approver,
            requesters,
            (action, kind, subject_id, approver.user_id) in excepted,
        )
        approvals.append(
            {
                "action": action,
                "subject": {"kind": kind, "id": subject_id, "label": label},
                "approver": _person(approver),
                "requesters": [{**_person(r.person), "why": r.why} for r in requesters],
                "status": status,
                "conflicts": conflicts,
                "at": at.isoformat() if at else None,
            }
        )
    approvals.sort(key=lambda a: (a["at"] or "", a["action"], a["subject"]["id"]))
    counts = Counter(a["status"] for a in approvals)
    policy = sod.policy_of(session, company_id)
    return {
        "company_id": company_id,
        "mode": policy.mode,
        "summary": {
            s: counts.get(s, 0)
            for s in (
                "separated",
                "exception",
                "violation",
                "unattributed",
                "not_a_person",
            )
        },
        "approvals": approvals,
        "violations": [
            a for a in approvals if a["status"] in ("violation", "exception")
        ],
        "refusals": _refusals(session, company_id),
        "matrix": _matrix(session, company_id, raw),
    }
