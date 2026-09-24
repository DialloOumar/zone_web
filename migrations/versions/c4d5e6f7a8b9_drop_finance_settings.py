"""Drop the paramétrage comptable

The page that mapped each kind of money line to a default account is gone:
the code is chosen on each line where it is written, from the accounts the
finance team marked as used. Nothing read the table but that page.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('finance_settings')


def downgrade():
    op.create_table(
        'finance_settings',
        sa.Column('key', sa.String(length=60), primary_key=True),
        sa.Column('value', sa.String(length=60), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
