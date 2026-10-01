"""The error journal

Revision ID: d0f2a4b6c8e0
Revises: c9e1a3b5d7f9
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd0f2a4b6c8e0'
down_revision = 'c9e1a3b5d7f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'error_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ref', sa.String(length=12), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('method', sa.String(length=8), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=80), nullable=True),
        sa.Column('message', sa.String(length=300), nullable=False),
        sa.Column('traceback', sa.Text(), nullable=False),
        sa.Column('form_keys', sa.String(length=300), nullable=True),
    )
    op.create_index('ix_error_events_created_at', 'error_events', ['created_at'])


def downgrade():
    op.drop_index('ix_error_events_created_at', table_name='error_events')
    op.drop_table('error_events')
