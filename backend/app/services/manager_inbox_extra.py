"""The rest of what a Manager decides in the product, as inbox items for the
Manager agent (issue #394), with the facts the platform observed.

- ``approve_specification`` / ``approve_architecture``: a draft with no open
  question, with what its deterministic check found;
- ``answer_question``: a draft with open questions, and the ones an approved
  specification or architecture already answers word for word;
- ``resume_after_credit``: a sprint the worker paused for lack of credit;
- ``merge_local`` / ``publish``: a done task's branch (and, for a merge, a
  sprint's integration whose suite passed or did not run), only when the
  policy names the rule, since nothing waits for a person there otherwise;
- ``undo_delivery``: a task-worker delivery whose latest verification is
  refuted, only when the policy names the rule.

Producer and agent text is labelled as data. Each item's ``state`` changes
with what the agent would have to look at again.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from models import AgentRun, Architecture, BacklogItem, Company, Specification, Sprint
from models.agent_run import TASK_WORKER_PRODUCER
from models.event import TaskEvent
from models.integration import SprintIntegration
from models.review import RunPublication
from sqlalchemy import func
from sqlmodel import Session, select

from services import arch_author, arch_check, spec_check, spec_planning, task_projection
from services.board import GATE_AUTHORS_LOWER, comment_list, is_rework_request
from services.manager_inbox import (
    FIELD_LIMIT,
    _latest_verification,
    _task_run,
    run_facts,
)

RECENT = 20
CREDIT_PREFIX = "No credit at"
MERGEABLE_INTEGRATIONS = ("tests_passed", "untested")
_NOT_QA = {"system", "user", "manager agent"} | GATE_AUTHORS_LOWER


def norm(text: Any) -> str:
    """A question or a decision's title as compared: case, spacing and the
    closing punctuation do not count; nothing else is guessed."""
    return re.sub(r"\s+", " ", str(text or "")).strip().strip("?.!:;").lower()


def _open(questions: Any) -> list[int]:
    return [
        i
        for i, q in enumerate(questions or [])
        if isinstance(q, dict) and not str(q.get("answer") or "").strip()
    ]


def known_answers(session: Session, company_id: int) -> dict[str, dict[str, str]]:
    """Answers the company's approved specifications and architectures give:
    their answered questions and their decisions, by normalized text."""
    known: dict[str, dict[str, str]] = {}
    for spec in session.exec(
        select(Specification).where(
            Specification.company_id == company_id,
            Specification.status == "approved",
        )
    ).all():
        for q in spec.questions or []:
            if isinstance(q, dict) and q.get("answer"):
                known.setdefault(
                    norm(q.get("question")),
                    {"answer": str(q["answer"]), "source": f"specification #{spec.id}"},
                )
    for arch in session.exec(
        select(Architecture).where(
            Architecture.company_id == company_id, Architecture.status == "approved"
        )
    ).all():
        for q in arch.questions or []:
            if isinstance(q, dict) and q.get("answer"):
                known.setdefault(
                    norm(q.get("question")),
                    {"answer": str(q["answer"]), "source": f"architecture #{arch.id}"},
                )
        for d in arch.decisions or []:
            if isinstance(d, dict) and d.get("title") and d.get("decision"):
                known.setdefault(
                    norm(d["title"]),
                    {
                        "answer": f"{d.get('id')}: {d['decision']}",
                        "source": f"architecture #{arch.id} {d.get('id')}",
                    },
                )
    return {k: v for k, v in known.items() if k}


def _question_item(kind: str, doc: Any, known: dict[str, dict[str, str]]) -> dict:
    open_ids = _open(doc.questions)
    answers = []
    for index in open_ids:
        found = known.get(norm(doc.questions[index].get("question")))
        if found:
            answers.append({"index": index, **found})
    facts = {
        "open_questions": len(open_ids),
        "open_question": bool(open_ids),
        "answer_known": bool(answers),
        "answers": answers,
    }
    lines = [f"{kind.capitalize()} #{doc.id} (draft): {doc.title}"]
    for index in open_ids:
        q = doc.questions[index]
        hit = next((a for a in answers if a["index"] == index), None)
        lines.append(f"- Q{index + 1} (data): {str(q.get('question'))[:400]}")
        lines.append(
            f"  answered by {hit['source']}: {hit['answer'][:400]}"
            if hit
            else "  no approved document answers it"
        )
    stamp = doc.updated_at.isoformat() if doc.updated_at else "-"
    return {
        "kind": f"{kind}_questions",
        "id": doc.id,
        "state": f"{stamp}:{','.join(map(str, open_ids))}",
        "category": "answer_question",
        "facts": facts,
        "text": "\n".join(lines),
    }


def _findings(result: dict[str, Any]) -> list[str]:
    return [
        f"{f.get('severity')} {f.get('where')}: {f.get('message')}"
        for f in result.get("findings") or []
    ][:15]


def _codes(result: dict[str, Any], code: str) -> int:
    return sum(1 for f in result.get("findings") or [] if f.get("code") == code)


def _specification_items(session: Session, company: Company, known) -> list[dict]:
    items = []
    for spec in session.exec(
        select(Specification)
        .where(Specification.company_id == company.id, Specification.status == "draft")
        .order_by(Specification.id)
    ).all():
        if _open(spec.questions):
            items.append(_question_item("specification", spec, known))
            continue
        result = spec_check.check(spec_planning.spec_content(spec))
        requirements = len(spec.requirements or [])
        facts = {
            "blocking_findings": result["blocking"],
            "open_questions": 0,
            "requirements": requirements,
            "all_requirements_have_criteria": requirements > 0
            and _codes(result, "no_criteria") == 0,
        }
        text = "\n".join(
            [
                f"Specification #{spec.id} v{spec.version} (draft): {spec.title}",
                f"Summary (data): {(spec.summary or '')[:FIELD_LIMIT]}",
                (
                    f"Requirements: {requirements}; the check blocks on "
                    f"{facts['blocking_findings']} finding(s)"
                ),
                *[f"- {f}" for f in _findings(result)],
            ]
        )
        items.append(
            {
                "kind": "specification",
                "id": spec.id,
                "state": f"draft:{spec.updated_at.isoformat() if spec.updated_at else '-'}",
                "category": "approve_specification",
                "facts": facts,
                "text": text,
            }
        )
    return items


def _architecture_items(session: Session, company: Company, known) -> list[dict]:
    items = []
    for arch in session.exec(
        select(Architecture)
        .where(Architecture.company_id == company.id, Architecture.status == "draft")
        .order_by(Architecture.id)
    ).all():
        if _open(arch.questions):
            items.append(_question_item("architecture", arch, known))
            continue
        spec = session.get(Specification, arch.specification_id)
        ids = arch_author.requirement_ids(spec) if spec else []
        result = arch_check.check(arch_author.content_of(arch), ids)
        uncovered = _codes(result, "uncovered_requirement")
        facts = {
            "blocking_findings": result["blocking"],
            "open_questions": 0,
            "specification_status": spec.status if spec else None,
            "uncovered_requirements": uncovered,
            "covers_all_requirements": bool(ids) and uncovered == 0,
        }
        text = "\n".join(
            [
                f"Architecture #{arch.id} v{arch.version} (draft): {arch.title}",
                f"Overview (data): {(arch.overview or '')[:FIELD_LIMIT]}",
                (
                    f"Specification: {facts['specification_status']}; requirements no "
                    f"module covers: {uncovered}; blocking "
                    f"findings: {facts['blocking_findings']}"
                ),
                *[f"- {f}" for f in _findings(result)],
            ]
        )
        items.append(
            {
                "kind": "architecture",
                "id": arch.id,
                "state": f"draft:{arch.updated_at.isoformat() if arch.updated_at else '-'}",
                "category": "approve_architecture",
                "facts": facts,
                "text": text,
            }
        )
    return items


def _credit_items(session: Session, company: Company) -> list[dict]:
    items = []
    for sprint in session.exec(
        select(Sprint)
        .where(Sprint.company_id == company.id, Sprint.status == "paused")
        .order_by(Sprint.id)
    ).all():
        reason = sprint.pause_reason or ""
        if not reason.startswith(CREDIT_PREFIX):
            continue
        pauses = session.exec(
            select(func.count(TaskEvent.id)).where(
                TaskEvent.sprint_id == sprint.id, TaskEvent.event_type == "paused"
            )
        ).one()
        items.append(
            {
                "kind": "sprint",
                "id": sprint.id,
                "state": f"paused:e{int(pauses or 0)}",
                "category": "resume_after_credit",
                "facts": {"sprint_status": sprint.status, "paused_for_credit": True},
                "text": f"Sprint #{sprint.id} {sprint.sprint_name} is paused: {reason}",
            }
        )
    return items


def qa_approved(comments: Any, record: Any = None) -> bool:
    """QA's latest verdict on the task is ``APPROVED:``, with no rework after it
    (from *record*'s events when they cover the thread, issue #324)."""
    if record is not None and (answer := record.qa_approved()) is not None:
        return answer
    for comment in reversed(comment_list(comments)):
        author = str(comment.get("author") or "").strip().lower()
        text = str(comment.get("text") or "").strip()
        if is_rework_request(comment):
            return False
        if author not in _NOT_QA and text.upper().startswith("APPROVED:"):
            return True
    return False


def _git(session: Session, run: AgentRun) -> dict[str, Any]:
    from services.review_changes import latest_workspace_report

    report = latest_workspace_report(session, run)
    git = (report.evidence or {}).get("git") if report is not None else None
    return git if isinstance(git, dict) else {}


def _publication(session: Session, run: AgentRun, commit: str, local: bool) -> bool:
    query = select(RunPublication).where(
        RunPublication.agent_run_id == run.id, RunPublication.commit == commit
    )
    query = query.where(
        RunPublication.how == "local" if local else RunPublication.how != "local"
    )
    return session.exec(query).first() is not None


def _done_tasks(session: Session, company: Company) -> list[BacklogItem]:
    return session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(Sprint.company_id == company.id, BacklogItem.status == "done")
        .order_by(BacklogItem.id.desc())
        .limit(RECENT)
    ).all()


