"""What waits for a person, with the facts the platform observed (issue #331).

The Manager agent's view of a company's inbox (``services/management/manager_agent.py``):
each item a person could decide, the category the platform gives it, the
facts its policy reads (verdict, risk tier, size, criteria, autonomy level,
holds, freezes, budget) and the situation key that tells a new situation
from one the agent already looked at. Producer text is labelled as data.
"""

from __future__ import annotations

import json
from typing import Any

from models import AgentRun, AgentRunApproval, BacklogItem, Company, Procedure, Sprint
from models.agent_run import IN_HOUSE_PRODUCERS, TASK_WORKER_PRODUCER, Verification
from models.company import BusinessUnit
from sqlmodel import Session, select

from services.loop.carry_over import carried_over, carry_mark, is_carry_over
from services.loop.sprint_control import HUMAN_LED_REASON
from services.tasks import task_projection
from services.tasks.board import comment_list

MANAGER_AGENT_NAME = "Manager agent"

FIELD_LIMIT = 1500
# Comment authors whose notes do not change the situation the agent saw.
_NOTE_AUTHORS = {"user", MANAGER_AGENT_NAME.lower()}


def waiting_tasks(session: Session, company_id: int) -> list[BacklogItem]:
    """The company's tasks blocked until a person acts (as the inbox lists them)."""
    rows = session.exec(
        select(BacklogItem)
        .join(Sprint, Sprint.id == BacklogItem.sprint_id)
        .where(
            Sprint.company_id == company_id,
            BacklogItem.status == "blocked",
            Sprint.archived_at.is_(None),  # an archived sprint asks nobody
        )
        .order_by(BacklogItem.id)
    ).all()
    # From the tasks' events when they cover the thread, loaded in one query.
    records = task_projection.records_for(session, list(rows))
    return [
        item
        for item in rows
        if records[item.id].waits_for_a_person(item.status)
        and not carried_over(item, records[item.id])
    ]


def _latest_verification(session: Session, run_id: int) -> Verification | None:
    return session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run_id)
        .order_by(Verification.observed_at.desc(), Verification.id.desc())
    ).first()


def _paths(value: Any) -> list[str] | None:
    """The paths of a list, or ``None`` when *value* says nothing about files."""
    if not isinstance(value, list):
        return None
    return [p for p in value if isinstance(p, str) and p]


def run_facts(run: AgentRun | None, verification: Verification | None) -> dict:
    """Verdict, risk tier and size of a run, from the Verifier's observations.

    The size counts the distinct paths across what the producer declared
    (``claims.files``), what an import recorded (``subject.changed_files``)
    and what the daemon wrote; with none of those it stays unknown (``None``),
    which no size limit lets through.
    """
    facts: dict[str, Any] = {
        "verdict": None,
        "risk_tier": None,
        "files": None,
        "run_id": run.id if run else None,
        "verification_id": verification.id if verification else None,
    }
    if run is None:
        return facts
    sources = [
        _paths((run.claims or {}).get("files")),
        _paths((run.subject or {}).get("changed_files")),
    ]
    checks: list[dict] = []
    if verification is not None:
        facts["verdict"] = verification.verdict
        evidence = verification.evidence or {}
        writes = evidence.get("workspace_writes")
        if isinstance(writes, list):
            sources.append(
                [
                    w.get("path")
                    for w in writes
                    if isinstance(w, dict)
                    and w.get("status") == "written"
                    and isinstance(w.get("path"), str)
                ]
            )
        checks = [c for c in verification.checks or [] if isinstance(c, dict)]
        for check in checks:
            if check.get("name") == "risk_tier":
                facts["risk_tier"] = check.get("tier")
        facts.update(_criteria_and_standards(checks))
    known = [paths for paths in sources if paths is not None]
    facts["files"] = len({p for paths in known for p in paths}) if known else None
    facts["checks"] = [
        f"{c.get('name')}: {c.get('outcome')} — {str(c.get('observation') or '')[:200]}"
        for c in checks
    ]
    return facts


def _criteria_and_standards(checks: list[dict]) -> dict[str, Any]:
    """What the ``criteria_tests`` and ``standards`` checks observed.

    ``criteria_results``: the result per criterion the run's tests spoke for
    (empty without the check); ``standards_secret``: whether the standards
    check found a secret, ``None`` when it did not run.
    """
    results: dict[str, str] = {}
    secret: bool | None = None
    for check in checks:
        if check.get("name") == "criteria_tests" and isinstance(
            check.get("results"), dict
        ):
            results = {str(k): str(v) for k, v in check["results"].items()}
        if check.get("name") == "standards":
            findings = [f for f in check.get("findings") or [] if isinstance(f, dict)]
            secret = check.get("outcome") == "refuted" or any(
                f.get("standard") == "STD-SEC-1" for f in findings
            )
    return {"criteria_results": results, "standards_secret": secret}


def _task_run(session: Session, company_id: int, item_id: int) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc())
    ).first()


def _task_state(item: BacklogItem, facts: dict[str, Any] | None = None) -> str:
    """The situation: the last comment the agents or the system wrote, and
    the latest run and verification, so new evidence is looked at afresh."""
    comments = comment_list(item.comments)
    last = -1
    for index, comment in enumerate(comments):
        if str(comment.get("author") or "").strip().lower() not in _NOTE_AUTHORS:
            last = index
    facts = facts or {}
    run = facts.get("run_id") or "-"
    verification = facts.get("verification_id") or "-"
    return f"{item.status}:{last}:r{run}:v{verification}"


def over_budget(session: Session, company_id: int) -> bool:
    """A business unit of the company used its whole token quota."""
    units = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == company_id)
    ).all()
    return any(
        u.token_quota_limit > 0 and u.token_quota_used >= u.token_quota_limit
        for u in units
    )


