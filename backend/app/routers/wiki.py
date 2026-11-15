"""
Project wiki router — what the project knows, compiled from the evidence (issue #445).

Endpoints:
- ``GET  /v1/companies/{company_id}/wiki``                 — the pages, their status
  and lint reasons, and the settings (Viewer).
- ``GET  /v1/companies/{company_id}/wiki/{slug}``          — a page: its body, each
  statement block with the sources its citations resolved to, its links, the
  proposal waiting, the lint reasons in the project's language (Viewer).
- ``GET  /v1/companies/{company_id}/wiki/{slug}/versions`` — the history (Viewer).
- ``PUT  /v1/companies/{company_id}/wiki/{slug}``          — a person writes the page
  (Manager): a new version in force, which the next compilation may only propose
  changes to.
- ``POST /v1/wiki_versions/{version_id}/accept|reject``    — a person decides on a
  proposed version (Manager).
- ``POST /v1/companies/{company_id}/wiki-lint``            — run the lint against the
  evidence now (Manager).
- ``PUT  /v1/companies/{company_id}/wiki-settings``        — write pages to
  ``docs/wiki/`` at sprint close, or not (Manager).

Pages are claims. The links and sources are derived by the backend from the
body; a client never sends them. Every write is audited under
``company:<id>:wiki``.
"""

from typing import Any

from auth_policy import current_user_if_required, require_company_role
from database import get_session
from fastapi import APIRouter, Depends, HTTPException
from models import Company, User, WikiPageVersion
from pydantic import BaseModel, Field
from services.contracts import wiki, wiki_templates
from services.learning import wiki_store
from services.loop import wiki_close
from services.platform.agent_service import AgentService
from sqlmodel import Session

router = APIRouter(prefix="/v1", tags=["Agents"])


class WikiPageWrite(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=wiki.TITLE_LIMIT)
    body: str = Field(max_length=wiki.BODY_LIMIT)


class WikiDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=wiki.REASON_LIMIT)


class WikiSettingsWrite(BaseModel):
    write_to_repo: bool


def _actor(user: User | None) -> str:
    return user.email if user is not None else "local demo"


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _audit(
    session: Session, company_id: int, actor: str, action: str, result: dict[str, Any]
) -> None:
    """One audit row for a wiki write; commits the session."""
    AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=f"company:{company_id}:wiki",
        agent_id=f"human:{actor}",
        action=action,
        details={"company_id": str(company_id)},
        result=result,
        decision_signature=None,
        cost_tokens=0,
    )


