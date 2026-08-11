"""Parts store: parts catalogue, stock movements, and the ledger link

A part holds no quantity of its own — what is on hand comes from its movements,
the same arrangement as a citerne and its fuel movements. Buying parts is the
only moment they cost money, so only an 'entree' gets an Expense row, linked
through expenses.stock_movement_id.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9a0b1c2d3e4'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'parts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False, server_default='piece'),
        sa.Column('unit_price', sa.Integer(), nullable=True),
        sa.Column('reorder_level', sa.Float(), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_parts_created_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_parts_is_active', 'parts', ['is_active'])

    op.create_table(
        'stock_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('part_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('unit_price', sa.Integer(), nullable=True),
        sa.Column('unit_value', sa.Integer(), nullable=True),
        sa.Column('supplier', sa.String(length=120), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('maintenance_record_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['part_id'], ['parts.id'], name='fk_stock_movements_part_id'),
        sa.ForeignKeyConstraint(['maintenance_record_id'], ['maintenance_records.id'],
                                name='fk_stock_movements_record_id'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_stock_movements_created_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    # The two reads that matter: a part's ledger (ordered by date) and the parts
    # of one service.
    op.create_index('ix_stock_movements_part_date', 'stock_movements', ['part_id', 'date'])
    op.create_index('ix_stock_movements_record', 'stock_movements', ['maintenance_record_id'])

    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('stock_movement_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_expenses_stock_movement_id', 'stock_movements',
                                    ['stock_movement_id'], ['id'])


def downgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_constraint('fk_expenses_stock_movement_id', type_='foreignkey')
        batch_op.drop_column('stock_movement_id')

    op.drop_index('ix_stock_movements_record', table_name='stock_movements')
    op.drop_index('ix_stock_movements_part_date', table_name='stock_movements')
    op.drop_table('stock_movements')
    op.drop_index('ix_parts_is_active', table_name='parts')
    op.drop_table('parts')