def _branch_items(
    session: Session, company: Company, category: str, publishing_on: bool = False
) -> list[dict]:
    items = []
    done = list(_done_tasks(session, company))
    records = task_projection.records_for(session, done)
    for item in done:
        run = _task_run(session, company.id, item.id)
        if run is None:
            continue
        git = _git(session, run)
        commit = str(git.get("commit") or git.get("parent") or "")
        if not git.get("branch") or not commit:
            continue
        verification = _latest_verification(session, run.id)
        facts = run_facts(run, verification)
        facts.update(
            target="task",
            task_id=item.id,
            has_branch=bool(git.get("repository")),
            qa_approved=qa_approved(item.comments, records[item.id]),
            publishing_on=publishing_on,
        )
        local = category == "merge_local"
        done = _publication(session, run, commit, local)
        facts["merged" if local else "published"] = done
        if done:
            continue
        what = "merged locally" if local else "published"
        items.append(
            {
                "kind": "task_branch" if local else "publication",
                "id": run.id,
                "state": f"r{run.id}:v{facts['verification_id'] or '-'}:c{commit[:12]}",
                "category": category,
                "facts": facts,
                "text": "\n".join(
                    [
                        (
                            f"Task #{item.id} (done): {item.title}; its branch "
                            f"{git.get('branch')} at {commit[:12]} is not {what}"
                        ),
                        (
                            f"QA approved: {facts['qa_approved']}; latest run verdict: "
                            f"{facts['verdict'] or 'none'}; risk tier: "
                            f"{facts['risk_tier'] or 'unknown'}; files: {facts['files']}"
                        ),
                        *[f"- {c}" for c in facts.get("checks") or []],
                    ]
                ),
            }
        )
    return items


