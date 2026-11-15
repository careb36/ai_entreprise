"""The project wiki's store: pages, versions, derived links, the person's precedence (issue #445).

The syntax is ``services/contracts/wiki.py``; the citations are resolved by
``services/learning/wiki_sources.py``. This module writes and answers; it
never calls a model, the daemon or the evidence (the loop does, at sprint
close: ``services/loop/wiki_close.py``).

- ``save`` writes a version. Its citations are resolved against the records
  first: one no record supports is refused (``WikiError``, 422). An agent's
  version becomes the page in force (``current``) only when every statement
  carries a source and no person's edit is in force; otherwise it is
  ``proposed`` and waits for a person (``accept`` / ``reject``). A person's
  version is always in force and locks the page against overwriting: the next
  compilation may only propose.
- Every version in force recomputes the page's links from its body
  (``wiki.derive_links``): never taken from a client.
- ``apply_lint`` marks a page ``stale`` with the lint's reasons; only a new
  version in force (a revision) makes it ``current`` again.

A page is a claim, never evidence: nothing here writes an observation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import WikiLink, WikiPage, WikiPageVersion, WikiSettings
from sqlmodel import Session, select

from services.contracts import wiki
from services.learning import wiki_sources


class WikiError(ValueError):
    """A wiki write that cannot be made; ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 422, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def page_of(session: Session, company_id: int, slug: str) -> WikiPage | None:
    return session.exec(
        select(WikiPage).where(WikiPage.company_id == company_id, WikiPage.slug == slug)
    ).first()


def pages_of(session: Session, company_id: int) -> list[WikiPage]:
    """The company's pages, by kind then slug."""
    pages = session.exec(
        select(WikiPage).where(WikiPage.company_id == company_id)
    ).all()
    order = {kind: i for i, kind in enumerate(wiki.KINDS)}
    return sorted(pages, key=lambda p: (order.get(p.kind, 9), p.slug))


def versions_of(session: Session, page_id: int) -> list[WikiPageVersion]:
    """A page's versions, newest first."""
    return list(
        session.exec(
            select(WikiPageVersion)
            .where(WikiPageVersion.page_id == page_id)
            .order_by(WikiPageVersion.number.desc())  # type: ignore[attr-defined]
        ).all()
    )


def links_of(session: Session, page_id: int) -> list[WikiLink]:
    return list(session.exec(select(WikiLink).where(WikiLink.page_id == page_id)).all())


def _relink(session: Session, page: WikiPage) -> None:
    """The page's links, derived from its body in force; the old ones go."""
    for link in links_of(session, page.id):
        session.delete(link)
    for link in wiki.derive_links(page.body):
        session.add(
            WikiLink(
                company_id=page.company_id,
                page_id=page.id,
                kind=link["kind"],
                target=link["target"],
                sha256=link["sha256"],
            )
        )


def _make_current(
    session: Session, page: WikiPage, version: WikiPageVersion, locked: bool
) -> None:
    for old in versions_of(session, page.id):
        if old.id != version.id and old.status == wiki.CURRENT:
            old.status = wiki.SUPERSEDED
            session.add(old)
    version.status = wiki.CURRENT
    page.title = version.title
    page.body = version.body
    page.version = version.number
    page.current_version_id = version.id
    page.status = wiki.CURRENT
    page.lint_reasons = []
    page.person_locked = locked
    if page.proposed_version_id == version.id:
        page.proposed_version_id = None
    page.updated_at = _now()
    session.add(version)
    session.add(page)
    session.flush()
    _relink(session, page)


def save(
    session: Session,
    company_id: int,
    *,
    slug: str,
    kind: str,
    title: str,
    body: str,
    author_kind: str,
    author: str,
    sprint_id: int | None = None,
    module: dict[str, Any] | None = None,
) -> tuple[WikiPage, WikiPageVersion]:
    """Write a version of page *slug* (created when new); flushed, not committed."""
    reason = wiki.check_page(slug, kind, title, body)
    if reason:
        raise WikiError(reason)
    if author_kind not in wiki.AUTHOR_KINDS:
        raise WikiError("author_kind: agent or person")
    bad = wiki.bad_citations(body)
    if bad:
        raise WikiError(
            "Malformed citations: " + ", ".join(bad), detail={"malformed": bad}
        )
    resolved = wiki_sources.resolve(session, company_id, wiki.citations(body))
    unknown = wiki_sources.unknown(resolved)
    if unknown:
        raise WikiError(
            "Citations no record supports: " + ", ".join(unknown[:10]),
            detail={"unknown": unknown},
        )
    findings = (
        wiki.uncited(wiki.statement_blocks(body)) if author_kind == wiki.AGENT else []
    )
    page = page_of(session, company_id, slug)
    if page is None:
        page = WikiPage(
            company_id=company_id, slug=slug, kind=kind, title=title.strip()
        )
        session.add(page)
        session.flush()
    elif page.kind != kind:
        raise WikiError(f"The page {slug} is a {page.kind} page", 409)
    if module:
        page.module_ref = module.get("id") or page.module_ref
        page.module_name = module.get("name") or page.module_name
        page.module_path = module.get("path") or page.module_path
    numbers = [v.number for v in versions_of(session, page.id)]
    version = WikiPageVersion(
        company_id=company_id,
        page_id=page.id,
        number=(max(numbers) if numbers else 0) + 1,
        author_kind=author_kind,
        author=(author or "").strip()[:200] or author_kind,
        sprint_id=sprint_id,
        title=title.strip()[: wiki.TITLE_LIMIT],
        body=body,
        sources=resolved,
        findings=findings,
        status=wiki.PROPOSED,
        created_at=_now(),
    )
    session.add(version)
    session.flush()
    if author_kind == wiki.PERSON:
        _make_current(session, page, version, locked=True)
    elif page.person_locked or findings:
        # A person's edit wins, and an uncited statement waits for a person.
        if page.proposed_version_id:
            earlier = session.get(WikiPageVersion, page.proposed_version_id)
            if earlier is not None and earlier.status == wiki.PROPOSED:
                earlier.status = wiki.SUPERSEDED
                session.add(earlier)
        page.proposed_version_id = version.id
        if page.current_version_id is None:
            page.status = wiki.PROPOSED
        page.updated_at = _now()
        session.add(page)
    else:
        _make_current(session, page, version, locked=False)
    session.flush()
    return page, version


