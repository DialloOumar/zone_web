"""Suppliers and the invoices the company owes them

The bills that come in, which is the opposite direction from the facturation
screen: a garage or a parts shop sends a paper, it is recorded with its amount
and its due date, and the page says what is still owed.

A register, not a till: nothing here touches the cash box or the expense
ledger, so no existing figure moves because of this migration.

Only `paid_amount` is stored about a payment. Paid, part-paid and untouched all
follow from it, so a status column can never drift away from the figures.

Revision ID: a1c4e7b9d2f0
Revises: f5a6b7c8d9e0
Create Date: 2026-09-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c4e7b9d2f0'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('contact', sa.String(length=80), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_suppliers_name'),
    )
    op.create_table(
        'supplier_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=60), nullable=True),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('due_date', sa.String(length=10), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=5), nullable=False, server_default='GNF'),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('paid_amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('paid_date', sa.String(length=10), nullable=True),
        sa.Column('payment_method', sa.String(length=20), nullable=True),
        sa.Column('payment_reference', sa.String(length=60), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'],
                                name='fk_supplier_invoices_supplier_id'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_supplier_invoices_created_by'),
    )
    # The two questions the page asks of the table: this supplier's bills, and
    # what falls due next.
    op.create_index('ix_supplier_invoices_supplier_id', 'supplier_invoices',
                    ['supplier_id'])
    op.create_index('ix_supplier_invoices_date', 'supplier_invoices', ['date'])


def downgrade():
    op.drop_index('ix_supplier_invoices_date', table_name='supplier_invoices')
    op.drop_index('ix_supplier_invoices_supplier_id', table_name='supplier_invoices')
    op.drop_table('supplier_invoices')
    op.drop_table('suppliers')
