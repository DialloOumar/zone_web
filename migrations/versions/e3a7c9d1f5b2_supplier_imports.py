"""An old supplier ledger read from Excel, under review before import

- supplier_imports: the file as read, the answers given so far, and whether
  it has been imported.

Revision ID: e3a7c9d1f5b2
Revises: e1b7d3f9a5c6
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3a7c9d1f5b2'
down_revision = 'e1b7d3f9a5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_imports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('as_of', sa.String(10), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('choices', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(10), nullable=False, server_default='draft'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('done_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('done_at', sa.DateTime()),
    )


def downgrade():
    op.drop_table('supplier_imports')
