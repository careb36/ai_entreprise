"""Specifications router — what the company's agents are asked to build.

Endpoints (``/v1/specifications``):
- ``GET    /``                        — the company's specifications (Viewer).
- ``GET    /{id}``                    — one, with its check and coverage (Viewer).
- ``POST   /``                        — a person writes a draft (Manager).
- ``PUT    /{id}``                    — edit a draft; an approved one is revised (Manager).
- ``POST   /draft``                   — the PO drafts, or revises, from a brief (Manager);
  a new draft's brief is kept in the intake queue, accepted and linked to it.
- ``POST   /{id}/questions/{index}``  — answer one of the PO's questions (Manager).
- ``POST   /{id}/approve``            — approve; refused while the check blocks (Manager).
- ``POST   /{id}/revise``             — a new draft version of an approved one (Manager).

The ids of requirements and criteria come from ``services.planning.spec_check``,
never from a request or a model; the check decides what can be approved.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from models import Architecture, ProjectDiscovery, Specification, User
from pydantic import Field
from schemas import RequestModel
from services.loop import sod_requesters
from services.planning import (
    document_approval,
    intake_queue,
    spec_author,
    spec_check,
    spec_diff,
    spec_impact,
    spec_planning,
)
from services.platform import sod
from sqlmodel import Session, select, text

router = APIRouter(prefix="/v1/specifications", tags=["Core"])


class SpecificationBody(RequestModel):
    title: str = Field(min_length=1, max_length=spec_check.TITLE_LIMIT)
    summary: str = ""
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    quality: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SpecificationCreate(SpecificationBody):
    company_id: int


class DraftRequest(RequestModel):
    company_id: int
    # Empty on a revision: the answered questions are what the PO works from.
    brief: str = Field(default="", max_length=spec_author.BRIEF_LIMIT)
    # A draft to revise with its answered questions; omitted, a new one.
    specification_id: int | None = None


class AnswerRequest(RequestModel):
    answer: str = Field(max_length=spec_check.TEXT_LIMIT)


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _now() -> datetime:
    return datetime.now(UTC)


def _architecture_version(session: Session | None, spec: Specification) -> int | None:
    if session is None or spec.architecture_id is None:
        return None
    arch = session.get(Architecture, spec.architecture_id)
    return arch.version if arch is not None else None


def serialize(spec: Specification, session: Session | None = None) -> dict[str, Any]:
    """The specification with its check; with *session*, its coverage too."""
    content = spec_planning.spec_content(spec)
    out = {
        "id": spec.id,
        "company_id": spec.company_id,
        "title": spec.title,
        "summary": spec.summary,
        "status": spec.status,
        "version": spec.version,
        "supersedes_id": spec.supersedes_id,
        # The version of the company's architecture it was planned against.
        "architecture_id": spec.architecture_id,
        "architecture_version": _architecture_version(session, spec),
        "requirements": content["requirements"],
        "quality": content["quality"],
        "constraints": content["constraints"],
        "questions": content["questions"],
        "sources": spec.sources or [],
        "drafted_by": spec.drafted_by,
        "approved_by": spec.approved_by,
        "approved_at": spec.approved_at.isoformat() if spec.approved_at else None,
        "created_at": spec.created_at.isoformat() if spec.created_at else None,
        "updated_at": spec.updated_at.isoformat() if spec.updated_at else None,
        "check": spec_check.check(content),
    }
    if session is not None:
        out["coverage"] = spec_planning.coverage(session, spec)
    return out


def _load(
    session: Session, user: User | None, spec_id: int, role: str
) -> Specification:
    query = select(Specification).where(Specification.id == spec_id)
    if role == "Manager":
        # A Manager's operation checks the status it then changes: the row is
        # locked until the commit, so two replicas cannot approve and edit at once.
        query = query.with_for_update()
    spec = session.exec(query).first()
    if spec is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    require_company_role(session, user, spec.company_id, role)
    return spec


def _apply(spec: Specification, content: dict[str, Any], by: str) -> None:
    normalized = spec_check.normalize(content)
    spec.title = normalized["title"]
    spec.summary = normalized["summary"]
    spec.requirements = normalized["requirements"]
    spec.quality = normalized["quality"]
    spec.constraints = normalized["constraints"]
    spec.questions = normalized["questions"]
    spec.drafted_by = by
    spec.updated_at = _now()


def _only_draft(spec: Specification) -> None:
    if spec.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"The specification is {spec.status}; revise it to get a new draft.",
        )


@router.get("")
async def list_specifications(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's specifications, newest first."""
    require_company_role(session, user, company_id, "Viewer")
    rows = session.exec(
        select(Specification)
        .where(Specification.company_id == company_id)
        .order_by(Specification.id.desc())
    ).all()
    return {"specifications": [serialize(spec) for spec in rows]}


