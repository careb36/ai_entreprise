"""Forge policy per company (issue #727): ``companies.forge_policy`` and
``sprint_integrations.forge_policy``.

``companies.forge_policy`` says whether the company's git may contact a forge:
``none`` (never), ``fetch`` (read the base from its remote before a new
branch) or ``publish`` (fetch, plus the Manager's publish actions). A company
created from now on starts at ``none`` (the model's default and the column's
server default): privacy by default, GDPR art. 25.

**Every company that exists when this runs is set to ``publish``**, which is
what the platform did for all of them until now (the daemon fetched the base
whenever the repository had an ``origin``, and a Manager could publish), so
no behaviour changes silently. An Owner lowers it with
``PUT /v1/companies/{id}/forge_policy`` (audited ``forge_policy_changed``);
the CHANGELOG says how.

``sprint_integrations.forge_policy`` records the policy an integration ran
under; rows recorded before this migration keep NULL (not observed).

Follows 0051 (code graph, #438). Open PR #711 takes 0052 and #703 takes
0053: rechain ``down_revision`` onto the newest of them at merge.

Revision ID: 0054
Revises: 0051
Create Date: 2026-11-19 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0054"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "forge_policy", text(), nullable=False, server_default="none"
            )
        )
    # The companies that exist now keep what they had: fetch and publish.
    op.execute(sa.text("UPDATE companies SET forge_policy = 'publish'"))
    with op.batch_alter_table("sprint_integrations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("forge_policy", text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sprint_integrations", schema=None) as batch_op:
        batch_op.drop_column("forge_policy")
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("forge_policy")