def _integration_items(session: Session, company: Company) -> list[dict]:
    from services import integration

    items = []
    sprints = session.exec(
        select(Sprint.id).where(Sprint.company_id == company.id).order_by(Sprint.id)
    ).all()
    for sprint_id in sprints:
        row: SprintIntegration | None = integration.latest(session, sprint_id)
        if row is None or row.status not in MERGEABLE_INTEGRATIONS:
            continue
        if (row.details or {}).get("local_merge"):
            continue
        items.append(
            {
                "kind": "integration",
                "id": row.id,
                "state": f"{row.status}:{(row.commit or '-')[:12]}",
                "category": "merge_local",
                "facts": {
                    "target": "integration",
                    "integration_status": row.status,
                    "merged": False,
                    "sprint_id": sprint_id,
                },
                "text": f"Sprint #{sprint_id}'s integration #{row.id} on {row.branch} "
                f"at {(row.commit or '-')[:12]}: {row.status}",
            }
        )
    return items


def _moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def person_since(comments: Any, since: datetime | None, record: Any = None) -> bool:
    """A person wrote on the task after *since* (or when, cannot be read), from
    *record*'s events when they cover the thread (issue #324)."""
    if since is None:
        return True
    since = since.astimezone(UTC).replace(tzinfo=None) if since.tzinfo else since
    if record is not None and (answer := record.person_since(since)) is not None:
        return answer
    for comment in comment_list(comments):
        if str(comment.get("author") or "").strip().lower() != "user":
            continue
        moment = _moment(comment.get("created_at"))
        if moment is None or moment >= since:
            return True
    return False


