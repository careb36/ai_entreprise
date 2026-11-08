"""A company's notification webhook (issue #357, part 4)."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class CompanyWebhook(SQLModel, table=True):
    """Where the company is told that something finished, and with which secret.

    One per company. The secret is Fernet-encrypted (``crypto.encrypt_value``)
    and never returned by the API; it signs every delivery with HMAC-SHA256.
    ``last_*`` record the most recent delivery attempt as the backend saw it.
    """

    __tablename__ = "company_webhooks"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True, unique=True)
    url: str
    encrypted_secret: str
    events: list | None = Field(default=None, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_delivery_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
