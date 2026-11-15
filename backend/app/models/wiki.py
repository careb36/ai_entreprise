"""The project wiki: what the project knows, compiled from the evidence (issue #445).

A company's wiki is a set of Markdown pages (``WikiPage``): one per module
of the approved architecture, one per important concept, a glossary and the
decisions in force. Every change is a version (``WikiPageVersion``) with its
author (an agent's role, or a person's email), the sprint that compiled it,
the sources its citations resolved to, and the body. The links a page makes
to tasks, requirements, modules, decisions, runs, notes and files
(``WikiLink``) are derived by the backend from the body on every save, never
sent by a client (after Plane's ``PageLog``, design only).

A page is a claim, never evidence (``docs/POSITIONING.md``): no field here
holds an observation. Its citations point at the records that do; the lint
(``services/contracts/wiki_lint.py``) compares them with what was observed
and marks a page ``stale`` when they no longer hold.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WikiPage(SQLModel, table=True):
    __tablename__ = "wiki_pages"
    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_wiki_pages_company_slug"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # Unique per company (``services/contracts/wiki.SLUG``).
    slug: str
    # module, concept, glossary, decisions (``services/contracts/wiki.KINDS``).
    kind: str
    title: str
    # The body in force: the current version's. Empty while the page has only
    # a proposed version.
    body: str = ""
    # current, stale, proposed (``services/contracts/wiki.STATUSES``).
    status: str = "proposed"
    # The current version's number (0 before the first).
    version: int = 0
    current_version_id: int | None = None
    # The agent version waiting for a person's decision, if any.
    proposed_version_id: int | None = None
    # True while the body in force is a person's edit: a compilation may then
    # only propose (the person's edit wins).
    person_locked: bool = False
    # For a module page: the module's id in the approved architecture it was
    # compiled from (``MOD-2``), its name and its path in the repository.
    module_ref: str | None = None
    module_name: str | None = None
    module_path: str | None = None
    # Why the lint marked the page stale: [{rule, ...}], empty when it holds.
    lint_reasons: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    linted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class WikiPageVersion(SQLModel, table=True):
    __tablename__ = "wiki_page_versions"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    # 1, 2, ... per page, in the order written (a rejected proposal keeps its number).
    number: int
    # agent or person; the agent's role or the person's email.
    author_kind: str
    author: str
    sprint_id: int | None = Field(default=None, foreign_key="sprints.id")
    title: str
    body: str
    # The citations of the body as the backend resolved them against the
    # records: [{kind, ref, sha256?, known, ...}]. Resolved, never trusted.
    sources: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    # What the citation check found (statements without a source).
    findings: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    # current, proposed, rejected, superseded.
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    created_at: datetime = Field(default_factory=_now)


class WikiLink(SQLModel, table=True):
    """One link of a page's body in force, derived by the backend on save."""

    __tablename__ = "wiki_links"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    # run, verification, task, note, retro, file, req, adr, mod
    kind: str
    target: str
    # For a file link: the hash the citation names. A claim, used only to look
    # up the daemon's observation (``services/learning/wiki_sources.py``);
    # never an observation itself.
    sha256: str | None = None


class WikiSettings(SQLModel, table=True):
    """A company's wiki settings: whether pages are also written to ``docs/wiki/``."""

    __tablename__ = "wiki_settings"

    company_id: int = Field(foreign_key="companies.id", primary_key=True)
    write_to_repo: bool = False
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_now)