def _undo_items(session: Session, company: Company) -> list[dict]:
    from services.delivery_undo import snapshot_of
    from services.review_changes import latest_workspace_report

    items = []
    refs = session.exec(
        select(AgentRun.external_ref)
        .where(
            AgentRun.company_id == company.id,
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .group_by(AgentRun.external_ref)
        .order_by(func.max(AgentRun.id).desc())
        .limit(RECENT)
    ).all()
    for ref in refs:
        if not str(ref or "").startswith("task-"):
            continue
        task_id = int(str(ref)[5:]) if str(ref)[5:].isdigit() else None
        item = session.get(BacklogItem, task_id) if task_id else None
        run = _task_run(session, company.id, task_id) if item else None
        if run is None:
            continue
        verification = _latest_verification(session, run.id)
        if verification is None or verification.verdict != "refuted":
            continue
        report = latest_workspace_report(session, run)
        snapshot = snapshot_of(session, run)
        facts = run_facts(run, verification)
        facts.update(
            task_id=item.id,
            on_branch=bool(report and (report.evidence or {}).get("git")),
            has_snapshot=bool(snapshot and snapshot["undone_at"] is None),
            person_touched=person_since(
                item.comments,
                verification.observed_at,
                task_projection.record_of(session, item),
            ),
        )
        if snapshot is None or snapshot["undone_at"] is not None:
            continue
        items.append(
            {
                "kind": "delivery",
                "id": run.id,
                "state": f"v{verification.id}:s{snapshot['id']}",
                "category": "undo_delivery",
                "facts": facts,
                "text": "\n".join(
                    [
                        (
                            f"Task #{item.id}: {item.title}; its delivery (run #{run.id}) "
                            "was refuted and the daemon kept a snapshot of the files before it"
                        ),
                        f"A person worked on the task since: {facts['person_touched']}",
                        *[f"- {c}" for c in facts.get("checks") or []],
                    ]
                ),
            }
        )
    return items


def extra_items(session: Session, company: Company) -> list[dict[str, Any]]:
    """The items of issue #394's categories for *company*."""
    from services import manager_policy

    policy = manager_policy.load(session, company.id)
    rules = set(policy.rules)
    known = known_answers(session, company.id)
    items = _specification_items(session, company, known)
    items += _architecture_items(session, company, known)
    items += _credit_items(session, company)
    if "merge_local" in rules:
        items += _branch_items(session, company, "merge_local")
        items += _integration_items(session, company)
    if "publish" in rules:
        items += _branch_items(session, company, "publish", policy.publish_enabled)
    if "undo_delivery" in rules:
        items += _undo_items(session, company)
    return items
