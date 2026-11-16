"""The files an architecture's write left in the repository (issue #329,
finding F4 of docs/retrospectives/2026-11-self-hosting.md).

``architectures.written_files`` keeps, per file, the path and the sha256 the
daemon reported after writing: ``docs/ARCHITECTURE.md`` and one
``docs/adr/NNNN-<slug>.md`` per decision. The document's own path and hash
stay in ``written_path`` and ``written_sha256``, so the earlier reads keep
working; a row written before this migration has no list and reads as ``[]``.

Follows 0038 (the company's architecture).

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-16 00:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("architectures", sa.Column("written_files", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("architectures", schema=None) as batch:
        batch.drop_column("written_files")
