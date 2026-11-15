"""A documentation pack: a project's management documents, compiled from the records.

One row per compiled version (issue #530,
``docs/design/PROJECT_DOCUMENTATION.md``). The documents (charter, scope
statement, WBS, schedule, RACI, risk register, lessons learned, the C4
diagrams) are Markdown built deterministically from the company's records by
``services/planning/project_docs``; each carries the sources it cites and the
digest of the records it read (``input_sha256``), which the drift check
compares with the current records. The few prose sections a model writes are
listed in each document's ``claims`` and marked as claims in its text.

A new compile is a draft and supersedes the older drafts; an Owner approves a
draft (the approved one before it becomes superseded), and only an approved
pack is written to the project's repository under ``docs/project/`` through the
workspace daemon, keeping the sha256 the daemon read back per file as an
observation.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel

PACK_STATUSES = ("draft", "approved", "superseded")
PACK_DIRECTORY = "docs/project"


class DocumentationPack(SQLModel, table=True):
    __tablename__ = "documentation_packs"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    version: int = 1
    status: str = "draft"
    # What it was compiled from: the specification and architecture read.
    specification_id: int | None = Field(default=None, foreign_key="specifications.id")
    architecture_id: int | None = Field(default=None, foreign_key="architectures.id")
    # The role that compiled it ("TechWriter") and who asked for it.
    compiled_by: str = "TechWriter"
    requested_by: str | None = None
    # [{key, title, framework, markdown, sources, missing, claims, inputs, input_sha256}]
    documents: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # [{key, level, title, nodes, edges, groups, notes, mermaid, input_sha256}]
    diagrams: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # The digest of every document's and diagram's input digest, in order.
    inputs_sha256: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None
    # [{path, sha256}] as the daemon reported them, and when.
    written_files: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    written_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
