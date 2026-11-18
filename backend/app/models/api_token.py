"""API tokens for external callers (CI, other systems)."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ApiToken(SQLModel, table=True):
    __tablename__ = "api_tokens"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    name: str
    token_hash: str = Field(index=True)
    # The creator's email as it was when the token was minted (shown in the
    # access review), and the creator's account id, which the cap on every
    # use reads (issue #512): an address can pass to another account, an id
    # cannot. None on rows migration 0048 could not match to an account;
    # those are judged by email, as before.
    created_by: str | None = None
    created_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    # The company role the token acts with ("Viewer" or "Manager", never
    # "Owner"); rows written before roles existed act as a Manager, as before.
    role: str = Field(default="Manager")
    # After this moment the token is refused; None (legacy rows) never expires.
    expires_at: datetime | None = None
