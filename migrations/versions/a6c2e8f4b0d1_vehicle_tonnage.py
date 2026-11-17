"""A machine's tonnage

- vehicles.tonnage: what the machine carries or weighs, in tonnes; blank
  until someone enters it.

Revision ID: a6c2e8f4b0d1
Revises: f5b1d7e3a9c0
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6c2e8f4b0d1'
down_revision = 'f5b1d7e3a9c0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch:
        batch.add_column(sa.Column('tonnage', sa.Float()))


def downgrade():
    with op.batch_alter_table('vehicles') as batch:
        batch.drop_column('tonnage')
