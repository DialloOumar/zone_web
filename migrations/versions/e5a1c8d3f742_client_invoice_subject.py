"""Client invoices carry a subject and the client's reference

What the bill is for, in words the client recognises, and the reference the
client gave (a purchase order), both printed on the sheet.

Revision ID: e5a1c8d3f742
Revises: d4f9a2c7e318
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a1c8d3f742'
down_revision = 'd4f9a2c7e318'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('client_invoices') as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('client_ref', sa.String(length=60), nullable=True))


def downgrade():
    with op.batch_alter_table('client_invoices') as batch_op:
        batch_op.drop_column('client_ref')
        batch_op.drop_column('subject')
