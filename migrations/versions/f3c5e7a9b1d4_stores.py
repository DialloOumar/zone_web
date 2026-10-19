"""Several stores: each with its own people, quantities and transfers

Revision ID: f3c5e7a9b1d4
Revises: e2b4d6f8a0c3
Create Date: 2026-10-19

Every movement and order so far belonged to the one store there was; it
becomes "Magasin principal", and the others are created from the app.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'f3c5e7a9b1d4'
down_revision = 'e2b4d6f8a0c3'
branch_labels = None
depends_on = None


def upgrade():
    stores = op.create_table(
        'stores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('site_id', sa.Integer(), sa.ForeignKey('sites.id'), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'store_managers',
        sa.Column('store_id', sa.Integer(), sa.ForeignKey('stores.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('ix_store_managers_user_id', 'store_managers', ['user_id'])

    with op.batch_alter_table('stock_movements') as b:
        b.add_column(sa.Column('store_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_stock_movements_store_id', 'stores', ['store_id'], ['id'])
        b.create_index('ix_stock_movements_store_id', ['store_id'])
    with op.batch_alter_table('purchase_orders') as b:
        b.add_column(sa.Column('store_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_purchase_orders_store_id', 'stores', ['store_id'], ['id'])

    op.create_table(
        'stock_transfers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('part_id', sa.Integer(), sa.ForeignKey('parts.id'), nullable=False),
        sa.Column('from_store_id', sa.Integer(), sa.ForeignKey('stores.id'), nullable=False),
        sa.Column('to_store_id', sa.Integer(), sa.ForeignKey('stores.id'), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False, server_default='transit'),
        sa.Column('sent_date', sa.String(10), nullable=False),
        sa.Column('sent_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('note', sa.String(255)),
        sa.Column('received_quantity', sa.Float(), nullable=True),
        sa.Column('received_date', sa.String(10), nullable=True),
        sa.Column('received_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('received_note', sa.String(255)),
        sa.Column('out_movement_id', sa.Integer(), sa.ForeignKey('stock_movements.id'), nullable=True),
        sa.Column('in_movement_id', sa.Integer(), sa.ForeignKey('stock_movements.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_stock_transfers_part_id', 'stock_transfers', ['part_id'])

    # The store there always was.
    op.bulk_insert(stores, [dict(id=1, name='Magasin principal', is_active=True,
                                 created_at=datetime.utcnow())])
    op.execute("UPDATE stock_movements SET store_id = 1")
    op.execute("UPDATE purchase_orders SET store_id = 1")
    # Keep the id sequence ahead of the row written by hand (PostgreSQL).
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("SELECT setval(pg_get_serial_sequence('stores', 'id'), (SELECT MAX(id) FROM stores))")


def downgrade():
    op.drop_index('ix_stock_transfers_part_id', 'stock_transfers')
    op.drop_table('stock_transfers')
    with op.batch_alter_table('purchase_orders') as b:
        b.drop_constraint('fk_purchase_orders_store_id', type_='foreignkey')
        b.drop_column('store_id')
    with op.batch_alter_table('stock_movements') as b:
        b.drop_index('ix_stock_movements_store_id')
        b.drop_constraint('fk_stock_movements_store_id', type_='foreignkey')
        b.drop_column('store_id')
    op.drop_index('ix_store_managers_user_id', 'store_managers')
    op.drop_table('store_managers')
    op.drop_table('stores')
