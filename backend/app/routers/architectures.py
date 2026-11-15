"""Architectures router — how an approved specification is built.

Endpoints (``/v1/architectures``):
- ``GET    /?specification_id=``      — the architectures of a specification (Viewer).
- ``GET    /{id}``                    — one, with its check (Viewer).
- ``GET    /{id}/document``           — it as ``docs/ARCHITECTURE.md`` (Viewer).
- ``GET    /{id}/adrs``               — its decisions as ``docs/adr/`` files (Viewer).
- ``POST   /``                        — a person writes a draft (Manager).
- ``PUT    /{id}``                    — edit a draft (Manager).
- ``POST   /draft``                   — the Architect drafts, or revises (Manager).
- ``POST   /{id}/questions/{index}``  — answer one of the Architect's questions (Manager).
- ``POST   /{id}/approve``            — approve; refused while the check blocks (Manager).
- ``POST   /{id}/revise``             — a new draft version of an approved one (Manager).
- ``POST   /{id}/write``              — write it into the repository through the daemon (Manager).

The ids of modules, interfaces and decisions come from ``services.planning.arch_check``,
never from a request or a model; the check decides what can be approved.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from models import Architecture, Specification, User
from pydantic import Field
from schemas import RequestModel
from services.loop import sod_requesters
from services.planning import adr_docs, arch_author, arch_check, document_approval
from services.platform import sod
from services.tools import repo_map
from services.workspace.workspace_daemon import DaemonError, DaemonUnavailable
from sqlmodel import Session, select

router = APIRouter(prefix="/v1/architectures", tags=["Core"])

WRITE_TIMEOUT_SECONDS = 30.0


class ArchitectureBody(RequestModel):
    title: str = Field(min_length=1, max_length=arch_check.TITLE_LIMIT)
    overview: str = ""
    modules: list[dict[str, Any]] = Field(default_factory=list)
    interfaces: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)


class ArchitectureCreate(ArchitectureBody):
    specification_id: int


class DraftRequest(RequestModel):
    specification_id: int
    # A draft to revise with its answered questions; omitted, a new one.
    architecture_id: int | None = None


class AnswerRequest(RequestModel):
    answer: str = Field(max_length=arch_check.TEXT_LIMIT)


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize(arch: Architecture, spec: Specification) -> dict[str, Any]:
    content = arch_author.content_of(arch)
    return {
        "id": arch.id,
        "company_id": arch.company_id,
        "specification_id": arch.specification_id,
        "title": arch.title,
        "overview": arch.overview,
        "status": arch.status,
        "version": arch.version,
        "supersedes_id": arch.supersedes_id,
        "modules": content["modules"],
        "interfaces": content["interfaces"],
        "decisions": content["decisions"],
        "conventions": content["conventions"],
        "questions": content["questions"],
        "sources": arch.sources or [],
        "drafted_by": arch.drafted_by,
        "approved_by": arch.approved_by,
        "approved_at": _iso(arch.approved_at),
        "written_path": arch.written_path,
        "written_sha256": arch.written_sha256,
        "written_at": _iso(arch.written_at),
        "created_at": _iso(arch.created_at),
        "updated_at": _iso(arch.updated_at),
        "check": arch_check.check(content, arch_author.requirement_ids(spec)),
    }


def _specification(
    session: Session, user: User | None, spec_id: int, role: str
) -> Specification:
    spec = session.get(Specification, spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    require_company_role(session, user, spec.company_id, role)
    return spec


def _load(
    session: Session, user: User | None, arch_id: int, role: str
) -> tuple[Architecture, Specification]:
    query = select(Architecture).where(Architecture.id == arch_id)
    if role == "Manager":
        # A Manager's operation checks the status it then changes: the row is
        # locked until the commit, so two replicas cannot approve and edit at
        # once, and two revisions of one approved architecture serialize on it.
        query = query.with_for_update()
    arch = session.exec(query).first()
    if arch is None:
        raise HTTPException(status_code=404, detail="Architecture not found")
    require_company_role(session, user, arch.company_id, role)
    spec = session.get(Specification, arch.specification_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Specification not found")
    return arch, spec


def _apply(arch: Architecture, content: dict[str, Any], by: str) -> None:
    normalized = arch_check.normalize(content)
    arch.title = normalized["title"]
    arch.overview = normalized["overview"]
    arch.modules = normalized["modules"]
    arch.interfaces = normalized["interfaces"]
    arch.decisions = normalized["decisions"]
    arch.conventions = normalized["conventions"]
    arch.questions = normalized["questions"]
    arch.drafted_by = by
    arch.updated_at = _now()


def _only_draft(arch: Architecture) -> None:
    if arch.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"The architecture is {arch.status}; revise it to get a new draft.",
        )


def _saved(session: Session, arch: Architecture, spec: Specification) -> dict[str, Any]:
    session.add(arch)
    session.commit()
    session.refresh(arch)
    return serialize(arch, spec)


@router.get("")
async def list_architectures(
    specification_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The architectures of a specification, newest first."""
    spec = _specification(session, user, specification_id, "Viewer")
    rows = session.exec(
        select(Architecture)
        .where(Architecture.specification_id == spec.id)
        .order_by(Architecture.id.desc())
    ).all()
    return {"architectures": [serialize(arch, spec) for arch in rows]}


