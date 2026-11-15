"""Documentation router — a project's management documentation, compiled from its records.

Endpoints (issue #530, ``docs/design/PROJECT_DOCUMENTATION.md``):
- ``GET  /v1/companies/{id}/documentation``           — the pack versions, newest first (Viewer).
- ``POST /v1/companies/{id}/documentation``           — compile a new draft pack (Manager).
- ``GET  /v1/documentation/{pack_id}``                — one pack with its documents, diagrams and lint (Viewer).
- ``GET  /v1/documentation/{pack_id}/documents/{key}`` — one document as Markdown (Viewer).
- ``POST /v1/documentation/{pack_id}/approve``        — approve a draft; refused while it drifts (Owner).
- ``POST /v1/documentation/{pack_id}/write``          — write an approved pack to ``docs/project/`` (Manager).
- ``GET  /v1/companies/{id}/doc_language``            — the language packs are written in (Viewer).
- ``PUT  /v1/companies/{id}/doc_language``            — change it, en or es (Manager; audited).

The documents are built deterministically by ``services/planning/project_docs``;
the only model call is the charter's background, asked of the TechWriter and
marked as a claim. The lint compares each document's input digest with the
current records, so a stale pack cannot be approved.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from models import Company, DocumentationPack, User
from schemas import RequestModel
from services.planning import document_approval
from services.planning.project_docs import collect, texts
from services.planning.project_docs import pack as packs
from services.platform.agent_service import AgentService
from services.workspace.workspace_daemon import DaemonError, DaemonUnavailable
from sqlmodel import Session, select

router = APIRouter(tags=["Core"])

WRITE_TIMEOUT_SECONDS = 60.0


class LanguageRequest(RequestModel):
    doc_language: Literal["en", "es"]


class CompileRequest(RequestModel):
    # Ask the TechWriter for the charter's background (the only prose).
    prose: bool = True


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _summary(pack: DocumentationPack) -> dict[str, Any]:
    return {
        "id": pack.id,
        "company_id": pack.company_id,
        "version": pack.version,
        "status": pack.status,
        "specification_id": pack.specification_id,
        "architecture_id": pack.architecture_id,
        "compiled_by": pack.compiled_by,
        "language": pack.language,
        "inputs_sha256": pack.inputs_sha256,
        "approved_at": _iso(pack.approved_at),
        "written_files": pack.written_files or [],
        "written_at": _iso(pack.written_at),
        "created_at": _iso(pack.created_at),
    }


def serialize(pack: DocumentationPack, lint: dict[str, Any]) -> dict[str, Any]:
    return {
        **_summary(pack),
        "documents": [
            {
                "key": d.get("key"),
                "title": d.get("title"),
                "framework": d.get("framework"),
                "file": f"{packs.PACK_DIRECTORY}/{packs.file_name(str(d.get('key')))}",
                "markdown": d.get("markdown") or "",
                "sources": d.get("sources") or [],
                "missing": d.get("missing") or [],
                "claims": d.get("claims") or [],
                "persons": d.get("persons") or [],
                "domain": d.get("domain") or "",
                "focus_area": d.get("focus_area") or "",
                # CC BY-SA 4.0 for the WSTG checklist and report (issue #539).
                "licence": d.get("licence"),
                "input_sha256": d.get("input_sha256"),
            }
            for d in pack.documents or []
        ],
        "diagrams": pack.diagrams or [],
        "lint": lint,
    }


def _company(
    session: Session, user: User | None, company_id: int, role: str
) -> Company:
    query = select(Company).where(Company.id == company_id)
    if role != "Viewer":
        # One compile at a time per company: the version number is read then written.
        query = query.with_for_update()
    company = session.exec(query).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    require_company_role(session, user, company.id, role)
    return company


def _load(
    session: Session, user: User | None, pack_id: int, role: str
) -> tuple[DocumentationPack, Company]:
    query = select(DocumentationPack).where(DocumentationPack.id == pack_id)
    if role != "Viewer":
        query = query.with_for_update()
    pack = session.exec(query).first()
    if pack is None:
        raise HTTPException(status_code=404, detail="Documentation pack not found")
    require_company_role(session, user, pack.company_id, role)
    company = session.get(Company, pack.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return pack, company


def _audit(
    session: Session,
    pack: DocumentationPack,
    actor: str,
    action: str,
    result: dict[str, Any],
) -> None:
    """An approval or a write enters the company's audit chain, attributed to the person."""
    AgentService().audit_interaction(
        session=session,
        company_id=pack.company_id,
        interaction_id=f"documentation-pack:{pack.id}",
        agent_id=f"human:{actor}",
        action=action,
        details={
            "pack_id": pack.id,
            "version": pack.version,
            "inputs_sha256": pack.inputs_sha256,
        },
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _with_lint(
    session: Session, pack: DocumentationPack, company: Company
) -> dict[str, Any]:
    return serialize(pack, packs.lint(pack, collect.snapshot(session, company)))


@router.get("/v1/companies/{company_id}/documentation")
async def list_packs(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's pack versions, newest first, without their documents."""
    company = _company(session, user, company_id, "Viewer")
    rows = session.exec(
        select(DocumentationPack)
        .where(DocumentationPack.company_id == company.id)
        .order_by(DocumentationPack.id.desc())
    ).all()
    return {"packs": [_summary(p) for p in rows]}


@router.post("/v1/companies/{company_id}/documentation", status_code=201)
async def compile_pack(
    company_id: int,
    req: CompileRequest | None = None,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Compile a new draft from the current records; older drafts become superseded."""
    company = _company(session, user, company_id, "Manager")
    snapshot = collect.snapshot(session, company)
    prose = None
    if req is None or req.prose:
        # Read with the request's session; only the proxy call runs in a thread.
        prose = await asyncio.to_thread(
            packs.write_prose, packs.prose_request(session, company, snapshot)
        )
    pack = packs.compile_pack(session, company, snapshot, _actor(user), prose)
    session.commit()
    session.refresh(pack)
    return serialize(pack, packs.lint(pack, snapshot))


@router.get("/v1/documentation/{pack_id}")
async def get_pack(
    pack_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    pack, company = _load(session, user, pack_id, "Viewer")
    return _with_lint(session, pack, company)


@router.get(
    "/v1/documentation/{pack_id}/documents/{key}", response_class=PlainTextResponse
)
async def get_document(
    pack_id: int,
    key: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """One document as the Markdown the repository gets."""
    pack, _ = _load(session, user, pack_id, "Viewer")
    doc = next((d for d in pack.documents or [] if d.get("key") == key), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="No such document in the pack")
    return PlainTextResponse(
        doc.get("markdown") or "", media_type="text/markdown; charset=utf-8"
    )


@router.post("/v1/documentation/{pack_id}/approve")
async def approve_pack(
    pack_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An Owner approves a draft; 409 for another status or while the lint blocks (drift)."""
    pack, company = _load(session, user, pack_id, "Owner")
    if pack.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"The pack is {pack.status}; compile a new one to approve.",
        )
    snapshot = collect.snapshot(session, company)
    lint = packs.lint(pack, snapshot)
    try:
        superseded = document_approval.approve_pack(session, pack, lint, _actor(user))
    except document_approval.ApprovalRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    _audit(
        session,
        pack,
        _actor(user),
        "documentation_pack_approved",
        {"superseded": superseded},
    )
    session.commit()
    session.refresh(pack)
    return serialize(pack, lint)


@router.post("/v1/documentation/{pack_id}/write")
async def write_pack(
    pack_id: int,
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Write an approved pack to ``docs/project/`` through the workspace daemon.

    Keeps the sha256 the daemon read back for each file. 409 for a pack that is
    not approved or a company without a project folder, 503 when no daemon answers.
    """
    pack, company = _load(session, user, pack_id, "Manager")
    if pack.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Only an approved pack is written to the repository.",
        )
    try:
        written = await packs.write_pack(
            getattr(request.app.state, "nats_client", None),
            session,
            pack,
            WRITE_TIMEOUT_SECONDS,
        )
    except packs.PackError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DaemonUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"No workspace daemon answered: {exc}"
        ) from exc
    except DaemonError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    pack.written_files = written
    pack.written_at = datetime.now(UTC)
    pack.updated_at = pack.written_at
    session.add(pack)
    _audit(
        session, pack, _actor(user), "documentation_pack_written", {"files": written}
    )
    session.commit()
    session.refresh(pack)
    return _with_lint(session, pack, company)


@router.get("/v1/companies/{company_id}/doc_language")
async def get_doc_language(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The language the company's packs are written in, and whether it was chosen."""
    company = _company(session, user, company_id, "Viewer")
    return {
        "company_id": company.id,
        "doc_language": texts.language(company.doc_language),
        "chosen": company.doc_language is not None,
    }


@router.put("/v1/companies/{company_id}/doc_language")
async def put_doc_language(
    company_id: int,
    req: LanguageRequest,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """An Owner or Manager sets the language; packs compiled before it are flagged by the lint."""
    company = _company(session, user, company_id, "Manager")
    before = company.doc_language
    company.doc_language = req.doc_language
    session.add(company)
    AgentService().audit_interaction(
        session=session,
        company_id=company.id,
        interaction_id=f"doc-language:{company.id}",
        agent_id=f"human:{_actor(user)}",
        action="doc_language_updated",
        details={"before": before},
        result={"doc_language": req.doc_language},
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
    return {"company_id": company.id, "doc_language": req.doc_language, "chosen": True}