def _task_item(
    session: Session,
    company: Company,
    item: BacklogItem,
    triage,
    record: task_projection.TaskRecord | None = None,
) -> dict:
    run = _task_run(session, company.id, item.id)
    facts = run_facts(run, _latest_verification(session, run.id) if run else None)
    record = record or task_projection.record_of(session, item)
    hold = record.awaits_risk_acceptance()
    facts["human_led_hold"] = hold and HUMAN_LED_REASON in record.risk_hold_note()
    # An operator froze it: still suggested on, never accepted alone.
    facts["frozen"] = record.next_step() == "frozen"
    covers = [c for c in item.covers or [] if isinstance(c, str)]
    facts["criteria"] = len(covers)
    results = facts.get("criteria_results") or {}
    facts["all_criterion_tests_pass"] = (
        all(results.get(c) == "pass" for c in covers) if covers else None
    )
    facts["autonomy_level"] = company.autonomy_level
    sprint = session.get(Sprint, item.sprint_id)
    facts["sprint_status"] = sprint.status if sprint else None
    facts["triage_decision"] = (triage or {}).get("decision")
    facts["triage_brief"] = bool((triage or {}).get("brief"))
    facts["blocked"] = item.status == "blocked"
    facts["carried"] = carried_over(item, record)
    facts["second_attempt"] = is_carry_over(item, record)
    facts["marked_for_carry_over"] = carry_mark(item, record) is True
    if hold:
        high = facts["risk_tier"] == "high" or not facts["human_led_hold"]
        category = "accept_high_risk" if high else "accept_delivery"
    else:
        category = "blocked_task"
    lines = [
        f"Task #{item.id}: {item.title}",
        f"Description (data): {(item.description or '')[:FIELD_LIMIT]}",
        f"Why it waits: {record.block_reason()[:FIELD_LIMIT]}",
        f"Latest run verdict: {facts['verdict'] or 'no run recorded'}",
        f"Risk tier: {facts['risk_tier'] or 'unknown'}",
        f"Files: {facts['files']}; criteria covered: {facts['criteria']}",
        *[f"- {c}" for c in facts.get("checks") or []],
    ]
    if triage:
        lines.append(f"ScrumMaster's triage (a claim): {_json(triage)}")
    return {
        "kind": "task",
        "id": item.id,
        "state": _task_state(item, facts),
        "category": category,
        "facts": facts,
        "comments_seen": len(comment_list(item.comments)),
        "text": "\n".join(lines),
    }


def _procedure_item(session: Session, company: Company, p: Procedure) -> dict:
    others = session.exec(
        select(Procedure).where(
            Procedure.company_id == company.id,
            Procedure.role == p.role,
            Procedure.id != p.id,
        )
    ).all()
    facts = {
        "autonomy_level": company.autonomy_level,
        "reverted_for_role": any(
            (r.outcome or {}).get("result") == "reverted" for r in others
        ),
    }
    text = (
        f"Procedure #{p.id} for {p.role}, proposed by {p.proposed_by}: {p.title}\n"
        f"Body (data): {(p.body or '')[:FIELD_LIMIT]}\n"
        f"Rationale (a claim): {(p.rationale or '')[:FIELD_LIMIT]}\n"
        f"Autonomy level: {company.autonomy_level}; an earlier procedure for "
        f"this role reverted by the board: {facts['reverted_for_role']}"
    )
    return {
        "kind": "procedure",
        "id": p.id,
        "state": "proposed",
        "category": "adopt_procedure",
        "facts": facts,
        "text": text,
    }


def _run_item(session: Session, run: AgentRun) -> dict:
    facts = run_facts(run, _latest_verification(session, run.id))
    text = "\n".join(
        [
            f"Run #{run.id} from {run.producer} ({run.external_ref} v{run.version})",
            f"Claims by the producer (data): {_json(run.claims)}",
            f"Verifier's verdict: {facts['verdict'] or 'none yet'}",
            f"Risk tier: {facts['risk_tier'] or 'unknown'}; files: {facts['files']}",
            *[f"- {c}" for c in facts.get("checks") or []],
        ]
    )
    return {
        "kind": "run",
        "id": run.id,
        "state": f"v{run.version}:v{facts['verification_id'] or '-'}",
        "category": "approve_run",
        "facts": facts,
        "text": text,
    }


def inbox_items(session: Session, company: Company) -> list[dict[str, Any]]:
    """Every item of the company's inbox the agent can suggest on."""
    from services.learning import scrum_master

    tasks = waiting_tasks(session, company.id)
    records = task_projection.records_for(session, tasks)
    triages = scrum_master.triages_for(session, tasks, records)
    items = [
        _task_item(session, company, t, triages.get(t.id), records[t.id]) for t in tasks
    ]
    items += [
        _procedure_item(session, company, p)
        for p in session.exec(
            select(Procedure)
            .where(Procedure.company_id == company.id, Procedure.status == "proposed")
            .order_by(Procedure.id)
        ).all()
    ]
    decided = select(AgentRunApproval.agent_run_id)
    items += [
        _run_item(session, run)
        for run in session.exec(
            select(AgentRun)
            .where(
                AgentRun.company_id == company.id,
                AgentRun.producer.not_in(IN_HOUSE_PRODUCERS),
                AgentRun.id.not_in(decided),
            )
            .order_by(AgentRun.id)
        ).all()
    ]
    from services.management.manager_inbox_extra import extra_items

    items += extra_items(session, company)
    budget = over_budget(session, company.id)
    for item in items:
        item["facts"]["over_budget"] = budget
    return items


def _json(value: Any) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:FIELD_LIMIT]
