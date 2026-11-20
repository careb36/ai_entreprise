"""A company's own standards, imported from its repository, and the
time-boxed exceptions to a standard (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §7, §11.3, §12).

- ``GET  /v1/companies/{id}/standards``                          the catalogue, the set in force, the exceptions in force (Viewer)
- ``GET  /v1/companies/{id}/standards/versions``                 history (Viewer)
- ``GET  /v1/companies/{id}/standards/versions/{v}``             one version with its standards and dry run (Viewer)
- ``POST /v1/companies/{id}/standards/import``                   read the file at the default branch's head: a draft (Manager; 202, 409 without the daemon)
- ``POST /v1/companies/{id}/standards/versions/{v}/activate``    put a draft in force (a signed-in Owner, not the importer)
- ``POST /v1/companies/{id}/standards/versions/{v}/reject``      reject a draft (a signed-in Owner)
- ``POST /v1/companies/{id}/standards/deactivate``               the catalogue alone (a signed-in Owner)
- ``GET  /v1/companies/{id}/standards/exceptions``               every exception (Viewer)
- ``POST /v1/companies/{id}/standards/exceptions``               request one (a signed-in Owner)
- ``POST /v1/companies/{id}/standards/exceptions/{e}/approve``   a second Owner approves
- ``POST /v1/companies/{id}/standards/exceptions/{e}/confirm``   the only Owner confirms their own, flagged
- ``POST /v1/companies/{id}/standards/exceptions/{e}/reject``    reject, or withdraw one's own
- ``POST /v1/companies/{id}/standards/exceptions/{e}/revoke``    end one in force

``GET /v1/standards`` stays the catalogue. Every change is a signed-in
human Owner's (an API token is refused like a Manager), never the Manager
agent's (``change_standards`` is in ``ALWAYS_ESCALATE``).
"""

from __future__ import annotations

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from models import Company, User
from pydantic import Field
from schemas import RequestModel
from services.evidence import standards as catalogue
from services.planning import company_standards_import as importer
from services.platform import company_standards_setting as setting
from services.platform import sod
from services.platform import standard_exceptions as exceptions
from services.workspace import sandbox
from sqlmodel import Session

from routers.sod import owner_person

router = APIRouter(prefix="/v1", tags=["Company"])


class Activation(RequestModel):
    single_owner_reason: str | None = Field(default=None, max_length=1000)


class ExceptionRequest(RequestModel):
    standard_id: str = Field(min_length=3, max_length=64)
    reason: str = Field(min_length=10, max_length=1000)
    compensating_control: str = Field(min_length=10, max_length=1000)
    days: int = Field(ge=1, le=exceptions.MAX_DAYS)


class SingleOwner(RequestModel):
    reason: str = Field(min_length=10, max_length=500)


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)  # type: ignore[attr-defined]


