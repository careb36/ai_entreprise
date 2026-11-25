"""Add correlation_id to outbox_events and agent_runs.

Revision ID: 0065
Revises: 0064
Create Date: 2026-01-23

A stable UUID generated once when an outbox event is enqueued, carried
from publish through JetStream consume, daemon call, verification and
AgentRun. Nullable with backfill for existing rows; distinct from
Nats-Msg-Id which remains for dedup only.
"""

import sqlalchemy as sa

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "correlation_id",
            sa.Uuid(),
            nullable=True,
        ),
    )
    # Backfill existing rows with a fresh UUID; one per row is fine because
    # the marker only needs to be stable from this point forward for the
    # messages it has not yet delivered (completed ones are done anyway).
    # gen_random_uuid() is PostgreSQL's (13+); elsewhere the worker assigns
    # one on first use (``OutboxEvent.ensure_correlation_id``).
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE outbox_events SET correlation_id = gen_random_uuid() "
            "WHERE correlation_id IS NULL"
        )
    op.create_index(
        "ix_outbox_events_correlation_id",
        "outbox_events",
        ["correlation_id"],
    )

    op.add_column(
        "agent_runs",
        sa.Column(
            "correlation_id",
            sa.Uuid(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agent_runs_correlation_id",
        "agent_runs",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_correlation_id", table_name="agent_runs")
    op.drop_column("agent_runs", "correlation_id")
    op.drop_index("ix_outbox_events_correlation_id", table_name="outbox_events")
    op.drop_column("outbox_events", "correlation_id")
