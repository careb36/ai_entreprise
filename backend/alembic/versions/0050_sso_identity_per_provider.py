"""One SSO identity per account and provider (issue #512; ``docs/archive/design/SSO.md``,
T33): a unique index on ``sso_identities (user_id, issuer)``. The code already
refuses a second subject at the same issuer (``identity_conflict``); the index
makes two links racing past that check fail in the database instead of
leaving the account with two identities.

When an existing account already holds two identities at one issuer, nothing
is changed: the migration stops and names each account and issuer with the
identity ids. It never deletes an identity; an operator decides which to keep
(``docs/OPERATIONS.md``, The SSO identity migration stops on duplicates).

Follows 0049 (DNS verification of SSO domains).

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-18 21:00:00 UTC
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

INDEX = "ux_sso_identities_user_issuer"


def upgrade() -> None:
    bind = op.get_bind()
    identities = sa.table(
        "sso_identities",
        sa.column("id", sa.Integer),
        sa.column("user_id", sa.Integer),
        sa.column("issuer"),
    )
    rows = bind.execute(
        sa.select(identities.c.id, identities.c.user_id, identities.c.issuer)
    ).fetchall()
    by_pair: dict[tuple[int, str], list[int]] = defaultdict(list)
    for identity_id, user_id, issuer in rows:
        by_pair[(user_id, issuer)].append(identity_id)
    duplicates = {pair: sorted(ids) for pair, ids in by_pair.items() if len(ids) > 1}
    if duplicates:
        listed = "; ".join(
            f"user {user_id} at {issuer}: identity ids {', '.join(map(str, ids))}"
            for (user_id, issuer), ids in sorted(duplicates.items())
        )
        raise RuntimeError(
            "migration 0050 stopped: these accounts hold more than one SSO "
            f"identity at the same provider ({listed}). Nothing was changed. "
            "Keep one identity per account and provider (delete the others) and "
            "run the migration again; see docs/OPERATIONS.md, The SSO identity "
            "migration stops on duplicates."
        )
    op.create_index(INDEX, "sso_identities", ["user_id", "issuer"], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX, table_name="sso_identities")
