"""A store's requests for articles, which logistics turns into orders

Revision ID: a7c9e1b3d5f7
Revises: f3c5e7a9b1d4
Create Date: 2026-10-21

A store asks for articles and quantities, without supplier or price. The
head of logistics draws one or more orders from the request; each order
line names the request line it answers.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c9e1b3d5f7'
down_revision = 'f3c5e7a9b1d4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'purchase_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('number', sa.String(20), nullable=False, unique=True),
        sa.Column('store_id', sa.Integer(), sa.ForeignKey('stores.id'), nullable=False),
        sa.Column('date', sa.String(10), nullable=False),
        sa.Column('note', sa.String(255), nullable=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='open'),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('requester_name', sa.String(120), nullable=True),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('reason', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_purchase_requests_store_id', 'purchase_requests', ['store_id'])
    op.create_table(
        'purchase_request_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.Integer(), sa.ForeignKey('purchase_requests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('part_id', sa.Integer(), sa.ForeignKey('parts.id'), nullable=True),
        sa.Column('description', sa.String(160), nullable=False),
        sa.Column('reference', sa.String(60), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('buy_unit', sa.String(20), nullable=True),
        sa.Column('factor', sa.Float(), nullable=False, server_default='1'),
        sa.Column('new_unit', sa.String(20), nullable=True),
        sa.Column('vehicle_ids', sa.String(255), nullable=True),
    )
    op.create_index('ix_purchase_request_lines_request_id', 'purchase_request_lines', ['request_id'])
    with op.batch_alter_table('purchase_orders') as b:
        b.add_column(sa.Column('request_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_purchase_orders_request_id', 'purchase_requests', ['request_id'], ['id'])
        b.create_index('ix_purchase_orders_request_id', ['request_id'])
    with op.batch_alter_table('purchase_order_lines') as b:
        b.add_column(sa.Column('request_line_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_purchase_order_lines_request_line_id', 'purchase_request_lines',
                             ['request_line_id'], ['id'])
        b.create_index('ix_purchase_order_lines_request_line_id', ['request_line_id'])


def downgrade():
    with op.batch_alter_table('purchase_order_lines') as b:
        b.drop_index('ix_purchase_order_lines_request_line_id')
        b.drop_constraint('fk_purchase_order_lines_request_line_id', type_='foreignkey')
        b.drop_column('request_line_id')
    with op.batch_alter_table('purchase_orders') as b:
        b.drop_index('ix_purchase_orders_request_id')
        b.drop_constraint('fk_purchase_orders_request_id', type_='foreignkey')
        b.drop_column('request_id')
    op.drop_index('ix_purchase_request_lines_request_id', table_name='purchase_request_lines')
    op.drop_table('purchase_request_lines')
    op.drop_index('ix_purchase_requests_store_id', table_name='purchase_requests')
    op.drop_table('purchase_requests')