def _page_view(session: Session, company: Company, slug: str) -> dict[str, Any]:
    page = wiki_store.page_of(session, company.id, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    language = wiki_templates.language_of(company)
    version = wiki_store.current_version(session, page)
    sources = list(version.sources or []) if version else []
    proposed = (
        session.get(WikiPageVersion, page.proposed_version_id)
        if page.proposed_version_id
        else None
    )
    return wiki_store.page_dict(page) | {
        "language": language,
        "blocks": wiki_store.blocks_with_sources(page.body, sources),
        "sources": sources,
        "links": [
            {"kind": link.kind, "target": link.target, "sha256": link.sha256}
            for link in wiki_store.links_of(session, page.id)
        ],
        "lint": [
            {"rule": r.get("rule"), "text": wiki_templates.describe_reason(r, language)}
            for r in page.lint_reasons or []
        ],
        "current_version": wiki_store.version_dict(version) if version else None,
        "proposed_version": (
            wiki_store.version_dict(proposed)
            | {
                "blocks": wiki_store.blocks_with_sources(
                    proposed.body, list(proposed.sources or [])
                )
            }
            if proposed is not None
            else None
        ),
    }


@router.get("/companies/{company_id}/wiki")
async def list_wiki(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """The company's pages, without their bodies; and the wiki's settings."""
    company = _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    return {
        "pages": [
            wiki_store.page_dict(p, with_body=False)
            for p in wiki_store.pages_of(session, company_id)
        ],
        "kinds": list(wiki.KINDS),
        "language": wiki_templates.language_of(company),
        "settings": {"write_to_repo": wiki_store.write_to_repo(session, company_id)},
    }


@router.get("/companies/{company_id}/wiki/{slug}")
async def get_wiki_page(
    company_id: int,
    slug: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    company = _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    return _page_view(session, company, slug)


@router.get("/companies/{company_id}/wiki/{slug}/versions")
async def wiki_page_versions(
    company_id: int,
    slug: str,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    _company(session, company_id)
    require_company_role(session, user, company_id, "Viewer")
    page = wiki_store.page_of(session, company_id, slug)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "versions": [
            wiki_store.version_dict(v) for v in wiki_store.versions_of(session, page.id)
        ]
    }


@router.put("/companies/{company_id}/wiki/{slug}")
async def write_wiki_page(
    company_id: int,
    slug: str,
    req: WikiPageWrite,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person's version, in force at once; audited as ``wiki_page_edited``."""
    company = _company(session, company_id)
    require_company_role(session, user, company_id, "Manager")
    actor = _actor(user)
    try:
        page, version = wiki_store.save(
            session,
            company_id,
            slug=slug,
            kind=req.kind,
            title=req.title,
            body=req.body,
            author_kind=wiki.PERSON,
            author=actor,
        )
    except wiki_store.WikiError as exc:
        session.rollback()
        raise HTTPException(
            status_code=exc.status, detail=exc.detail or str(exc)
        ) from exc
    _audit(
        session,
        company_id,
        actor,
        "wiki_page_edited",
        {"slug": page.slug, "version": version.number, "kind": page.kind},
    )
    return _page_view(session, company, slug)


def _decide(
    session: Session, version_id: int, user: User | None
) -> tuple[WikiPageVersion, Company, str]:
    version = session.get(WikiPageVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    company = _company(session, version.company_id)
    require_company_role(session, user, version.company_id, "Manager")
    return version, company, _actor(user)


@router.post("/wiki_versions/{version_id}/accept")
async def accept_wiki_version(
    version_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person puts a proposed version in force; audited as ``wiki_version_accepted``."""
    version, company, actor = _decide(session, version_id, user)
    try:
        page = wiki_store.accept(session, version, actor)
    except wiki_store.WikiError as exc:
        session.rollback()
        raise HTTPException(
            status_code=exc.status, detail=exc.detail or str(exc)
        ) from exc
    _audit(
        session,
        company.id,
        actor,
        "wiki_version_accepted",
        {"slug": page.slug, "version": version.number},
    )
    return _page_view(session, company, page.slug)


@router.post("/wiki_versions/{version_id}/reject")
async def reject_wiki_version(
    version_id: int,
    req: WikiDecision,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """A person turns a proposed version down; audited as ``wiki_version_rejected``."""
    version, company, actor = _decide(session, version_id, user)
    try:
        page = wiki_store.reject(session, version, actor, req.reason)
    except wiki_store.WikiError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    _audit(
        session,
        company.id,
        actor,
        "wiki_version_rejected",
        {
            "slug": page.slug,
            "version": version.number,
            "reason": version.decision_reason,
        },
    )
    return _page_view(session, company, page.slug)


@router.post("/companies/{company_id}/wiki-lint")
async def lint_wiki(
    company_id: int,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Run the lint against the evidence now (no daemon); audited as ``wiki_linted``."""
    _company(session, company_id)
    require_company_role(session, user, company_id, "Manager")
    found = wiki_close.lint_company(session, company_id)
    session.commit()
    _audit(session, company_id, _actor(user), "wiki_linted", {"stale": sorted(found)})
    return {"stale": found}


@router.put("/companies/{company_id}/wiki-settings")
async def write_wiki_settings(
    company_id: int,
    req: WikiSettingsWrite,
    session: Session = Depends(get_session),  # noqa: B008 - FastAPI dependency marker.
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Whether the pages are written to ``docs/wiki/``; audited as ``wiki_settings_changed``."""
    _company(session, company_id)
    require_company_role(session, user, company_id, "Manager")
    actor = _actor(user)
    row = wiki_store.set_write_to_repo(session, company_id, req.write_to_repo, actor)
    _audit(
        session,
        company_id,
        actor,
        "wiki_settings_changed",
        {"write_to_repo": row.write_to_repo},
    )
    return {"write_to_repo": row.write_to_repo}
