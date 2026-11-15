"""The architecture: how a company's system is built, as data, versioned.

A company has one architecture (issue #329, finding F3 of the self-hosting
retrospective). Each row of this table is one version of it: ``version``
counts the company's versions (1, 2, 3…), and approving one supersedes every
other approved version of the company, so the company has one current
architecture and its earlier versions stay readable. ``specification_id`` is
the specification the version was drafted for; a module's ``covers`` names
that specification's requirements, and its ``earlier_covers`` keeps the
requirements it covers in the earlier specifications the architecture
serves, by specification id (``{"12": ["REQ-1"]}``). A revision for a new
specification is the next version of the same architecture, so the covers of
earlier specifications are kept (``services/planning/arch_versions.py``).

It names the modules (each with its responsibility, its place in the
repository, what it depends on and which requirements it covers), the
interfaces between them (a contract a test can hold them to), the decisions
taken and why (ADRs), and the conventions every task follows. The ids
(``MOD-1``, ``IF-2``, ``ADR-3``) are assigned by ``services.planning.arch_check``,
never by a model.

The Architect drafts it from the specification and the project; a person
answers its questions, edits and approves. A draft is a claim; the
deterministic check (unknown ids, a dependency cycle, a decision without
its decision, an open question) decides whether it can be approved. Once
approved, every task's prompt carries the modules its requirements map to,
and a Manager can write it into the repository as ``docs/ARCHITECTURE.md``:
the sha256 the daemon reads back is kept here as an observation.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel

ARCHITECTURE_STATUSES = ("draft", "approved", "superseded")
ARCHITECTURE_PATH = "docs/ARCHITECTURE.md"


class Architecture(SQLModel, table=True):
    __tablename__ = "architectures"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    specification_id: int = Field(foreign_key="specifications.id", index=True)
    title: str
    overview: str = ""
    status: str = "draft"
    version: int = 1
    supersedes_id: int | None = Field(default=None, foreign_key="architectures.id")
    # [{id, name, responsibility, path, depends_on: [MOD ids], covers: [REQ ids],
    #   earlier_covers?: {"<specification id>": [REQ ids]}}]
    modules: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # [{id, name, kind, module_id, contract, consumers: [MOD ids]}]
    interfaces: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # [{id, title, context, decision, consequences}]
    decisions: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    conventions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # [{question, answer}] — the Architect's open points; a person answers.
    questions: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # The sources the drafting agent consulted (``services/research_tools.py``):
    # observations the backend made, never the agent's own account.
    sources: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    drafted_by: str | None = None  # "Architect", or a person's email
    approved_by: str | None = None
    approved_at: datetime | None = None
    # Where the daemon wrote it in the repository and the sha256 it read back.
    written_path: str | None = None
    written_sha256: str | None = None
    written_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
