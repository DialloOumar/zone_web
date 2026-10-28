"""One maintenance visit, several services, each with its own parts

A record kept one kind of service. It now keeps a list of them, and every
part it took from the store or bought outside says which of them it was
for. Every existing record gets its one type in the list, and its parts
are put under it.

Revision ID: b5d7f9a1c3e5
Revises: c4e6a8b0d2f9
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = 'b5d7f9a1c3e5'
down_revision = 'c4e6a8b0d2f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'maintenance_record_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('record_id', sa.Integer(), sa.ForeignKey('maintenance_records.id'), nullable=False),
        sa.Column('type', sa.String(length=40), nullable=False),
        sa.UniqueConstraint('record_id', 'type'),
    )
    op.create_index('ix_maintenance_record_types_record_id', 'maintenance_record_types', ['record_id'])
    op.create_index('ix_maintenance_record_types_type', 'maintenance_record_types', ['type'])
    with op.batch_alter_table('stock_movements') as b:
        b.add_column(sa.Column('service_type', sa.String(length=40), nullable=True))
    with op.batch_alter_table('service_part_purchases') as b:
        b.add_column(sa.Column('service_type', sa.String(length=40), nullable=True))

    op.execute("INSERT INTO maintenance_record_types (record_id, type) "
               "SELECT id, type FROM maintenance_records")
    op.execute("UPDATE stock_movements SET service_type = (SELECT r.type FROM maintenance_records r "
               "WHERE r.id = stock_movements.maintenance_record_id) "
               "WHERE maintenance_record_id IS NOT NULL")
    op.execute("UPDATE service_part_purchases SET service_type = (SELECT r.type FROM maintenance_records r "
               "WHERE r.id = service_part_purchases.record_id)")


def downgrade():
    # Only the first service of each record survives, as its type.
    with op.batch_alter_table('service_part_purchases') as b:
        b.drop_column('service_type')
    with op.batch_alter_table('stock_movements') as b:
        b.drop_column('service_type')
    op.drop_index('ix_maintenance_record_types_type', 'maintenance_record_types')
    op.drop_index('ix_maintenance_record_types_record_id', 'maintenance_record_types')
    op.drop_table('maintenance_record_types')