@router.get("/{arch_id}")
async def get_architecture(
    arch_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    arch, spec = _load(session, user, arch_id, "Viewer")
    return serialize(arch, spec)


@router.get("/{arch_id}/document", response_class=PlainTextResponse)
async def get_document(
    arch_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The architecture as the Markdown the repository gets."""
    arch, _ = _load(session, user, arch_id, "Viewer")
    return arch_author.document(session, arch)


@router.get("/{arch_id}/adrs")
async def get_adrs(
    arch_id: int,
    first_number: int = Query(default=1, ge=1, le=9999),
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Its decisions as ADR files, ``docs/adr/NNNN-<slug>.md`` (``services/planning/adr_docs.py``).

    ``first_number`` is the number of ADR-1, for a repository whose ADRs
    started before the factory recorded them."""
    arch, spec = _load(session, user, arch_id, "Viewer")
    approved_on = (
        arch.approved_at.date().isoformat()
        if arch.status != "draft" and arch.approved_at
        else None
    )
    files = adr_docs.adr_documents(
        arch_author.content_of(arch),
        spec.title,
        arch.version,
        first_number=first_number,
        approved_on=approved_on,
    )
    return {"architecture_id": arch.id, "files": files}


@router.post("", status_code=201)
async def create_architecture(
    req: ArchitectureCreate,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person writes a draft; it is numbered and checked like the Architect's."""
    spec = _specification(session, user, req.specification_id, "Manager")
    arch = Architecture(
        company_id=spec.company_id, specification_id=spec.id, title=req.title
    )
    _apply(arch, req.model_dump(exclude={"specification_id"}), _actor(user))
    return _saved(session, arch, spec)


@router.put("/{arch_id}")
async def update_architecture(
    arch_id: int,
    req: ArchitectureBody,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Edit a draft. The questions keep their answers; the ids are reassigned."""
    arch, spec = _load(session, user, arch_id, "Manager")
    _only_draft(arch)
    _apply(arch, {**req.model_dump(), "questions": arch.questions}, _actor(user))
    return _saved(session, arch, spec)


@router.post("/draft")
async def draft_architecture(
    req: DraftRequest,
    http_request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The Architect drafts from the approved specification, or revises the draft named.

    409 for a specification that is not approved; 502 when the Architect gave
    nothing usable (the draft named is left as it was).
    """
    spec = _specification(session, user, req.specification_id, "Manager")
    if spec.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"The specification is {spec.status}; an architecture is drafted from an approved one.",
        )
    existing = None
    if req.architecture_id is not None:
        existing, _ = _load(session, user, req.architecture_id, "Manager")
        if existing.specification_id != spec.id:
            raise HTTPException(status_code=404, detail="Architecture not found")
        _only_draft(existing)
    # What the Architect is asked is read here, with the request's session;
    # only the call to the proxy runs in a thread (a session is not shared).
    request = arch_author.prepare(session, spec, existing)
    request["prompt"] += await repo_map.fetch_section(
        getattr(http_request.app.state, "nats_client", None), request["project_path"]
    )
    try:
        content = await asyncio.to_thread(arch_author.draft_from, request)
    except arch_author.ArchAuthorError as exc:
        raise HTTPException(
            status_code=502, detail=f"The Architect gave no usable draft: {exc}"
        ) from exc
    arch = existing or Architecture(
        company_id=spec.company_id, specification_id=spec.id, title=content["title"]
    )
    _apply(arch, content, arch_author.ARCHITECT_ROLE)
    arch.sources = content.get("sources") or []
    return _saved(session, arch, spec)


@router.post("/{arch_id}/questions/{index}")
async def answer_question(
    arch_id: int,
    index: int,
    req: AnswerRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person answers the Architect's question *index* (0-based); an empty answer reopens it."""
    arch, spec = _load(session, user, arch_id, "Manager")
    _only_draft(arch)
    questions = list(arch.questions or [])
    if not 0 <= index < len(questions):
        raise HTTPException(status_code=404, detail="No such question")
    answer = " ".join(req.answer.split())
    questions[index] = {**questions[index], "answer": answer or None}
    arch.questions = questions
    arch.updated_at = _now()
    return _saved(session, arch, spec)


@router.post("/{arch_id}/approve")
async def approve_architecture(
    arch_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Approve a draft; the version it revises, and any other approved one of the specification, become superseded."""
    arch, spec = _load(session, user, arch_id, "Manager")
    _only_draft(arch)
    # Segregation of duties (#543), when the company's mode is ``all``.
    try:
        sod.enforce(
            session,
            arch.company_id,
            sod.APPROVE_ARCH,
            {"kind": "architecture", "id": arch.id, "version": arch.version},
            sod.actor_of(session, user),
            sod_requesters.of_architecture(session, arch),
        )
    except sod.SodRefused as refused:
        raise HTTPException(
            status_code=refused.status, detail=refused.detail
        ) from refused
    try:
        document_approval.approve_architecture(session, arch, spec, _actor(user))
    except document_approval.ApprovalRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return _saved(session, arch, spec)


@router.post("/{arch_id}/revise", status_code=201)
async def revise_architecture(
    arch_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A new draft, version + 1, with the approved content to start from."""
    arch, spec = _load(session, user, arch_id, "Manager")
    if arch.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Only an approved architecture is revised; this one is {arch.status}.",
        )
    open_revision = session.exec(
        select(Architecture).where(
            Architecture.supersedes_id == arch.id, Architecture.status == "draft"
        )
    ).first()
    if open_revision is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A revision is already in draft.",
                "architecture_id": open_revision.id,
            },
        )
    revision = Architecture(
        company_id=arch.company_id,
        specification_id=arch.specification_id,
        title=arch.title,
        overview=arch.overview,
        version=arch.version + 1,
        supersedes_id=arch.id,
        modules=list(arch.modules or []),
        interfaces=list(arch.interfaces or []),
        decisions=list(arch.decisions or []),
        conventions=list(arch.conventions or []),
        questions=[],
        drafted_by=_actor(user),
    )
    return _saved(session, revision, spec)


@router.post("/{arch_id}/write")
async def write_architecture(
    arch_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Write the approved architecture into the repository as ``docs/ARCHITECTURE.md``.

    The daemon on the person's computer writes it and reports the sha256 of
    the bytes it read back; that is what is kept. 409 for a draft, 503 when
    no daemon answers.
    """
    arch, spec = _load(session, user, arch_id, "Manager")
    if arch.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Only an approved architecture is written to the repository.",
        )
    try:
        written = await arch_author.write_document(
            getattr(request.app.state, "nats_client", None),
            session,
            arch,
            WRITE_TIMEOUT_SECONDS,
        )
    except arch_author.ArchAuthorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DaemonUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"No workspace daemon answered: {exc}"
        ) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    arch.written_path = str(written["path"])
    arch.written_sha256 = str(written["sha256"])
    arch.written_at = _now()
    arch.updated_at = arch.written_at
    return _saved(session, arch, spec)
