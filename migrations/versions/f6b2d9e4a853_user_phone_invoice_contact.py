"""A phone on the user, and the contact person on a client invoice

The person who issues a bill is its contact by default, printed with their
phone; both are copied onto the bill so it still says so later.

Revision ID: f6b2d9e4a853
Revises: e5a1c8d3f742
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6b2d9e4a853'
down_revision = 'e5a1c8d3f742'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=30), nullable=True))
    with op.batch_alter_table('client_invoices') as batch_op:
        batch_op.add_column(sa.Column('contact_name', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('contact_phone', sa.String(length=60), nullable=True))


def downgrade():
    with op.batch_alter_table('client_invoices') as batch_op:
        batch_op.drop_column('contact_phone')
        batch_op.drop_column('contact_name')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('phone')
