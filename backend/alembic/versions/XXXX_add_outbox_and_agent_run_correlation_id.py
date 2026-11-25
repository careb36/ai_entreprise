"""Add correlation_id to outbox_events and agent_runs.

Revision ID: c41d0b3f2a01
Revises: <previous_revision>
Create Date: 2026-01-23

A stable UUID generated once when an outbox event is enqueued, carried
from publish through JetStream consume, daemon call, verification and
AgentRun. Nullable with backfill for existing rows; distinct from
Nats-Msg-Id which remains for dedup only.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c41d0b3f2a01"
down_revision = "<previous_revision>"  # set by alembic chain
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # Backfill existing rows with a fresh UUID; one per row is fine because
    # the marker only needs to be stable from this point forward for the
    # messages it has not yet delivered (completed ones are done anyway).
    op.execute(
        "UPDATE outbox_events SET correlation_id = gen_random_uuid() WHERE correlation_id IS NULL"
    )
    # The extension is assumed present; backend uses pgcrypto elsewhere.
    op.create_index(
        "ix_outbox_events_correlation_id",
        "outbox_events",
        ["correlation_id"],
    )

    op.add_column(
        "agent_runs",
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
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
