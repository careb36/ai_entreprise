"""Pending invitations to a company (WSTG-v42-IDNT-04;
``services/platform/invitations.py``).

``CompanyInvitation`` — one invitation of one address to one company with a
role. Inviting adds nobody: only the invitee's acceptance creates the
``company_members`` row. The token that names it in a link is kept only as
its sha256; the address is normalised (``services/contracts/emails.py``).
``status`` is ``pending``, ``accepted``, ``declined`` or ``revoked``; an
invitation past ``expires_at`` is refused whatever its status says.
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CompanyInvitation(SQLModel, table=True):
    __tablename__ = "company_invitations"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    email: str = Field(index=True)  # normalised
    human_role: str  # "Owner" | "Manager" | "Viewer"
    invited_by_user_id: int | None = Field(default=None, foreign_key="users.id")
    invited_by: str | None = None  # the inviter's email, for the record
    token_sha256: str = Field(unique=True, index=True)
    status: str = "pending"
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    responded_at: datetime | None = None
    responded_by_user_id: int | None = Field(default=None, foreign_key="users.id")
