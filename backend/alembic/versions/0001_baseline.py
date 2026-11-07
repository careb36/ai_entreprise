"""Baseline: the schema as the models define it, converging whatever a database
already has.

Before this migration the backend created its tables with
``SQLModel.metadata.create_all`` at startup and patched older databases with
``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` statements by hand. A database may
therefore arrive here in three states: empty (the e2e, a fresh install), with
the tables ``db/init-db/init.sql`` created on the first container start, or
with the tables and patches of an older backend. This migration takes each
to the same place: a table the models define and the database lacks is
created whole; a column a table lacks is added (nullable, as the old patches
did); the nullability and default changes, the indexes and the backfills the
old startup applied are applied once more, idempotently, on Postgres.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 19:30:00 UTC
"""

from __future__ import annotations

import models  # noqa: F401 - registers every table on SQLModel.metadata
import sqlalchemy as sa
from sqlmodel import SQLModel

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# (table, column): made nullable and stripped of its default, as the old
# startup did for columns the models no longer require.
_MADE_OPTIONAL = (
    ("sprints", "duration_days"),
    ("companies", "default_sprint_length_days"),
    ("bug_reports", "category"),
    ("bug_reports", "severity"),
)
# Indexes the old startup created by hand on tables init.sql may have made.
_INDEXES = (
    (
        "ix_assistant_runtime_state_company_id",
        "assistant_runtime_state",
        ["company_id"],
        False,
    ),
    (
        "ix_assistant_runtime_state_assistant_key",
        "assistant_runtime_state",
        ["assistant_key"],
        False,
    ),
    ("ix_review_comments_external_id", "review_comments", ["external_id"], False),
    ("ix_audit_log_company_id", "audit_log", ["company_id"], False),
    ("ux_audit_log_company_chain_seq", "audit_log", ["company_id", "chain_seq"], True),
)
_POSTGRES_ONLY = (
    # audit rows written before company_id was a column carry it in details
    """
    UPDATE audit_log AS a SET company_id = c.id
    FROM companies AS c
    WHERE a.company_id IS NULL AND a.details->>'company_id' = c.id::text
    """,
)
_VECTOR_MEMORY = (
    "ALTER TABLE vector_memory ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "CREATE INDEX IF NOT EXISTS ix_vector_memory_company_id ON vector_memory (company_id)",
    """
    UPDATE vector_memory AS m SET company_id = c.id
    FROM companies AS c
    WHERE m.company_id IS NULL AND m.metadata->>'company_id' = c.id::text
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind)
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            # Added nullable: an existing row has no value for it, and the
            # application fills it. The model's NOT NULL holds for new rows
            # through the ORM, as it did under the old patches.
            op.add_column(
                table.name,
                sa.Column(column.name, column.type, nullable=True),
            )
            if column.foreign_keys:
                fk = next(iter(column.foreign_keys))
                op.create_foreign_key(
                    None,
                    table.name,
                    fk.column.table.name,
                    [column.name],
                    [fk.column.name],
                )
    inspector = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        for table_name, column_name in _MADE_OPTIONAL:
            if not inspector.has_table(table_name):
                continue
            column = next(
                (
                    c
                    for c in inspector.get_columns(table_name)
                    if c["name"] == column_name
                ),
                None,
            )
            if column is None:
                continue
            if not column["nullable"]:
                op.alter_column(table_name, column_name, nullable=True)
            if column.get("default") is not None:
                op.alter_column(table_name, column_name, server_default=None)
        for name, table_name, columns, unique in _INDEXES:
            if not inspector.has_table(table_name):
                continue
            if name in {i["name"] for i in inspector.get_indexes(table_name)}:
                continue
            op.create_index(name, table_name, columns, unique=unique)
        for statement in _POSTGRES_ONLY:
            op.execute(sa.text(statement))
        if inspector.has_table("vector_memory"):
            for statement in _VECTOR_MEMORY:
                op.execute(sa.text(statement))


def downgrade() -> None:
    # The baseline has nothing before it: a downgrade drops the schema the
    # models define, and only that (vector_memory and the rest of init.sql's
    # tables are not the migrations' to drop).
    bind = op.get_bind()
    for table in reversed(SQLModel.metadata.sorted_tables):
        table.drop(bind, checkfirst=True)
