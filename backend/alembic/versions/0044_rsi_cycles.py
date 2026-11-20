"""The self-improvement cycles (``docs/design/rsi-loop.md``, PR 10 of section 8;
REQ-22 to REQ-26): ``rsi_cycles``, one row per cycle a company runs for a
finding, with B and C, the status, the decision and its reasons, the diff's
size the daemon observed, the token budget and the reported tokens, the kept
files with the hashes the backend computed beside the ones the runner
claimed, and who merged. Every change is audited
(``services/management/rsi_cycle.py``).

New table; no existing row changes.

Follows 0043 (the findings).

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-17 18:00:00 UTC
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    json = sa.JSON(none_as_null=True)
    op.create_table(
        "rsi_cycles",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("cycle_key", text(), nullable=False),
        sa.Column(
            "finding_id", sa.Integer(), sa.ForeignKey("findings.id"), nullable=True
        ),
        sa.Column("source", text(), nullable=False),
        sa.Column("status", text(), nullable=False),
        sa.Column("baseline_sha", text(), nullable=True),
        sa.Column("candidate_sha", text(), nullable=True),
        sa.Column("sprint_id", sa.Integer(), nullable=True),
        sa.Column("integration_id", sa.Integer(), nullable=True),
        sa.Column("pull_request", sa.Integer(), nullable=True),
        sa.Column("decision", text(), nullable=True),
        sa.Column("reasons", json, nullable=True),
        sa.Column("metrics", json, nullable=True),
        sa.Column("diff_files", sa.Integer(), nullable=True),
        sa.Column("diff_lines", sa.Integer(), nullable=True),
        sa.Column("diff_sha256", text(), nullable=True),
        sa.Column("token_budget", sa.Integer(), nullable=True),
        sa.Column("tokens_reported", sa.Integer(), nullable=False),
        sa.Column("bench_tokens", sa.Integer(), nullable=True),
        sa.Column("kept_files", json, nullable=True),
        sa.Column("record", json, nullable=True),
        sa.Column("merged_sha", text(), nullable=True),
        sa.Column("merged_by", text(), nullable=True),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.Column("started_by", text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("resumed_by", text(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "company_id", "cycle_key", name="uq_rsi_cycles_company_key"
        ),
    )
    op.create_index(
        "ix_rsi_cycles_company_id", "rsi_cycles", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_rsi_cycles_company_id", table_name="rsi_cycles")
    op.drop_table("rsi_cycles")
