"""Finance settings: the accounting map

Key/value rows for the Finance workspace: which account of the plan each
kind of money line lands on, and the company's TVA position. Kept apart from
app_settings so the ops Administration page never shows them.

Revision ID: b7d2e4a9c631
Revises: a1f3c7e9b205
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7d2e4a9c631'
down_revision = 'a1f3c7e9b205'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'finance_settings',
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('value', sa.String(length=60), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id']),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('finance_settings')
