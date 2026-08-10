"""Drop search_vector from documents table

Revision ID: 002_drop_tsvector
Revises: 001_agentic_pipeline
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002_drop_tsvector'
down_revision = '001_agentic_pipeline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the index and the column
    op.drop_index('idx_documents_search_vector', table_name='documents', postgresql_using='gin', if_exists=True)
    op.drop_column('documents', 'search_vector')


def downgrade() -> None:
    # Add back the column and index
    op.add_column('documents', sa.Column('search_vector', postgresql.TSVECTOR(), autoincrement=False, nullable=True))
    op.create_index('idx_documents_search_vector', 'documents', ['search_vector'], unique=False, postgresql_using='gin')
