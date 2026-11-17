"""
The self-improvement cycles, as records (``docs/design/rsi-loop.md``, PR 10).

One row per cycle a company runs for one finding: the baseline B and the
candidate C, the status, the fitness decision with its reasons, the size of
C's diff as the workspace daemon observed it, the token budget and the
reported tokens, the files kept with their hashes, and who merged. Every
change of a row is audited in the company's chain
(``services/management/rsi_cycle.py``). A record imported from the cycle
runner keeps what it claimed apart from what the backend observed: the raw
record in ``record``, and each kept file's ``sha256_claimed`` beside the
``sha256_observed`` the backend computed (a claim and an observation never
share a field).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel

RUNNING = "running"
AWAITING_MERGE = "awaiting_merge"  # kept: a person merges at C
AWAITING_PERSON = "awaiting_person"  # over the size cap: unmeasured, a person looks
MERGED = "merged"
REVERTED = "reverted"
UNMEASURED = "unmeasured"
BUDGET_EXCEEDED = "budget_exceeded"
STOPPED = "stopped"
CYCLE_STATUSES = (
    RUNNING,
    AWAITING_MERGE,
    AWAITING_PERSON,
    MERGED,
    REVERTED,
    UNMEASURED,
    BUDGET_EXCEEDED,
    STOPPED,
)
# A cycle whose decision was keep; every other ended status kept nothing.
KEPT_STATUSES = (AWAITING_MERGE, MERGED)


class RsiCycle(SQLModel, table=True):
    __tablename__ = "rsi_cycles"
    __table_args__ = (
        UniqueConstraint("company_id", "cycle_key", name="uq_rsi_cycles_company_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # The cycle's name, branch-safe (the runner's ``cycle_id``).
    cycle_key: str
    finding_id: int | None = Field(default=None, foreign_key="findings.id")
    # "backend": started here; "imported": a runner's record read in.
    source: str = "backend"
    status: str = RUNNING
    baseline_sha: str | None = None
    candidate_sha: str | None = None
    sprint_id: int | None = None
    integration_id: int | None = None
    pull_request: int | None = None
    # The fitness decision (keep, revert, unmeasured), written by the platform.
    decision: str | None = None
    reasons: list[Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    metrics: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    # C's diff as the workspace daemon read it from git; never declared.
    diff_files: int | None = None
    diff_lines: int | None = None
    diff_sha256: str | None = None
    token_budget: int | None = None
    tokens_reported: int = 0
    # [{"path", "sha256_observed", "sha256_claimed"}]
    kept_files: list[Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    # The runner's record as received (claims), for an imported cycle.
    record: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    merged_sha: str | None = None
    merged_by: str | None = None  # the signed-in Owner's email
    merged_at: datetime | None = None
    started_by: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    # A person resumed the loop after the stop: this cycle no longer counts.
    resumed_by: str | None = None
    resumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