@router.get("/{spec_id}")
async def get_specification(
    spec_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    spec = _load(session, user, spec_id, "Viewer")
    return serialize(spec, session)


@router.post("", status_code=201)
async def create_specification(
    req: SpecificationCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person writes a draft; it is numbered and checked like the PO's."""
    require_company_role(session, user, req.company_id, "Manager")
    spec = Specification(company_id=req.company_id, title=req.title)
    _apply(spec, req.model_dump(exclude={"company_id"}), _actor(user))
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return serialize(spec, session)


@router.put("/{spec_id}")
async def update_specification(
    spec_id: int,
    req: SpecificationBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Edit a draft. The questions keep their answers; the ids are reassigned."""
    spec = _load(session, user, spec_id, "Manager")
    _only_draft(spec)
    _apply(spec, {**req.model_dump(), "questions": spec.questions}, _actor(user))
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return serialize(spec, session)


@router.post("/draft")
async def draft_specification(
    req: DraftRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The PO drafts a specification from the brief, or revises the draft named.

    The answer is a claim: it is numbered and checked, and a person approves
    it. 502 when the PO gave nothing usable; the draft named is left as it was.
    """
    require_company_role(session, user, req.company_id, "Manager")
    if not req.brief.strip() and req.specification_id is None:
        raise HTTPException(
            status_code=422, detail="A brief is required for a new draft"
        )
    existing = None
    if req.specification_id is not None:
        existing = _load(session, user, req.specification_id, "Manager")
        if existing.company_id != req.company_id:
            raise HTTPException(status_code=404, detail="Specification not found")
        _only_draft(existing)
    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == req.company_id)
        .order_by(text("created_at DESC"))
    ).first()
    # What the PO is asked is read here, with the request's session; only the
    # call to the proxy runs in a thread (a session is not shared).
    request = spec_author.prepare(
        session,
        req.company_id,
        req.brief,
        discovery.detected_stack if discovery else None,
        existing,
    )
    try:
        content = await asyncio.to_thread(spec_author.draft_from, request)
    except spec_author.SpecAuthorError as exc:
        raise HTTPException(
            status_code=502, detail=f"The PO gave no usable draft: {exc}"
        ) from exc
    spec = existing or Specification(company_id=req.company_id, title=content["title"])
    _apply(spec, content, spec_author.PO_ROLE)
    spec.sources = content.get("sources") or []
    session.add(spec)
    if existing is None:
        session.flush()
        # The client's brief enters the intake queue accepted, linked to the
        # draft it became (issue #532): the request keeps its outcome.
        intake_queue.record_brief(
            session, req.company_id, req.brief, spec, _actor(user)
        )
    session.commit()
    session.refresh(spec)
    return serialize(spec, session)


@router.post("/{spec_id}/questions/{index}")
async def answer_question(
    spec_id: int,
    index: int,
    req: AnswerRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person answers the PO's question *index* (0-based); an empty answer reopens it."""
    spec = _load(session, user, spec_id, "Manager")
    _only_draft(spec)
    questions = list(spec.questions or [])
    if not 0 <= index < len(questions):
        raise HTTPException(status_code=404, detail="No such question")
    answer = " ".join(req.answer.split())
    questions[index] = {**questions[index], "answer": answer or None}
    spec.questions = questions
    spec.updated_at = _now()
    session.add(spec)
    session.commit()
    session.refresh(spec)
    return serialize(spec, session)


@router.post("/{spec_id}/approve")
async def approve_specification(
    spec_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Approve a draft; the version it revises becomes superseded.

    409 with the findings while the check blocks: a requirement without a
    criterion, a target without a number, a question without an answer.
    """
    spec = _load(session, user, spec_id, "Manager")
    _only_draft(spec)
    # Segregation of duties (#543): who drafted or revised it does not approve it.
    try:
        sod.enforce(
            session,
            spec.company_id,
            sod.APPROVE_SPEC,
            {"kind": "specification", "id": spec.id, "version": spec.version},
            sod.actor_of(session, user),
            sod_requesters.of_specification(session, spec),
        )
    except sod.SodRefused as refused:
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    try:
        document_approval.approve_specification(session, spec, _actor(user))
    except document_approval.ApprovalRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    session.commit()
    session.refresh(spec)
    return serialize(spec, session)


@router.get("/{spec_id}/diff")
async def diff_specification(
    spec_id: int,
    against: int | None = Query(default=None),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Deterministic diff between *spec_id* and another version.

    ``against`` is the other specification's id; when omitted, the version
    this one supersedes (its previous approved version) is used.
    """
    spec = _load(session, user, spec_id, "Viewer")
    other_id = against if against is not None else spec.supersedes_id
    if other_id is None:
        raise HTTPException(
            status_code=409, detail="No previous version to diff against"
        )
    other = session.get(Specification, other_id)
    if other is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    require_company_role(session, user, other.company_id, "Viewer")
    # Diff the older version against the newer one: older -> newer.
    if (other.version or 0) <= (spec.version or 0):
        d = spec_diff.diff_specs(other, spec)
        from_version, to_version = other.version, spec.version
    else:
        d = spec_diff.diff_specs(spec, other)
        from_version, to_version = spec.version, other.version
    return {
        "specification_id": spec.id,
        "against_id": other.id,
        "from_version": from_version,
        "to_version": to_version,
        "diff": d,
    }


@router.get("/{spec_id}/impact")
async def specification_impact(
    spec_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The tasks and modules affected by this revision's approval."""
    spec = _load(session, user, spec_id, "Viewer")
    return spec_impact.impact(session, spec)


@router.post("/{spec_id}/revise", status_code=201)
async def revise_specification(
    spec_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A new draft, version + 1, with the approved content to start from."""
    spec = _load(session, user, spec_id, "Manager")
    if spec.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Only an approved specification is revised; this one is {spec.status}.",
        )
    open_revision = session.exec(
        select(Specification).where(
            Specification.supersedes_id == spec.id, Specification.status == "draft"
        )
    ).first()
    if open_revision is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A revision is already in draft.",
                "specification_id": open_revision.id,
            },
        )
    revision = Specification(
        company_id=spec.company_id,
        title=spec.title,
        summary=spec.summary,
        version=spec.version + 1,
        supersedes_id=spec.id,
        requirements=list(spec.requirements or []),
        quality=list(spec.quality or []),
        constraints=list(spec.constraints or []),
        questions=[],
        drafted_by=_actor(user),
    )
    session.add(revision)
    session.commit()
    session.refresh(revision)
    return serialize(revision, session)
