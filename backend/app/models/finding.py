"""
The company's findings: what its retrospectives observed, as records.

A finding is a defect or gap the platform observed, kept with the
specification drafted for it and a target measure: a person-written check
that fails on the baseline and should pass once the finding is fixed
(``docs/design/rsi-loop.md``, section 0). Its outcomes are the cycles run
for it, each with the fitness decision, its reasons, the baseline's and the
candidate's sha and the date. The outcomes are observations the platform
writes (later, the cycle runner); a model or an agent never writes one, and
the specification's sha256 is computed by the platform, never received from
a producer (rule 6 of ``docs/POSITIONING.md``).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Column, Field, SQLModel

FINDING_STATUSES = (
    "open",
    "in_cycle",
    "kept",
    "reverted",
    "unmeasured",
    "closed_by_person",
)
# A cycle's decision, the same names fitness.decide answers with.
OUTCOME_DECISIONS = ("keep", "revert", "unmeasured")


class Finding(SQLModel, table=True):
    __tablename__ = "findings"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # Where the finding was observed: the retrospective's file and its item.
    source: str | None = None  # "docs/retrospectives/2026-11-self-hosting.md"
    source_ref: str | None = None  # "F5"
    title: str
    # The specification drafted for the finding. The sha256 is of the
    # normalised text and the platform computes it; it is the key REQ-20
    # compares, so whitespace or line endings make no second specification.
    specification_sha256: str | None = Field(default=None, index=True)
    specification_path: str | None = None
    specification_text: str | None = None
    # The person-written check that fails on the baseline: a repository path.
    target_measure: str | None = None
    status: str = "open"
    # One entry per cycle: its decision, the reasons, the two shas and the
    # date. none_as_null: "no outcome yet" must be SQL NULL so queries find it.
    outcomes: list[Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    closed_by: str | None = None  # the email of the person who closed it
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