def accept(session: Session, version: WikiPageVersion, by: str) -> WikiPage:
    """A person puts a proposed version in force; its citations are checked
    again, since the records may have changed since it was proposed."""
    if version.status != wiki.PROPOSED:
        raise WikiError("Only a proposed version can be accepted", 409)
    page = session.get(WikiPage, version.page_id)
    if page is None:
        raise WikiError("The page is gone", 404)
    resolved = wiki_sources.resolve(
        session, page.company_id, wiki.citations(version.body)
    )
    unknown = wiki_sources.unknown(resolved)
    if unknown:
        raise WikiError(
            "Citations no record supports any more: " + ", ".join(unknown[:10]),
            detail={"unknown": unknown},
        )
    version.sources = resolved
    version.decided_by = (by or "").strip()[:200] or wiki.PERSON
    version.decided_at = _now()
    # The text in force is the agent's, which a person approved: the next
    # compilation may update it again (only a person's own edit locks a page).
    _make_current(session, page, version, locked=False)
    return page


def reject(
    session: Session, version: WikiPageVersion, by: str, reason: str | None = None
) -> WikiPage:
    if version.status != wiki.PROPOSED:
        raise WikiError("Only a proposed version can be rejected", 409)
    page = session.get(WikiPage, version.page_id)
    if page is None:
        raise WikiError("The page is gone", 404)
    version.status = wiki.REJECTED
    version.decided_by = (by or "").strip()[:200] or wiki.PERSON
    version.decided_at = _now()
    version.decision_reason = (reason or "").strip()[: wiki.REASON_LIMIT] or None
    if page.proposed_version_id == version.id:
        page.proposed_version_id = None
    session.add(version)
    session.add(page)
    session.flush()
    return page


def apply_lint(session: Session, page: WikiPage, reasons: list[dict[str, Any]]) -> None:
    """Record the lint's reasons; with any, the page is stale until revised."""
    page.linted_at = _now()
    if reasons:
        page.lint_reasons = reasons
        if page.status == wiki.CURRENT:
            page.status = wiki.STALE
    session.add(page)


def in_force(session: Session, company_id: int) -> list[WikiPage]:
    """The pages a prompt may read: in force and not stale."""
    return [p for p in pages_of(session, company_id) if p.status == wiki.CURRENT]


def current_version(session: Session, page: WikiPage) -> WikiPageVersion | None:
    if page.current_version_id is None:
        return None
    return session.get(WikiPageVersion, page.current_version_id)


def write_to_repo(session: Session, company_id: int) -> bool:
    row = session.get(WikiSettings, company_id)
    return bool(row and row.write_to_repo)


def set_write_to_repo(
    session: Session, company_id: int, on: bool, by: str
) -> WikiSettings:
    row = session.get(WikiSettings, company_id) or WikiSettings(company_id=company_id)
    row.write_to_repo = bool(on)
    row.updated_by = (by or "").strip()[:200] or None
    row.updated_at = _now()
    session.add(row)
    session.flush()
    return row


def page_dict(page: WikiPage, *, with_body: bool = True) -> dict[str, Any]:
    out = {
        "id": page.id,
        "slug": page.slug,
        "kind": page.kind,
        "title": page.title,
        "status": page.status,
        "version": page.version,
        "person_locked": page.person_locked,
        "proposed_version_id": page.proposed_version_id,
        "module": {
            "id": page.module_ref,
            "name": page.module_name,
            "path": page.module_path,
        }
        if page.kind == "module"
        else None,
        "lint_reasons": list(page.lint_reasons or []),
        "linted_at": _iso(page.linted_at),
        "updated_at": _iso(page.updated_at),
    }
    if with_body:
        out["body"] = page.body
    return out


def version_dict(version: WikiPageVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "page_id": version.page_id,
        "number": version.number,
        "author_kind": version.author_kind,
        "author": version.author,
        "sprint_id": version.sprint_id,
        "title": version.title,
        "body": version.body,
        "sources": list(version.sources or []),
        "findings": list(version.findings or []),
        "status": version.status,
        "decided_by": version.decided_by,
        "decided_at": _iso(version.decided_at),
        "decision_reason": version.decision_reason,
        "created_at": _iso(version.created_at),
    }


def blocks_with_sources(
    body: str, sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The body's blocks, each with the resolved sources its citations name:
    what the page shows beside each statement."""
    by_key = {(s["kind"], s["ref"], s.get("sha256")): s for s in sources}
    out = []
    for block in wiki.statement_blocks(body):
        cited = []
        for c in block["citations"]:
            source = by_key.get((c["kind"], c["ref"], c.get("sha256")))
            cited.append(source or {"kind": c["kind"], "ref": c["ref"], "known": False})
        out.append(
            {
                "kind": block["kind"],
                "line": block["line"],
                "text": block["text"],
                "sources": cited,
                "statement": block["kind"] in wiki.STATEMENT_KINDS,
            }
        )
    return out
