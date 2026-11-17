"""One account per email address, whatever its case: existing addresses are
normalised (spaces around them stripped, lower-cased; the rule of
``services/contracts/emails.py``, repeated here so the migration does not
change when the app does) and a unique index on ``lower(email)`` keeps it.

When two existing accounts normalise to the same address, nothing is changed:
the migration stops and names each address with the ids of the accounts that
share it. It never merges or deletes an account; an operator decides which to
keep (``docs/OPERATIONS.md``, The email migration stops on duplicates).

Follows 0043 (the findings of the self-improvement loop).

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-17 13:00:00 UTC
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

INDEX = "ux_users_email_lower"


def _normalise(value: str) -> str:
    return value.strip().lower()


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("email"))
    rows = bind.execute(sa.select(users.c.id, users.c.email)).fetchall()
    by_address: dict[str, list[int]] = defaultdict(list)
    for user_id, email in rows:
        by_address[_normalise(email or "")].append(user_id)
    collisions = {a: sorted(ids) for a, ids in by_address.items() if len(ids) > 1}
    if collisions:
        listed = "; ".join(
            f"{address}: user ids {', '.join(str(i) for i in ids)}"
            for address, ids in sorted(collisions.items())
        )
        raise RuntimeError(
            "migration 0044 stopped: these accounts share an email address once "
            f"it is lower-cased and stripped ({listed}). Nothing was changed. "
            "Keep one account per address (change the others' email, or move "
            "their memberships and delete them) and run the migration again; "
            "see docs/OPERATIONS.md, The email migration stops on duplicates."
        )
    for user_id, email in rows:
        normalised = _normalise(email or "")
        if normalised != email:
            bind.execute(
                users.update().where(users.c.id == user_id).values(email=normalised)
            )
    op.create_index(INDEX, "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    # The addresses stay normalised: the original spelling is not kept.
    op.drop_index(INDEX, table_name="users")
