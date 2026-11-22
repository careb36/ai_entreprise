"""The project wiki (issue #445; ``docs/archive/design/PROJECT_WIKI.md``): pages,
their versions, the links derived from their bodies and a company's wiki
settings, company-scoped. New tables only: nothing existing changes.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-15 20:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _company() -> sa.Column:
    return sa.Column(
        "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
    )


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column("slug", text(), nullable=False),
        sa.Column("kind", text(), nullable=False),
        sa.Column("title", text(), nullable=False),
        sa.Column("body", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("proposed_version_id", sa.Integer(), nullable=True),
        sa.Column("person_locked", sa.Boolean(), nullable=False),
        sa.Column("module_ref", text(), nullable=True),
        sa.Column("module_name", text(), nullable=True),
        sa.Column("module_path", text(), nullable=True),
        sa.Column("lint_reasons", sa.JSON(), nullable=True),
        sa.Column("linted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "slug", name="uq_wiki_pages_company_slug"),
    )
    op.create_index("ix_wiki_pages_company_id", "wiki_pages", ["company_id"])
    op.create_table(
        "wiki_page_versions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column(
            "page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("author_kind", text(), nullable=False),
        sa.Column("author", text(), nullable=False),
        sa.Column(
            "sprint_id", sa.Integer(), sa.ForeignKey("sprints.id"), nullable=True
        ),
        sa.Column("title", text(), nullable=False),
        sa.Column("body", text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("status", text(), nullable=False),
        sa.Column("decided_by", text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_reason", text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_wiki_page_versions_company_id", "wiki_page_versions", ["company_id"]
    )
    op.create_index("ix_wiki_page_versions_page_id", "wiki_page_versions", ["page_id"])
    op.create_table(
        "wiki_links",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        _company(),
        sa.Column(
            "page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False
        ),
        sa.Column("kind", text(), nullable=False),
        sa.Column("target", text(), nullable=False),
        sa.Column("sha256", text(), nullable=True),
    )
    op.create_index("ix_wiki_links_company_id", "wiki_links", ["company_id"])
    op.create_index("ix_wiki_links_page_id", "wiki_links", ["page_id"])
    op.create_table(
        "wiki_settings",
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("write_to_repo", sa.Boolean(), nullable=False),
        sa.Column("updated_by", text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("wiki_settings")
    op.drop_index("ix_wiki_links_page_id", table_name="wiki_links")
    op.drop_index("ix_wiki_links_company_id", table_name="wiki_links")
    op.drop_table("wiki_links")
    op.drop_index("ix_wiki_page_versions_page_id", table_name="wiki_page_versions")
    op.drop_index("ix_wiki_page_versions_company_id", table_name="wiki_page_versions")
    op.drop_table("wiki_page_versions")
    op.drop_index("ix_wiki_pages_company_id", table_name="wiki_pages")
    op.drop_table("wiki_pages")
