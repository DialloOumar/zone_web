"""A user's stamp and signature strokes

Revision ID: b5d7f9a3c641
Revises: a4c6e8b1d532
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b5d7f9a3c641'
down_revision = 'a4c6e8b1d532'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('stamp_label', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('stamp_phone', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('signature_png', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('signature_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('signature_at')
        batch_op.drop_column('signature_png')
        batch_op.drop_column('stamp_phone')
        batch_op.drop_column('stamp_label')
