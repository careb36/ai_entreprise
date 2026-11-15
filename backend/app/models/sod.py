"""Segregation of duties and the periodic access review (issue #543;
``docs/design/SEGREGATION_OF_DUTIES.md``).

- ``SodPolicy``     — one per company, written only by a signed-in Owner: which
  approvals the rule holds (``off``, ``high_risk`` — the default — or ``all``),
  the single-person exception (granted with a reason, audited, honoured only
  while the company has one person able to approve), and the access review's
  interval and inactivity threshold. No row means the defaults.
- ``AccessReview``  — one completed access review: what was listed (members
  with their effective roles, API tokens, SSO and chat links, last activity),
  the Owner's decision on each item (confirmed or removed) and whether it was
  completed on time.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SodPolicy(SQLModel, table=True):
    __tablename__ = "sod_policies"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", unique=True, index=True)
    # "off" | "high_risk" | "all" (``services/platform/sod.py``, ``MODES``).
    mode: str = "high_risk"
    # The single-person exception: an Owner's explicit, audited choice, never
    # a silent bypass. Honoured only while one person can approve.
    solo_exception: bool = False
    solo_reason: str | None = None
    solo_granted_by: str | None = None
    solo_granted_at: datetime | None = None
    review_interval_days: int = 90
    inactive_days: int = 90
    updated_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AccessReview(SQLModel, table=True):
    __tablename__ = "access_reviews"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # The due date this review answered (empty: the first one, due at once).
    due_at: datetime | None = None
    completed_at: datetime = Field(default_factory=_utcnow)
    completed_by: str
    completed_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    on_time: bool = True
    # [{kind, id, label, ..., decision}] as listed when it was completed.
    items: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    # {"members": n, "tokens": n, "confirmed": n, "removed": n, "inactive": n}
    summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    note: str | None = None
    audit_log_id: int | None = Field(default=None, foreign_key="audit_log.id")
