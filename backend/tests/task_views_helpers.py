"""What the tests of saved task views share (issue #535): a company, a sprint,
a task, a run with its verification, and the ids of a filtered result.
The tests are ``test_task_views.py``, ``test_task_view_filter.py`` and
``test_task_view_budget.py``.
"""

from __future__ import annotations

from uuid import uuid4

from models import (
    AgentRun,
    BacklogItem,
    Company,
    Sprint,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from sqlmodel import Session, select

WAITS = [{"author": "System", "text": "Blocked: manual triage needed."}]


def _company(session: Session) -> Company:
    company = Company(company_name=f"Views {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    return company


def _sprint(session: Session, company: Company, name: str = "S") -> Sprint:
    sprint = Sprint(company_id=company.id, sprint_name=name)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session: Session, sprint: Sprint, **fields) -> BacklogItem:
    fields.setdefault("title", f"Task {uuid4().hex[:4]}")
    item = BacklogItem(sprint_id=sprint.id, **fields)
    session.add(item)
    session.commit()
    return item


def _run(session, company, item, verdict, tier=None, producer=TASK_WORKER_PRODUCER):
    earlier = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).all()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=len(earlier) + 1,
        producer=producer,
        artifact_digest="0" * 64,
        artifact_size=1,
        # What the producer said of its own work: never read as the verdict.
        claims={"verdict": "verified", "risk": {"tier": "low"}},
    )
    session.add(run)
    session.commit()
    checks = [{"name": "risk_tier", "tier": tier}] if tier else []
    session.add(
        Verification(
            company_id=company.id, agent_run_id=run.id, verdict=verdict, checks=checks
        )
    )
    session.commit()
    return run


def _ids(result) -> list[int]:
    return [t["id"] for t in result["tasks"]]
