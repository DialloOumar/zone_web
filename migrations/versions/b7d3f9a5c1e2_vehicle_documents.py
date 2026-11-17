"""A machine's papers

- vehicle_documents: one row per paper kept on a machine's file (carte grise,
  assurance...), with the name given to it and the stored file.

Revision ID: b7d3f9a5c1e2
Revises: a6c2e8f4b0d1
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7d3f9a5c1e2'
down_revision = 'a6c2e8f4b0d1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vehicle_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('file_key', sa.String(200), nullable=False),
        sa.Column('content_type', sa.String(60)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_vehicle_documents_vehicle_id', 'vehicle_documents', ['vehicle_id'])


def downgrade():
    op.drop_index('ix_vehicle_documents_vehicle_id', table_name='vehicle_documents')
    op.drop_table('vehicle_documents')
