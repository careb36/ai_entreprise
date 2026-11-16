"""What the tests of the Manager agent share (issue #331).

The fixed answer, a company, a task held for a person with its run and
verification, a policy, one cycle with a scripted model, the decisions it
wrote, an outside run and its approvals, and members with their bearer
headers. The tests are ``test_manager_agent*.py``.
"""

from __future__ import annotations

import secrets

import auth_tokens
import pytest
from config import settings
from models import AgentRun, AgentRunApproval, BacklogItem, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, Verification
from models.company import Company, CompanyMember, User
from models.manager import ManagerDecision, ManagerPolicy
from services.loop.sprint_control import HUMAN_LED_REASON
from services.management import manager_agent
from services.tasks.board import RISK_HOLD_PREFIX
from sqlmodel import Session, select


def answer(decision: str, category: str, reason: str = "The run is verified.") -> str:
    return f"DECISION: {decision}\nCATEGORY: {category}\nREASON:\n{reason}\nEND"


def _company(session: Session, autonomy: int = 1) -> Company:
    company = Company(
        company_name=f"Client {secrets.token_hex(3)}", autonomy_level=autonomy
    )
    session.add(company)
    session.commit()
    return company


def _hold(
    session: Session,
    company: Company,
    *,
    human_led: bool = True,
    verdict: str = "verified",
    tier: str = "low",
    files: int = 2,
    covers: list | None = None,
) -> BacklogItem:
    """A task QA approved that waits for a person, with its verified run."""
    sprint = Sprint(company_id=company.id, sprint_name="S1", status="active")
    session.add(sprint)
    session.commit()
    reason = HUMAN_LED_REASON if human_led else "authentication: app/auth.py"
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Add the greeting",
        assignee="Developer",
        status="blocked",
        covers=covers,
        comments=[
            {"author": "Developer", "text": "```python path=app/hi.py\nprint(1)\n```"},
            {"author": "QA", "text": "APPROVED: looks right."},
            {
                "author": "System",
                "text": f"{RISK_HOLD_PREFIX} ({reason}). Mark it done to accept it.",
            },
        ],
    )
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id},
        claims={"files": [f"app/f{i}.py" for i in range(files)]},
        artifact_digest=secrets.token_hex(8),
        artifact_size=10,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": tier}],
        )
    )
    session.commit()
    return item


def _policy(session: Session, company: Company, *rules: str, **limits) -> None:
    session.add(ManagerPolicy(company_id=company.id, rules=list(rules), **limits))
    session.commit()


def _cycle(session: Session, company: Company, reply) -> int:
    calls = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return reply(prompt) if callable(reply) else reply

    written = manager_agent.company_cycle(session, company, ask)
    _cycle.prompts = calls
    return written


def _rows(session: Session, company: Company) -> list[ManagerDecision]:
    return session.exec(
        select(ManagerDecision)
        .where(ManagerDecision.company_id == company.id)
        .order_by(ManagerDecision.id)
    ).all()


def _outside_run(session, company, verdict="verified", tier="low"):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"pr-{secrets.token_hex(3)}",
        producer="github",
        claims={"files": ["README.md"]},
        artifact_digest=secrets.token_hex(8),
        artifact_size=10,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "risk_tier", "outcome": "pass", "tier": tier}],
        )
    )
    session.commit()
    return run


def _approvals(session, run):
    return session.exec(
        select(AgentRunApproval)
        .where(AgentRunApproval.agent_run_id == run.id)
        .order_by(AgentRunApproval.id)
    ).all()


@pytest.fixture()
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session: Session, company_id: int | None, role: str) -> dict[str, str]:
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(5)}@example.com",
        full_name=role,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}
