"""Renaming a company, and deleting it with everything it holds.

A company's rows are every row whose ``company_id`` is the company's, and
every row that points at one of those through a foreign key (a sprint's
tasks, say), found from the models' own metadata so a table added later is
covered without a list to keep. They are deleted children first, in one
transaction: either the whole company goes, or nothing does.

Its audit chain goes with it: the chain is kept per company
(``audit_anchors``), so no other company's chain changes. What is deleted is
recorded first in the deployment's own audit row (``company_id`` empty):
the company's id and name, how many rows each table lost, and who asked.

Files are not touched: the project folder, its ``fabrique/*`` branches and
the daemon's caches stay on the computer that holds them (the daemon is the
only component that touches a person's files).

A company with a sprint running or an owner action running is not deleted;
the caller says which (``blockers``).
"""

from __future__ import annotations

from typing import Any

from models import Company, OwnerActionRun, Sprint
from sqlalchemy import Table, delete, func, inspect, or_, select
from sqlmodel import Session, SQLModel

from services.platform.agent_service import AgentService

NAME_MAX = 100
# A sprint in one of these runs work on the company's project now.
RUNNING_SPRINTS = ("active",)


class CompanyRemovalRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _conditions(session: Session, company_id: int) -> list[tuple[Table, Any]]:
    """Each table holding the company's rows, parents first, with the condition that selects them.

    Only tables the database has: a model whose table was never created holds nothing.
    """
    present = set(inspect(session.get_bind()).get_table_names())
    scoped: dict[str, Any] = {}
    out: list[tuple[Table, Any]] = []
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in present:
            continue
        if table.name == "companies":
            scoped[table.name] = table.c.id == company_id
            continue
        parts = []
        if "company_id" in table.c:
            parts.append(table.c.company_id == company_id)
        for fk in table.foreign_keys:
            parent = fk.column.table
            if parent.name in scoped and parent.name != "companies":
                parts.append(
                    fk.parent.in_(select(fk.column).where(scoped[parent.name]))
                )
        if parts:
            condition = or_(*parts) if len(parts) > 1 else parts[0]
            scoped[table.name] = condition
            out.append((table, condition))
    return out


def counts(session: Session, company_id: int) -> dict[str, int]:
    """How many rows each table holds for the company (tables with none left out)."""
    found = {}
    for table, condition in _conditions(session, company_id):
        n = session.execute(
            select(func.count()).select_from(table).where(condition)
        ).scalar_one()
        if n:
            found[table.name] = int(n)
    return found


def blockers(session: Session, company_id: int) -> list[str]:
    """Why the company cannot be deleted now (empty when it can)."""
    found = []
    running = session.execute(
        select(func.count())
        .select_from(Sprint)
        .where(Sprint.company_id == company_id, Sprint.status.in_(RUNNING_SPRINTS))
    ).scalar_one()
    if running:
        found.append("sprint_running")
    action = session.execute(
        select(func.count())
        .select_from(OwnerActionRun)
        .where(
            OwnerActionRun.company_id == company_id, OwnerActionRun.status == "running"
        )
    ).scalar_one()
    if action:
        found.append("owner_action_running")
    return found


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise CompanyRemovalRefused(404, "Company not found")
    return company


def rename(session: Session, company_id: int, name: str, user: Any) -> Company:
    """Give the company a new name (audited ``company_renamed`` in its own chain)."""
    company = _company(session, company_id)
    clean = " ".join((name or "").split())
    if not clean or len(clean) > NAME_MAX:
        raise CompanyRemovalRefused(
            422, f"A company name has 1 to {NAME_MAX} characters"
        )
    before = company.company_name
    if clean == before:
        return company
    company.company_name = clean
    session.add(company)
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}",
        agent_id=f"human:{getattr(user, 'id', None)}",
        action="company_renamed",
        details={"company_id": str(company_id)},
        result={"before": before, "after": clean, "user_id": getattr(user, "id", None)},
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )
    session.commit()
    session.refresh(company)
    return company


def delete_company(
    session: Session, company_id: int, confirm_name: str, user: Any
) -> dict[str, Any]:
    """Delete the company and every row it holds; what was deleted."""
    company = _company(session, company_id)
    if " ".join((confirm_name or "").split()) != company.company_name:
        raise CompanyRemovalRefused(422, "Type the company's name exactly to delete it")
    held = blockers(session, company_id)
    if held:
        raise CompanyRemovalRefused(
            409, f"The company cannot be deleted now: {', '.join(held)}"
        )
    deleted = counts(session, company_id)
    summary = {
        "company_id": company_id,
        "company_name": company.company_name,
        "rows": deleted,
        "user_id": getattr(user, "id", None),
    }
    for table, condition in reversed(_conditions(session, company_id)):
        session.exec(delete(table).where(condition))
    session.exec(delete(Company.__table__).where(Company.__table__.c.id == company_id))
    # The deployment's record, outside the chain that was just deleted.
    AgentService().audit_interaction(
        session=session,
        company_id=None,
        interaction_id=f"company:{company_id}",
        agent_id=f"human:{getattr(user, 'id', None)}",
        action="company_deleted",
        details={"company_id": str(company_id)},
        result=summary,
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )
    session.commit()
    return summary
