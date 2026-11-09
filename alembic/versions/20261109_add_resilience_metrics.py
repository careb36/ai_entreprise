"""add resilience metrics

Revision ID: 1a2b3c4d5e6f
Revises: 
Create Date: 2026-11-09 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_audit_logs', sa.Column('llm_timeout_duration_ms', sa.Integer(), nullable=True))
    op.add_column('ai_audit_logs', sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('ai_audit_logs', sa.Column('self_correction_iterations', sa.Integer(), server_default='0', nullable=False))


def downgrade():
    op.drop_column('ai_audit_logs', 'self_correction_iterations')
    op.drop_column('ai_audit_logs', 'retry_count')
    op.drop_column('ai_audit_logs', 'llm_timeout_duration_ms')
