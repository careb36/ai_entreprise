"""The company's stack profile overrides and its last dependency installs
(issue #315): ``stack_profile`` (install, test, lint, start, e2e, port,
probe_path over the detected profile) and ``dependency_installs`` (the
manifests' sha256 as the last successful install saw them, per folder). Both
nullable: an existing company keeps the detected profile and installs on the
next manifest change.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stack_profile", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("dependency_installs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("dependency_installs")
        batch_op.drop_column("stack_profile")
