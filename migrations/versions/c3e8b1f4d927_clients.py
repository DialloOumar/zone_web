"""Clients and their per-machine dated rates

A client is who the machines work for, the mirror of a lessor supplier. Each
machine placed with a client is priced there, GNF per worked unit, as dated
history. The earlier per-fleet-and-category rates stay in fleet_rates,
unread from now on.

Revision ID: c3e8b1f4d927
Revises: b7d2e4a9c631
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e8b1f4d927'
down_revision = 'b7d2e4a9c631'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'clients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('code', sa.String(length=12), nullable=False),
        sa.Column('contact', sa.String(length=80), nullable=True),
        sa.Column('address', sa.String(length=200), nullable=True),
        sa.Column('tax_id', sa.String(length=40), nullable=True),
        sa.Column('rccm', sa.String(length=40), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_clients_name'),
        sa.UniqueConstraint('code', name='uq_clients_code'),
    )
    op.create_table(
        'client_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), nullable=False),
        sa.Column('rate_per_unit', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_rates_client_id', 'client_rates', ['client_id'])
    op.create_index('ix_client_rates_vehicle_id', 'client_rates', ['vehicle_id'])
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('client_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_vehicles_client_id', 'clients', ['client_id'], ['id'])


def downgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_constraint('fk_vehicles_client_id', type_='foreignkey')
        batch_op.drop_column('client_id')
    op.drop_index('ix_client_rates_vehicle_id', table_name='client_rates')
    op.drop_index('ix_client_rates_client_id', table_name='client_rates')
    op.drop_table('client_rates')
    op.drop_table('clients')
