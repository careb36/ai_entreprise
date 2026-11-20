"""A person's recorded acknowledgement of a weaker guarantee (issue #766).

``CompanyAcknowledgement`` — an Owner's statement that the company runs
with less than the default protection, and why: ``tests_off`` (the
deployment does not run the delivery's tests, so no code delivery can be
verified) and ``host_sandbox`` (agent-written code runs on the daemon's
computer without isolation). Without one, a sprint of that company does not
start (``services/loop/preconditions.py``). Each has a reason, the account
that recorded it, and an end (``expires_at``, at most 90 days); withdrawing
one sets ``withdrawn_at`` and never deletes the row. Both writes are
audited. ``docs/design/target/01-verification.md``.
"""

from datetime import UTC, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CompanyAcknowledgement(SQLModel, table=True):
    __tablename__ = "company_acknowledgements"
    __table_args__ = (Index("ix_company_acknowledgements_kind", "company_id", "kind"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id")
    kind: str
    reason: str
    recorded_by: str
    recorded_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    recorded_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    withdrawn_by: str | None = None
    withdrawn_at: datetime | None = None
