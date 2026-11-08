"""The Manager agent's further actions and the client's overrides of them
(issue #394), each through the code a person's decision goes through.

=====================  =====================================  ==========================================
Category               What the agent does                    What an override does
=====================  =====================================  ==========================================
merge_local            ``workspace.git.merge`` as             Recorded and counted, nothing undone: a
                       ``POST /v1/agent_runs/{id}/merge`` or  merge into the base branch is reverted in
                       the integration merge, recorded as a   git by a person (``NOT_UNDONE``).
                       ``local`` publication or under the
                       integration's details
undo_delivery          ``workspace.undo`` from the daemon's   Recorded and counted, nothing undone: the
                       snapshot, as ``POST .../undo``         files are put back by redelivering.
publish                ``workspace.git.publish``, as          Refused (409): a pushed branch and an open
                       ``POST .../publish``                   request are closed on the platform.
blocked_task           send back: the task to ``todo`` with   Back to blocked for a person while the
(send_back)            the ScrumMaster's brief (a person's    agents have not taken it up (409 after).
                       send-back, authored by the agent)
blocked_task           a mark the next sprint's plan reads    A person's note taking the mark back; 409
(carry_over)           (``carry_over.CARRY_MARK_PREFIX``)     once the task was carried.
answer_question        the answers an approved document       The agent's answers cleared while the
                       gives, ``answered_by`` the agent       draft is still a draft (409 after).
resume_after_credit    the sprint resumed and its waiting     The sprint paused again with its reason
                       tasks dispatched, as ``/resume``       while it is still active.
approve_specification  ``document_approval``, as ``/approve`` Back to draft, what it superseded restored,
approve_architecture                                          only while nothing was planned from it.
=====================  =====================================  ==========================================

``act`` returns the outcome and what to audit; it raises ``Stale`` when the
item changed (or no daemon answered: the next cycle tries again) and
``Failed`` when the daemon or the check refused.
"""

from __future__ import annotations

from typing import Any

from models import AgentRun, Architecture, AuditLog, BacklogItem, Specification, Sprint
from models.integration import SprintIntegration
from models.manager import ManagerDecision
from sqlmodel import Session

from services import document_approval, manager_observe, sprint_control, task_events
from services.board import BRIEF_PREFIX, SEND_BACK_MARKER
from services.carry_over import (
    CARRY_MARK_PREFIX,
    CARRY_UNMARK_PREFIX,
    carried_over,
    waits_for_a_person,
)
from services.delivery_undo import UndoRefused, record_undo, undo_spec
from services.git_publish import PublishRefused, publish_spec, record_publication
from services.local_merge import (
    integration_merge_spec,
    record_integration_merge,
    record_run_merge,
    run_merge_spec,
)
from services.workspace_daemon import DaemonError, DaemonUnavailable

AGENT = "Manager agent"
OVERRIDE_PREFIX = "Override:"
DAEMON_TIMEOUT_SECONDS = 150.0
NOT_UNDONE = "recorded_not_undone"
CATEGORIES = frozenset(
    {
        "merge_local",
        "undo_delivery",
        "publish",
        "blocked_task",
        "answer_question",
        "resume_after_credit",
        "approve_specification",
        "approve_architecture",
    }
)


class Stale(Exception):
    """The item is no longer as the agent saw it; nothing was changed."""


class Failed(Exception):
    """The daemon or the check refused the action; nothing was changed."""


class Refused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _daemon(subject: str, spec: dict[str, Any]) -> dict[str, Any]:
    try:
        return manager_observe.request(subject, spec, DAEMON_TIMEOUT_SECONDS)
    except DaemonUnavailable as exc:
        raise Stale(f"no workspace daemon answered: {exc}") from exc
    except DaemonError as exc:
        raise Failed(exc.message) from exc


def _run(session: Session, row: ManagerDecision) -> AgentRun:
    run = session.get(AgentRun, row.item_id)
    if run is None or run.company_id != row.company_id:
        raise Stale("the run is gone")
    return run


