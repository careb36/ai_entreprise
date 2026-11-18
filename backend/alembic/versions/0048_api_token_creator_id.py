"""An API token records its creator's account id (issue #512, after the SDL
review of PR #644): ``api_tokens.created_by_user_id``, nullable, a foreign
key to ``users.id``. The cap on every use (``services/platform/api_tokens``)
compares on it, so an email address that is renamed or passes to another
account neither takes a token's standing away nor hands it on.

Backfill: a token whose ``created_by`` matches an account's email (both
stripped and lower-cased, the rule of ``services/contracts/emails.py``,
repeated here so the migration does not change when the app does) gets that
account's id. A token with no matching account keeps a NULL id and is judged
by its email, as before; one with no ``created_by`` stays uncapped, as
before. The backfill binds a token to whoever holds the address at upgrade
time, which is what the email rule already did that moment.

Follows 0047 (a person's statements).

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-18 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

COLUMN = "created_by_user_id"
FOREIGN_KEY = "fk_api_tokens_created_by_user_id_users"


def _normalise(value: str) -> str:
    return value.strip().lower()


def upgrade() -> None:
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FOREIGN_KEY, "users", [COLUMN], ["id"])

    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("email"))
    tokens = sa.table(
        "api_tokens",
        sa.column("id", sa.Integer),
        sa.column("created_by"),
        sa.column(COLUMN, sa.Integer),
    )
    # 0045 keeps one account per normalised address; an empty one matches
    # nothing.
    by_address = {
        _normalise(email): user_id
        for user_id, email in bind.execute(sa.select(users.c.id, users.c.email))
        if email and _normalise(email)
    }
    rows = bind.execute(
        sa.select(tokens.c.id, tokens.c.created_by).where(
            tokens.c.created_by.is_not(None)
        )
    ).fetchall()
    for token_id, created_by in rows:
        user_id = by_address.get(_normalise(created_by))
        if user_id is not None:
            bind.execute(
                tokens.update().where(tokens.c.id == token_id).values({COLUMN: user_id})
            )


def downgrade() -> None:
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.drop_constraint(FOREIGN_KEY, type_="foreignkey")
        batch_op.drop_column(COLUMN)
