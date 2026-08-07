"""Add reasoning and agent_steps to processing_logs; add resolve to pipeline_stage enum

Revision ID: 001_agentic_pipeline
Revises: 
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001_agentic_pipeline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'resolve' value to the pipeline_stage enum
    op.execute("ALTER TYPE pipelinestage ADD VALUE IF NOT EXISTS 'resolve'")

    # Add reasoning and agent_steps columns to processing_logs
    op.add_column(
        'processing_logs',
        sa.Column('reasoning', sa.Text(), nullable=True),
    )
    op.add_column(
        'processing_logs',
        sa.Column('agent_steps', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('processing_logs', 'agent_steps')
    op.drop_column('processing_logs', 'reasoning')
    # Note: PostgreSQL does not support removing values from an enum type without
    # recreating it. Downgrade leaves the 'resolve' enum value in place.
