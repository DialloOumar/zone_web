"""Roles sign purchase orders; suppliers carry an e-mail and an address

Two boxes on a role -- responsable logistique, responsable financier -- name
who gives each approval on a bon de commande. The supplier's e-mail and
address are what the printed order carries.

Revision ID: b8d4f6a2c975
Revises: a7c3e5b9d164
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d4f6a2c975'
down_revision = 'a7c3e5b9d164'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roles') as batch_op:
        batch_op.add_column(sa.Column('approves_logistics', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('approves_finance', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('address', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.drop_column('address')
        batch_op.drop_column('email')
    with op.batch_alter_table('roles') as batch_op:
        batch_op.drop_column('approves_finance')
        batch_op.drop_column('approves_logistics')
