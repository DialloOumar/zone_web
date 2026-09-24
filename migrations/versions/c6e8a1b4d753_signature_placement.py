"""Where the signature sits on the stamp, and how big

Revision ID: c6e8a1b4d753
Revises: b5d7f9a3c641
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c6e8a1b4d753'
down_revision = 'b5d7f9a3c641'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('sig_dx', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('sig_dy', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('sig_scale', sa.Integer(), nullable=False, server_default='100'))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('sig_scale')
        batch_op.drop_column('sig_dy')
        batch_op.drop_column('sig_dx')