def _merge(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    try:
        if row.item_kind == "integration":
            integration = session.get(SprintIntegration, row.item_id)
            if integration is None or integration.company_id != row.company_id:
                raise Stale("the integration is gone")
            reply = _daemon(
                "workspace.git.merge", integration_merge_spec(session, integration)
            )
            done = record_integration_merge(
                session, integration, reply, merged_by=AGENT, agent_id=AGENT
            )
            return "merged", {"local_merge": (done.details or {}).get("local_merge")}
        run = _run(session, row)
        reply = _daemon("workspace.git.merge", run_merge_spec(session, run))
        publication = record_run_merge(
            session, run, reply, merged_by=AGENT, merged_by_user_id=None, agent_id=AGENT
        )
        return "merged", {
            "publication_id": publication.id,
            "commit": publication.commit,
        }
    except PublishRefused as exc:
        raise Failed(exc.detail) from exc


def _undo(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    run = _run(session, row)
    try:
        reply = _daemon("workspace.undo", undo_spec(session, run))
        return "undone", record_undo(
            session, run, reply, undone_by=AGENT, agent_id=AGENT
        )
    except UndoRefused as exc:
        raise Failed(exc.detail) from exc


def _publish(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    run = _run(session, row)
    try:
        reply = _daemon("workspace.git.publish", publish_spec(session, run))
        publication = record_publication(
            session,
            run,
            reply,
            published_by=AGENT,
            published_by_user_id=None,
            agent_id=AGENT,
        )
    except PublishRefused as exc:
        raise Failed(exc.detail) from exc
    return "published", {"publication_id": publication.id, "url": publication.url}


def _task(session: Session, row: ManagerDecision) -> BacklogItem:
    item = session.get(BacklogItem, row.item_id)
    if item is None or not waits_for_a_person(item) or carried_over(item):
        raise Stale("the task no longer waits for a person")
    return item


def _blocked_task(
    session: Session, row: ManagerDecision, rule: str
) -> tuple[str, dict]:
    from services import scrum_master

    item = _task(session, row)
    reason = row.suggested_reason or ""
    if row.suggested_decision == "send_back":
        triage = scrum_master.triages_for(session, [item]).get(item.id) or {}
        brief = str(triage.get("brief") or "")
        task_events.record(
            session,
            item,
            "sent_back",
            AGENT,
            status="todo",
            text=f"{SEND_BACK_MARKER} by the Manager agent under the company's "
            f"policy (rule {rule}, decision #{row.id}). {BRIEF_PREFIX}{brief}",
        )
        return "sent_back", {"brief": brief}
    task_events.record(
        session,
        item,
        "note",
        AGENT,
        text=f"{CARRY_MARK_PREFIX} under the company's policy (rule {rule}, "
        f"decision #{row.id}): the next sprint planned gives it another attempt. "
        f"{reason}",
    )
    return "marked_for_carry_over", {}


def _doc(session: Session, row: ManagerDecision) -> Specification | Architecture:
    model = Specification if row.item_kind.startswith("specification") else Architecture
    doc = session.get(model, row.item_id)
    if doc is None or doc.company_id != row.company_id or doc.status != "draft":
        raise Stale("the document is no longer a draft")
    return doc


def _answer(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    from services.manager_inbox_extra import known_answers, norm

    doc = _doc(session, row)
    known = known_answers(session, row.company_id)
    questions = [dict(q) if isinstance(q, dict) else q for q in doc.questions or []]
    answered = []
    for index, q in enumerate(questions):
        if not isinstance(q, dict) or str(q.get("answer") or "").strip():
            continue
        found = known.get(norm(q.get("question")))
        if found:
            q.update(
                answer=found["answer"], answered_by=AGENT, answer_source=found["source"]
            )
            answered.append(index)
    if not answered:
        raise Stale("no open question has a known answer any more")
    doc.questions = questions
    doc.updated_at = document_approval._now()
    session.add(doc)
    return f"answered {len(answered)}", {"answered": answered}


def _resume(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    sprint = session.get(Sprint, row.item_id)
    if sprint is None or sprint.company_id != row.company_id:
        raise Stale("the sprint is gone")
    if sprint.status != sprint_control.PAUSED:
        raise Stale(f"the sprint is {sprint.status}")
    reason = sprint.pause_reason
    sprint.status = sprint_control.ACTIVE
    sprint.pause_reason = None
    session.add(sprint)
    dispatched = sprint_control.dispatch_todo(session, sprint)
    return "resumed", {"dispatched": dispatched, "pause_reason": reason}


def _approve(session: Session, row: ManagerDecision) -> tuple[str, dict]:
    doc = _doc(session, row)
    before = document_approval.last_sprint_id(session, row.company_id)
    try:
        if isinstance(doc, Specification):
            superseded = document_approval.approve_specification(session, doc, AGENT)
        else:
            spec = session.get(Specification, doc.specification_id)
            if spec is None:
                raise Stale("the specification is gone")
            superseded = document_approval.approve_architecture(
                session, doc, spec, AGENT
            )
    except document_approval.ApprovalRefused as exc:
        raise Failed(str(exc.detail)) from exc
    return "approved", {"superseded": superseded, "sprints_before": before}


def act(session: Session, row: ManagerDecision, rule: str) -> tuple[str, dict]:
    """Take *row*'s decision in one of ``CATEGORIES``; the outcome and its facts."""
    if row.category == "merge_local":
        return _merge(session, row)
    if row.category == "undo_delivery":
        return _undo(session, row)
    if row.category == "publish":
        return _publish(session, row)
    if row.category == "blocked_task":
        return _blocked_task(session, row, rule)
    if row.category == "answer_question":
        return _answer(session, row)
    if row.category == "resume_after_credit":
        return _resume(session, row)
    return _approve(session, row)


def _acted(session: Session, row: ManagerDecision) -> dict[str, Any]:
    """What the agent's action recorded in its audit row."""
    audit = session.get(AuditLog, row.audit_log_id) if row.audit_log_id else None
    result = (
        audit.result if audit is not None and isinstance(audit.result, dict) else {}
    )
    return result.get("facts") or {}


def _undo_send_back(session: Session, row: ManagerDecision, actor: str) -> str:
    item = session.get(BacklogItem, row.item_id)
    if item is None or item.status != "todo":
        raise Refused(
            409,
            f"The agents took the task up again ({item.status if item else 'gone'}); "
            "it is no longer as the Manager agent left it.",
        )
    task_events.record(
        session,
        item,
        "moved",
        task_events.PERSON,
        status="blocked",
        text=f"{OVERRIDE_PREFIX} {actor} took back the Manager agent's send-back "
        f"(decision #{row.id}); the task waits for a person again.",
    )
    return "waits_for_a_person"


def _undo_mark(session: Session, row: ManagerDecision, actor: str) -> str:
    item = session.get(BacklogItem, row.item_id)
    if item is None or carried_over(item):
        raise Refused(409, "The task was carried into another sprint already.")
    task_events.record(
        session,
        item,
        "note",
        task_events.PERSON,
        text=f"{CARRY_UNMARK_PREFIX} by {actor} (decision #{row.id}); the task "
        "waits for a person.",
    )
    return "waits_for_a_person"


def _undo_answers(session: Session, row: ManagerDecision) -> str:
    try:
        doc = _doc(session, row)
    except Stale as exc:
        raise Refused(409, "The document is no longer a draft.") from exc
    questions = [dict(q) if isinstance(q, dict) else q for q in doc.questions or []]
    cleared = 0
    for index in _acted(session, row).get("answered") or []:
        q = questions[index] if 0 <= index < len(questions) else None
        if isinstance(q, dict) and q.get("answered_by") == AGENT:
            q.update(answer=None, answered_by=None, answer_source=None)
            cleared += 1
    doc.questions = questions
    doc.updated_at = document_approval._now()
    session.add(doc)
    return f"reopened {cleared}"


def _undo_resume(session: Session, row: ManagerDecision, actor: str) -> str:
    sprint = session.get(Sprint, row.item_id)
    reason = _acted(session, row).get("pause_reason") or f"Paused by {actor}"
    if sprint is None or not sprint_control.pause(session, sprint, reason):
        raise Refused(409, "The sprint is no longer running as the agent left it.")
    return "paused"


def _undo_approval(session: Session, row: ManagerDecision) -> str:
    model = Specification if row.category == "approve_specification" else Architecture
    doc = session.get(model, row.item_id)
    if doc is None:
        raise Refused(409, "The document is gone.")
    acted = _acted(session, row)
    try:
        document_approval.unapprove(
            session,
            doc,
            AGENT,
            list(acted.get("superseded") or []),
            int(acted.get("sprints_before") or 0),
        )
    except document_approval.ApprovalRefused as exc:
        raise Refused(exc.status, str(exc.detail)) from exc
    return "back_to_draft"


def override(session: Session, row: ManagerDecision, actor: str) -> str:
    """Take back the agent's decision *row*; the outcome. Raises ``Refused``."""
    if row.category == "publish":
        raise Refused(
            409,
            "A published branch cannot be taken back here: close the pull or merge "
            "request on the platform, and delete the branch there.",
        )
    if row.category in ("merge_local", "undo_delivery"):
        return NOT_UNDONE
    if row.category == "blocked_task":
        if row.outcome == "sent_back":
            return _undo_send_back(session, row, actor)
        return _undo_mark(session, row, actor)
    if row.category == "answer_question":
        return _undo_answers(session, row)
    if row.category == "resume_after_credit":
        return _undo_resume(session, row, actor)
    return _undo_approval(session, row)


def override_note(row: ManagerDecision) -> str | None:
    """What the override response says when the platform undid nothing."""
    if row.category == "merge_local":
        return (
            "Recorded and counted against the rule; the merge stays in the base "
            "branch: revert it in git yourself."
        )
    if row.category == "undo_delivery":
        return (
            "Recorded and counted against the rule; the files stay as the undo "
            "left them: send the task back to have them delivered again."
        )
    return None
