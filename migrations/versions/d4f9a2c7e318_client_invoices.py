"""Client invoices: a month's work frozen into a numbered bill

One row per bill issued to a client, with its lines copied in at issue time
so a later correction of a daily entry never moves a bill already sent.

Revision ID: d4f9a2c7e318
Revises: c3e8b1f4d927
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4f9a2c7e318'
down_revision = 'c3e8b1f4d927'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'client_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=20), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('due_date', sa.String(length=10), nullable=True),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='issued'),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number', name='uq_client_invoices_number'),
    )
    op.create_index('ix_client_invoices_client_id', 'client_invoices', ['client_id'])
    op.create_table(
        'client_invoice_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), nullable=True),
        sa.Column('vehicle_code', sa.String(length=30), nullable=False),
        sa.Column('category_label', sa.String(length=80), nullable=False),
        sa.Column('unit_type', sa.String(length=10), nullable=False),
        sa.Column('units', sa.Float(), nullable=False),
        sa.Column('rate', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['client_invoices.id']),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_invoice_lines_invoice_id', 'client_invoice_lines', ['invoice_id'])


def downgrade():
    op.drop_index('ix_client_invoice_lines_invoice_id', table_name='client_invoice_lines')
    op.drop_table('client_invoice_lines')
    op.drop_index('ix_client_invoices_client_id', table_name='client_invoices')
    op.drop_table('client_invoices')
