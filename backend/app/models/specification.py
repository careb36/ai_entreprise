"""The specification: what a company's agents are asked to build, as data.

A specification is a numbered list of requirements, each with acceptance
criteria written as *given / when / then*, beside the quality attributes
(each with a number to measure it by) and the constraints. The ids
(``REQ-1``, ``REQ-1.2``, ``NFR-1``) are assigned by ``services.spec_check``,
never by a model, so a task can name the criteria it covers and the board
can show which of them got done.

The PO drafts it from a person's brief and asks what it could not decide
(``questions``); a person answers, edits and approves. A draft is a claim;
the deterministic check (``services.spec_check``) decides whether it can be
approved, and the board decides whether it was met. An approved version is
never edited: a revision is a new draft that supersedes it on approval.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel

SPECIFICATION_STATUSES = ("draft", "approved", "superseded")


class Specification(SQLModel, table=True):
    __tablename__ = "specifications"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    title: str
    summary: str = ""
    status: str = "draft"
    version: int = 1
    supersedes_id: int | None = Field(default=None, foreign_key="specifications.id")
    # [{id, title, description, criteria: [{id, given, when, then}]}]
    requirements: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # [{id, attribute, target}] — the target carries a number.
    quality: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    constraints: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # [{question, answer}] — the PO's open points; a person answers.
    questions: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    drafted_by: str | None = None  # "PO", or a person's email
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
