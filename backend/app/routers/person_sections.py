"""Person sections router — what only a person can write in a project's documentation pack.

Endpoints (issue #530, phase 2, ``docs/archive/design/PROJECT_DOCUMENTATION.md``):
- ``GET    /v1/companies/{id}/documentation/person-sections``                   — every person section and its current statement (Viewer).
- ``GET    /v1/companies/{id}/documentation/person-sections/{section}/history`` — every revision of one section, newest first (Viewer).
- ``PUT    /v1/companies/{id}/documentation/person-sections/{section}``         — write the section's statement (Manager; audited).
- ``DELETE /v1/companies/{id}/documentation/person-sections/{section}``         — withdraw it (Manager; audited).

A section is one of ``project_docs.statements.SECTIONS`` (``charter.budget``,
``risks.appetite``…); it is checked after the caller's role, so an unknown
one is a 404 to a member and a 403 to anyone else. A statement is a claim by
its author, kept in its own table (``PersonStatement``) and quoted by the next
compiled pack with the author's company role and time; the author's address
is recorded here and in the audit chain, never in the pack. The backend
computes ``statement_sha256``; nothing in the request names a digest, an
author or a time.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, PersonStatement, User
from pydantic import Field
from schemas import RequestModel
from services.planning.project_docs import collect, statements
from services.planning.project_docs.refs import digest
from services.platform import teams
from services.platform.agent_service import AgentService
from sqlmodel import Session, select

router = APIRouter(tags=["Core"])

HISTORY_LIMIT = 100


class StatementRequest(RequestModel):
    text: str = Field(min_length=1, max_length=statements.TEXT_LIMIT)


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _company(
    session: Session, user: User | None, company_id: int, role: str
) -> Company:
    query = select(Company).where(Company.id == company_id)
    if role != "Viewer":
        # One write at a time per company: the current row is read, then replaced.
        query = query.with_for_update()
    company = session.exec(query).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    require_company_role(session, user, company.id, role)
    return company


def _section(section: str) -> str:
    if section not in statements.SECTIONS:
        raise HTTPException(status_code=404, detail="No such person section")
    return section


def _author_role(session: Session, user: User | None, company_id: int) -> str:
    """The caller's effective company role now (a token's own role for a token)."""
    if user is None:
        return "Owner"
    token_role = getattr(user, "_api_token_role", None)
    if token_role is not None:
        return str(token_role)
    return teams.member_role(session, company_id, user.id) or "Manager"


def _statement(row: PersonStatement) -> dict[str, Any]:
    return {
        "id": row.id,
        "section": row.section,
        "text": row.text,
        "withdrawn": row.text is None,
        # A claim by this person: who wrote it, in which role, and when.
        "author": row.recorded_by,
        "author_role": row.author_role,
        "recorded_at": row.created_at.isoformat() if row.created_at else None,
        "supersedes_id": row.supersedes_id,
        "statement_sha256": row.statement_sha256,
    }


def _current(session: Session, company_id: int, section: str) -> PersonStatement | None:
    return session.exec(
        select(PersonStatement)
        .where(
            PersonStatement.company_id == company_id,
            PersonStatement.section == section,
        )
        .order_by(PersonStatement.id.desc())
    ).first()


def _record(
    session: Session,
    user: User | None,
    company: Company,
    section: str,
    text: str | None,
) -> PersonStatement:
    """A new row for *section*, its digest and its audit entry; the caller commits."""
    previous = _current(session, company.id, section)
    row = PersonStatement(
        company_id=company.id,
        section=section,
        text=text,
        author_role=_author_role(session, user, company.id),
        recorded_by=_actor(user),
        recorded_by_user_id=user.id if user is not None else None,
        supersedes_id=previous.id if previous is not None else None,
    )
    row.statement_sha256 = digest(
        {
            "company_id": row.company_id,
            "section": row.section,
            "text": row.text,
            "author_role": row.author_role,
            "recorded_by": row.recorded_by,
            "supersedes_id": row.supersedes_id,
            "created_at": row.created_at.isoformat(),
        }
    )
    session.add(row)
    session.flush()
    AgentService().audit_interaction(
        session=session,
        company_id=company.id,
        interaction_id=f"person-statement:{row.id}",
        agent_id=f"human:{row.recorded_by}",
        action="person_statement_recorded"
        if text is not None
        else "person_statement_withdrawn",
        details={
            "section": section,
            "statement_id": row.id,
            "supersedes_id": row.supersedes_id,
            "author_role": row.author_role,
        },
        result={
            "statement_sha256": row.statement_sha256,
            "characters": len(text or ""),
        },
        decision_signature=None,
        cost_tokens=0,
    )
    return row


@router.get("/v1/companies/{company_id}/documentation/person-sections")
async def list_sections(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Every person section, in the pack's order, with its current statement (or None)."""
    company = _company(session, user, company_id, "Viewer")
    current = {r.section: r for r in collect.current_statements(session, company.id)}
    return {
        "company_id": company.id,
        "text_limit": statements.TEXT_LIMIT,
        "sections": [
            {
                "key": key,
                "document": document,
                "statement": _statement(current[key])
                if key in current and current[key].text is not None
                else None,
            }
            for key, (document, _, _) in statements.SECTIONS.items()
        ],
    }


@router.get(
    "/v1/companies/{company_id}/documentation/person-sections/{section}/history"
)
async def section_history(
    company_id: int,
    section: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Every revision of one section, withdrawals included, newest first."""
    company = _company(session, user, company_id, "Viewer")
    key = _section(section)
    rows = session.exec(
        select(PersonStatement)
        .where(PersonStatement.company_id == company.id, PersonStatement.section == key)
        .order_by(PersonStatement.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    return {"section": key, "revisions": [_statement(r) for r in rows]}


@router.put("/v1/companies/{company_id}/documentation/person-sections/{section}")
async def write_section(
    company_id: int,
    section: str,
    req: StatementRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An Owner or a Manager writes a section; a pack compiled before it turns stale.

    The text is trimmed; 422 when nothing is left.
    """
    company = _company(session, user, company_id, "Manager")
    key = _section(section)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="The statement is empty")
    row = _record(session, user, company, key, text)
    session.commit()
    session.refresh(row)
    return _statement(row)


@router.delete("/v1/companies/{company_id}/documentation/person-sections/{section}")
async def withdraw_section(
    company_id: int,
    section: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An Owner or a Manager withdraws a section's statement; the history keeps it.

    409 when the section has no statement to withdraw.
    """
    company = _company(session, user, company_id, "Manager")
    key = _section(section)
    previous = _current(session, company.id, key)
    if previous is None or previous.text is None:
        raise HTTPException(
            status_code=409, detail="The section has no statement to withdraw"
        )
    row = _record(session, user, company, key, None)
    session.commit()
    session.refresh(row)
    return _statement(row)
