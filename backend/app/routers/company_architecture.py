"""The company's architecture and its versions (issue #329, finding F3).

A company has one architecture; each approved revision is a new version, and
the earlier versions stay readable (``services/planning/arch_versions.py``).
These routes read it by company; ``routers/architectures.py`` keeps the
per-version routes (edit, approve, write…), which still answer for every
version id.

Endpoints:
- ``GET  /v1/companies/{company_id}/architecture``                     — the current version, the open draft and the history (Viewer).
- ``GET  /v1/companies/{company_id}/architecture/versions``            — every version, newest first (Viewer).
- ``GET  /v1/companies/{company_id}/architecture/versions/{version}``  — one version, with its check (Viewer).
- ``POST /v1/companies/{company_id}/architecture/revisions``           — the next draft version for an approved specification (Manager).
"""

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Architecture, Company, Specification, User
from schemas import RequestModel
from services.planning import arch_versions
from sqlmodel import Session, select

from routers.architectures import serialize

router = APIRouter(prefix="/v1/companies", tags=["Core"])


class RevisionRequest(RequestModel):
    specification_id: int


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _company(
    session: Session, user: User | None, company_id: int, role: str
) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    require_company_role(session, user, company_id, role)
    return company


def _full(session: Session, arch: Architecture) -> dict:
    spec = session.get(Specification, arch.specification_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    return serialize(session, arch, spec)


@router.get("/{company_id}/architecture")
async def get_company_architecture(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's current architecture (``null`` before one is approved),
    the draft that revises it, and every version as a line of history."""
    _company(session, user, company_id, "Viewer")
    current = arch_versions.current(session, company_id)
    rows = arch_versions.versions(session, company_id)
    draft = next((a for a in rows if a.status == "draft"), None)
    return {
        "company_id": company_id,
        "current": _full(session, current) if current is not None else None,
        "draft": arch_versions.summary(session, draft) if draft is not None else None,
        "versions": [arch_versions.summary(session, a) for a in rows],
    }


@router.get("/{company_id}/architecture/versions")
async def list_versions(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Every version of the company's architecture, newest first."""
    _company(session, user, company_id, "Viewer")
    return {
        "versions": [
            arch_versions.summary(session, a)
            for a in arch_versions.versions(session, company_id)
        ]
    }


@router.get("/{company_id}/architecture/versions/{version}")
async def get_version(
    company_id: int,
    version: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One version by its number, as ``GET /v1/architectures/{id}`` serves it."""
    _company(session, user, company_id, "Viewer")
    arch = session.exec(
        select(Architecture)
        .where(Architecture.company_id == company_id, Architecture.version == version)
        .order_by(Architecture.id.desc())
    ).first()
    if arch is None:
        raise HTTPException(status_code=404, detail="No such version")
    return _full(session, arch)


@router.post("/{company_id}/architecture/revisions", status_code=201)
async def revise_for_specification(
    company_id: int,
    req: RevisionRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The next draft version of the company's architecture, for an approved
    specification of the company: the current version's content, its covers
    in that specification as the draft's own, and the covers of every other
    specification kept. 409 without a current architecture (draft the first
    one with ``POST /v1/architectures``), for a specification not approved,
    or while a revision is open."""
    _company(session, user, company_id, "Manager")
    spec = session.get(Specification, req.specification_id)
    if spec is None or spec.company_id != company_id:
        raise HTTPException(status_code=404, detail="Specification not found")
    if spec.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"The specification is {spec.status}; a version is drafted for an approved one.",
        )
    base = session.exec(
        select(Architecture)
        .where(Architecture.company_id == company_id, Architecture.status == "approved")
        .order_by(Architecture.version.desc(), Architecture.id.desc())
        .with_for_update()
    ).first()
    if base is None:
        raise HTTPException(
            status_code=409,
            detail="The company has no approved architecture to revise yet.",
        )
    open_revision = arch_versions.open_draft(session, company_id)
    if open_revision is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A revision is already in draft.",
                "architecture_id": open_revision.id,
            },
        )
    revision = arch_versions.start_revision(session, base, spec, _actor(user))
    session.add(revision)
    session.commit()
    session.refresh(revision)
    return serialize(session, revision, spec)
