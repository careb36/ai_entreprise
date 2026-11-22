"""The stored code knowledge graph (issue #438, second slice;
``docs/archive/design/CODE_GRAPH.md``): ``code_graph_files`` (one row per company,
project folder and file, with the sha256 the workspace daemon read from
disk), ``code_graph_definitions`` and ``code_graph_references`` (what the
daemon parsed in each file, replaced with it when its sha256 changes) and
``code_graph_edges`` (``implements``, ``depends_on`` and ``verified``, each
with the record it was copied from).

New tables; no existing row changes.

Follows 0050 (PR #670).

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-18 22:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "code_graph_files",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("workspace_root", text(), nullable=False),
        sa.Column("path", text(), nullable=False),
        sa.Column("sha256", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "company_id", "workspace_root", "path", name="uq_code_graph_files_path"
        ),
    )
    op.create_index(
        "ix_code_graph_files_company_id", "code_graph_files", ["company_id"]
    )
    op.create_index("ix_code_graph_files_sha256", "code_graph_files", ["sha256"])

    for table in ("code_graph_definitions", "code_graph_references"):
        extra = (
            [
                sa.Column("line", sa.Integer(), nullable=False),
                sa.Column("depth", sa.Integer(), nullable=False),
            ]
            if table == "code_graph_definitions"
            else [sa.Column("count", sa.Integer(), nullable=False)]
        )
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column(
                "file_id",
                sa.Integer(),
                sa.ForeignKey("code_graph_files.id"),
                nullable=False,
            ),
            sa.Column(
                "company_id",
                sa.Integer(),
                sa.ForeignKey("companies.id"),
                nullable=False,
            ),
            sa.Column("name", text(), nullable=False),
            *extra,
        )
        op.create_index(f"ix_{table}_file_id", table, ["file_id"])
        op.create_index(f"ix_{table}_scope_name", table, ["company_id", "name"])

    op.create_table(
        "code_graph_edges",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("workspace_root", text(), nullable=False),
        sa.Column("kind", text(), nullable=False),
        sa.Column("from_ref", text(), nullable=False),
        sa.Column("to_ref", text(), nullable=False),
        sa.Column("source", text(), nullable=False),
        sa.Column("source_ref", text(), nullable=False),
        sa.Column("detail", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_code_graph_edges_company_id", "code_graph_edges", ["company_id"]
    )
    op.create_index(
        "ix_code_graph_edges_scope_source",
        "code_graph_edges",
        ["company_id", "workspace_root", "source"],
    )


def downgrade() -> None:
    op.drop_index("ix_code_graph_edges_scope_source", table_name="code_graph_edges")
    op.drop_index("ix_code_graph_edges_company_id", table_name="code_graph_edges")
    op.drop_table("code_graph_edges")
    for table in ("code_graph_references", "code_graph_definitions"):
        op.drop_index(f"ix_{table}_scope_name", table_name=table)
        op.drop_index(f"ix_{table}_file_id", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_code_graph_files_sha256", table_name="code_graph_files")
    op.drop_index("ix_code_graph_files_company_id", table_name="code_graph_files")
    op.drop_table("code_graph_files")
