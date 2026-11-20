"""Each memory vector's source: ``vector_memory.embedding_model`` (issue #755).

Vectors from different embedding sources do not compare: the local semantic
model's (``multilingual-e5-small``), the hash vector's
(``local-fallback-embedding``) and Gemini's live in the same 1536-wide column,
padded with zeros, but the cosine distance between two of different sources
is noise. The proxy names the source of every vector it answers; the backend
now records it beside the vector, a search compares only rows of the query's
model, and the rows of another source are re-embedded once the semantic model
answers (``MemoryClient.reembed_stale``).

Existing rows are marked ``local-fallback-embedding``: before issue #751 the
only vectors that could be stored were the hash vector's (Gemini's 768 values
did not fit the column and the insert failed), and #751 kept the hash vector
as the default. They are re-embedded at the next start with the model.

``vector_memory`` is created by ``db/init-db/init.sql`` where pgvector is
installed, and may be absent (SQLite, or Postgres without pgvector): then this
migration does nothing, as the baseline does for that table.

Follows 0056 (sprint objective).

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-20 12:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

HASH_MODEL = "local-fallback-embedding"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("vector_memory"):
        return
    op.execute(
        sa.text(
            "ALTER TABLE vector_memory ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE vector_memory SET embedding_model = :hash WHERE embedding_model IS NULL"
        ).bindparams(hash=HASH_MODEL)
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_vector_memory_embedding_model "
            "ON vector_memory (embedding_model)"
        )
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("vector_memory"):
        return
    op.execute(sa.text("DROP INDEX IF EXISTS ix_vector_memory_embedding_model"))
    op.execute(sa.text("ALTER TABLE vector_memory DROP COLUMN IF EXISTS embedding_model"))
