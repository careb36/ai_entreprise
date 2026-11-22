"""A company's own standards and the time-boxed exceptions to a standard
(issue #700; ``docs/archive/design/COMPANY_STANDARDS.md`` §7, §11.2, §12).

- ``CompanyStandardSet``       — one version of a company's standards file, as
  imported from the default branch's head: the bytes, the sha256 the backend
  computed over them, the commit, the parsed standards with their refusals,
  the guards' hashes, the baselines read at that commit and the dry run. A
  Manager imports a ``draft``; a different person, an Owner, activates it
  (``active``; the one it replaced becomes ``superseded``); an Owner may
  reject a draft (``rejected``). ``reverted`` is a version an Owner replaced
  by the revert the platform proposed.
- ``CompanyStandardException`` — a time-boxed exception to one standard (a
  catalogue ``STD-`` id or a company id): an Owner requests it with a reason,
  a compensating control and at most 90 days; a second Owner approves it. It
  lapses on its own at ``expires_at`` and is listed in the access review.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, LargeBinary, Text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CompanyStandardSet(SQLModel, table=True):
    __tablename__ = "company_standard_sets"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    version: int
    # "draft" | "active" | "superseded" | "reverted" | "rejected"
    status: str = "draft"
    source_path: str
    source_commit: str
    source_base: str | None = None
    # Computed by the backend over ``raw``; never read from the file, the
    # request or the daemon's reply (rule 6).
    sha256: str
    raw: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    # ``contracts.company_standards.Parsed.as_dict()``.
    parsed: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # guard path -> {"sha256": digest, "exclude": [...], "files": {path: sha256}}
    guard_hashes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # baseline path -> {path: lines}, read at ``source_commit``.
    baselines: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Refusals found while reading guards and baselines through the daemon.
    import_refusals: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    dry_run: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # The daemon's ``FABRIQUE_SANDBOX_REQUIRED`` when the draft was imported.
    sandbox_seen: str | None = None
    # An account's email, or ``agent:ScrumMaster`` for an agent's proposal.
    proposed_by: str | None = None
    imported_by: str | None = None
    imported_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    imported_at: datetime = Field(default_factory=_utcnow)
    activated_by: str | None = None
    activated_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    activated_at: datetime | None = None
    # The only Owner activated their own import: flagged in the access review
    # until a second Owner confirms it there (``reviewed_in``).
    single_owner: bool = False
    single_owner_reason: str | None = None
    reviewed_in: int | None = None
    supersedes_id: int | None = None
    ended_by: str | None = None
    ended_at: datetime | None = None
    # Measured after activation (the next pull request): the numbers before,
    # the numbers over tasks pinned to this version, and what they said.
    baseline: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    measurement: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    result: str | None = None


class CompanyStandardException(SQLModel, table=True):
    __tablename__ = "company_standard_exceptions"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    standard_id: str
    reason: str = Field(sa_column=Column(Text, nullable=False))
    compensating_control: str = Field(sa_column=Column(Text, nullable=False))
    days: int
    # "pending" | "approved" | "confirmed_single_owner" | "rejected"
    # | "withdrawn" | "revoked" | "expired" (a pending request past its week)
    state: str = "pending"
    requested_by: int = Field(foreign_key="users.id")
    requested_at: datetime = Field(default_factory=_utcnow)
    decided_by: int | None = Field(default=None, foreign_key="users.id")
    decided_at: datetime | None = None
    decision_reason: str | None = None
    # Set when approved: ``decided_at`` plus ``days``; it lapses on its own.
    expires_at: datetime | None = None
    reviewed_in: int | None = None
    revoked_by: int | None = Field(default=None, foreign_key="users.id")
    revoked_at: datetime | None = None
