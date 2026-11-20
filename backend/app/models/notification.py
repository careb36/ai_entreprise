"""Personal notifications: one row per receiver (issue #683).

- ``PersonalNotification`` — one notice to one person (``user_id``) in one
  company (``company_id``): its ``kind``, what it is about (``subject_type``
  and ``subject_id``, a reference, never a copy of the subject's text), a
  small ``context`` of the integer ids the link needs, when it was made, read
  and snoozed until. It holds nothing the receiver could not already see in
  that company, and it is readable only by its receiver
  (``services/platform/notifications.py``). ``dedupe_key`` keeps a producer
  that sees the same event twice from writing two rows for one person.
- ``NotificationPreference`` — what one person wants for one kind: shown in
  the app, and copied by email when the deployment has SMTP. A security
  notice is always shown in the app; its preference can only change the
  email.

Rows are deleted after ``RETENTION_NOTIFICATIONS_DAYS`` (90 by default,
never unbounded) and as soon as the receiver is no longer a member of the
company (``backend/janitor/retention.py``). The model follows Plane's
``Notification`` and ``UserNotificationPreference`` (AGPL-3.0-only, design
only: no code taken).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PersonalNotification(SQLModel, table=True):
    __tablename__ = "personal_notifications"
    __table_args__ = (
        Index("ix_personal_notifications_receiver", "user_id", "company_id"),
        Index("ix_personal_notifications_dedupe", "user_id", "dedupe_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    company_id: int = Field(foreign_key="companies.id", index=True)
    kind: str
    subject_type: str
    subject_id: int
    # Integer ids only, from the kind's allowlist (the link's parts).
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    dedupe_key: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, index=True)
    read_at: datetime | None = None
    snoozed_until: datetime | None = None


class NotificationPreference(SQLModel, table=True):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_notification_preferences"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    kind: str
    in_app: bool = True
    email: bool = False
    updated_at: datetime = Field(default_factory=_utcnow)
