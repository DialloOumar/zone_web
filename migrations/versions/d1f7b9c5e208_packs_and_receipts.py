"""Packs on parts, receipts against purchase order lines

A part may be bought in a pack (a 200-litre drum) while the store counts
units; an order line says which it is written in, and remembers what came
in. A receipt movement points at the line it settled.

Revision ID: d1f7b9c5e208
Revises: c9e5a7b3d186
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd1f7b9c5e208'
down_revision = 'c9e5a7b3d186'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('parts') as batch_op:
        batch_op.add_column(sa.Column('pack_name', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('pack_size', sa.Float(), nullable=True))
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.add_column(sa.Column('in_pack', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('received_qty', sa.Float(), nullable=False, server_default='0'))
    with op.batch_alter_table('stock_movements') as batch_op:
        batch_op.add_column(sa.Column('purchase_line_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_stock_movements_purchase_line', 'purchase_order_lines',
                                    ['purchase_line_id'], ['id'])


def downgrade():
    with op.batch_alter_table('stock_movements') as batch_op:
        batch_op.drop_constraint('fk_stock_movements_purchase_line', type_='foreignkey')
        batch_op.drop_column('purchase_line_id')
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.drop_column('received_qty')
        batch_op.drop_column('in_pack')
    with op.batch_alter_table('parts') as batch_op:
        batch_op.drop_column('pack_size')
        batch_op.drop_column('pack_name')
