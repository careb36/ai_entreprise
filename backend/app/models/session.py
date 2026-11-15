"""Browser sessions (issue #524, ``docs/design/SESSIONS.md``).

- ``SessionFamily`` — one sign-in of one person in one browser: the method it
  started with (``password`` | ``sso``), the sha256 of its CSRF token, an
  absolute expiry, and when and why it was revoked. Access tokens issued for
  it carry its ``public_id`` as ``sid`` and are refused once it is revoked.
- ``RefreshToken``  — the refresh tokens of a family, one per rotation. Only
  sha256 of the value the browser holds is stored; ``used_at`` marks a token
  already rotated, so presenting it again is reuse.

Times are naive UTC, as in ``models/sso.py``.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel


class SessionFamily(SQLModel, table=True):
    __tablename__ = "session_families"

    id: int | None = Field(default=None, primary_key=True)
    public_id: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    method: str  # "password" | "sso"
    csrf_hash: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # "logout" | "reuse" | "sso_required" | "member_removed" | "user_deleted"
    revoked_reason: str | None = None


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: int | None = Field(default=None, primary_key=True)
    family_id: int = Field(foreign_key="session_families.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: datetime
    used_at: datetime | None = None