@router.get("/companies/{company_id}/standards")
def get_standards(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The catalogue, the company's set in force (none: the catalogue alone)
    and the exceptions in force."""
    require_company_role(session, user, company_id, "Viewer")
    row = setting.in_force(session, company_id)
    return {
        "company_id": company_id,
        "catalogue": catalogue.catalogue(),
        "in_force": setting.serialize(row) if row else None,
        "exceptions": [
            exceptions.serialize(r) for r in exceptions.in_force(session, company_id)
        ],
    }


@router.get("/companies/{company_id}/standards/versions")
def get_standard_versions(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    return {
        "versions": [
            setting.serialize(r, full=False)
            for r in setting.versions(session, company_id)
        ]
    }


@router.get("/companies/{company_id}/standards/versions/{version}")
def get_standard_version(
    company_id: int,
    version: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    try:
        return setting.serialize(setting.find(session, company_id, version))
    except setting.StandardsRefused as exc:
        raise _refused(exc) from exc


@router.post("/companies/{company_id}/standards/import", status_code=202)
async def import_standards(
    company_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Read ``.fabrique/standards.toml`` at the default branch's head: a draft
    with its refusals and dry run. Nothing in force changes."""
    require_company_role(session, user, company_id, "Manager")
    company = _company(session, company_id)
    person = sod.actor_of(session, user) or sod.Person()
    try:
        row = await importer.import_draft(
            session,
            getattr(request.app.state, "nats_client", None),
            company,
            by=person.email or getattr(user, "email", None) or "unknown",
            user_id=person.user_id,
        )
    except importer.ImportUnavailable as exc:
        raise _refused(exc) from exc
    return JSONResponse(status_code=202, content=setting.serialize(row))


@router.post("/companies/{company_id}/standards/versions/{version}/activate")
def activate_standards(
    company_id: int,
    version: int,
    payload: Activation | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Put a draft in force: a signed-in Owner who did not import it (four-eyes)."""
    actor = owner_person(session, user, company_id)
    company = _company(session, company_id)
    try:
        row = setting.activate(
            session,
            company_id,
            version,
            actor,
            company_sandbox=sandbox.mode_for(company),
            single_owner_reason=payload.single_owner_reason if payload else None,
        )
    except setting.StandardsRefused as exc:
        raise _refused(exc) from exc
    return setting.serialize(row)


@router.post("/companies/{company_id}/standards/versions/{version}/reject")
def reject_standards(
    company_id: int,
    version: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = owner_person(session, user, company_id)
    try:
        return setting.serialize(
            setting.reject(session, company_id, version, actor), full=False
        )
    except setting.StandardsRefused as exc:
        raise _refused(exc) from exc


@router.post("/companies/{company_id}/standards/deactivate")
def deactivate_standards(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = owner_person(session, user, company_id)
    row = setting.deactivate(session, company_id, actor)
    return {
        "company_id": company_id,
        "deactivated": setting.serialize(row, full=False) if row else None,
    }


@router.get("/companies/{company_id}/standards/exceptions")
def get_standard_exceptions(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    require_company_role(session, user, company_id, "Viewer")
    return {
        "exceptions": [
            exceptions.serialize(r) for r in exceptions.listing(session, company_id)
        ]
    }


@router.post("/companies/{company_id}/standards/exceptions", status_code=201)
def request_standard_exception(
    company_id: int,
    payload: ExceptionRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    actor = owner_person(session, user, company_id)
    try:
        row = exceptions.request(
            session,
            company_id,
            actor,
            standard_id=payload.standard_id,
            reason=payload.reason,
            compensating_control=payload.compensating_control,
            days=payload.days,
        )
    except exceptions.ExceptionRefused as exc:
        raise _refused(exc) from exc
    return exceptions.serialize(row)


def _decide(
    action,
    company_id: int,
    exception_id: int,
    session: Session,
    user: User | None,
    *args,
):
    actor = owner_person(session, user, company_id)
    try:
        return exceptions.serialize(
            action(session, company_id, exception_id, actor, *args)
        )
    except exceptions.ExceptionRefused as exc:
        raise _refused(exc) from exc


@router.post("/companies/{company_id}/standards/exceptions/{exception_id}/approve")
def approve_standard_exception(
    company_id: int,
    exception_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    return _decide(exceptions.approve, company_id, exception_id, session, user)


@router.post("/companies/{company_id}/standards/exceptions/{exception_id}/confirm")
def confirm_standard_exception(
    company_id: int,
    exception_id: int,
    payload: SingleOwner,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    return _decide(
        exceptions.confirm_single_owner,
        company_id,
        exception_id,
        session,
        user,
        payload.reason,
    )


@router.post("/companies/{company_id}/standards/exceptions/{exception_id}/reject")
def reject_standard_exception(
    company_id: int,
    exception_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    return _decide(exceptions.reject, company_id, exception_id, session, user)


@router.post("/companies/{company_id}/standards/exceptions/{exception_id}/revoke")
def revoke_standard_exception(
    company_id: int,
    exception_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    return _decide(exceptions.revoke, company_id, exception_id, session, user)
