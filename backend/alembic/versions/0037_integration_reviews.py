"""A person's review of a sprint's integration (F7 of the self-hosting
retrospective, ``docs/retrospectives/2026-11-self-hosting.md``):
``integration_reviews`` (a submitted review, with the commit, merge base and
diff hash the daemon observed) and ``integration_review_comments`` (a note on
a line of the integration's diff, with the file's sha256 on that side), and
``companies.integration_review_required``, false for every existing company,
so nothing changes until an Owner asks for an approving review before a merge.

Follows 0036 (segregation of duties, #543); renumbered at merge.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-15 23:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _scope(text) -> list[sa.Column]:
    """The columns both tables open with: the company, the sprint, the integration."""
    return [
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column(
            "sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=False
        ),
        sa.Column(
            "integration_id",
            sa.Integer(),
            sa.ForeignKey("sprint_integrations.id"),
            nullable=False,
        ),
    ]


def _indexes(table: str) -> None:
    for column in ("company_id", "sprint_id", "integration_id"):
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "integration_reviews",
        *_scope(text),
        sa.Column("event", text(), nullable=False),
        sa.Column("body", text(), nullable=True),
        sa.Column("outcome", text(), nullable=False),
        sa.Column("commit", text(), nullable=False),
        sa.Column("merge_base", text(), nullable=True),
        sa.Column("diff_sha256", text(), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False),
        sa.Column("published_url", text(), nullable=True),
        sa.Column("sod", text(), nullable=True),
        sa.Column("author_name", text(), nullable=False),
        sa.Column(
            "author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "audit_log_id", sa.Integer(), sa.ForeignKey("audit_log.id"), nullable=True
        ),
    )
    _indexes("integration_reviews")
    op.create_table(
        "integration_review_comments",
        *_scope(text),
        sa.Column("path", text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("side", text(), nullable=False),
        sa.Column("body", text(), nullable=False),
        sa.Column("commit", text(), nullable=False),
        sa.Column("file_sha256", text(), nullable=True),
        sa.Column("author_name", text(), nullable=False),
        sa.Column(
            "author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "review_id",
            sa.Integer(),
            sa.ForeignKey("integration_reviews.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    _indexes("integration_review_comments")
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "integration_review_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_column("integration_review_required")
    for table in ("integration_review_comments", "integration_reviews"):
        for column in ("integration_id", "sprint_id", "company_id"):
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
