"""A supplier's bill is settled in instalments

One bill, several payments: the cash box pays part of it in cash this week,
accounting wires the rest next month. Each instalment keeps its own date and
its own method instead of the four columns on the invoice, where a second
payment overwrote the first.

Where the money came from decides where it is entered, and the two never
overlap. A cost on the Dépenses page can settle a bill, and writes its
instalment beside itself (`expense_id` set). Anyone without access to the cash
box records their instalment on the invoice, and it stays empty. So a payment
is described in exactly one place and can never be counted twice.

What was already recorded moves across as a first instalment: nothing was ever
tied to the cash box before, so every migrated row keeps `expense_id` null.

Revision ID: c7e2b4d9f6a1
Revises: b3d6f1a8c4e2
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7e2b4d9f6a1'
down_revision = 'b3d6f1a8c4e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=True),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('expense_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('expense_id', name='uq_supplier_payments_expense_id'),
        sa.ForeignKeyConstraint(['invoice_id'], ['supplier_invoices.id'],
                                name='fk_supplier_payments_invoice_id'),
        sa.ForeignKeyConstraint(['expense_id'], ['expenses.id'],
                                name='fk_supplier_payments_expense_id'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_supplier_payments_created_by'),
    )
    op.create_index('ix_supplier_payments_invoice_id', 'supplier_payments',
                    ['invoice_id'])

    # Carry every payment already recorded across as the bill's first
    # instalment. A payment with no date kept none worth keeping, so it falls
    # back to the invoice's own date rather than being dropped.
    op.execute(sa.text("""
        INSERT INTO supplier_payments
            (invoice_id, date, amount, method, reference, expense_id,
             created_by, created_at)
        SELECT id,
               COALESCE(paid_date, date),
               paid_amount,
               payment_method,
               payment_reference,
               NULL,
               created_by,
               created_at
        FROM supplier_invoices
        WHERE paid_amount > 0
    """))

    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.drop_column('payment_reference')
        batch_op.drop_column('payment_method')
        batch_op.drop_column('paid_date')
        batch_op.drop_column('paid_amount')


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.add_column(sa.Column('paid_amount', sa.Integer(), nullable=False,
                                      server_default='0'))
        batch_op.add_column(sa.Column('paid_date', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('payment_method', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('payment_reference', sa.String(length=60), nullable=True))

    # Fold the instalments back into the one figure the invoice used to carry.
    # Only the latest instalment's date and method survive — the older ones had
    # nowhere to go in that shape, which is why this table exists.
    op.execute(sa.text("""
        UPDATE supplier_invoices SET paid_amount = COALESCE((
            SELECT SUM(amount) FROM supplier_payments
            WHERE supplier_payments.invoice_id = supplier_invoices.id), 0)
    """))
    op.execute(sa.text("""
        UPDATE supplier_invoices SET
            paid_date = (SELECT date FROM supplier_payments
                         WHERE supplier_payments.invoice_id = supplier_invoices.id
                         ORDER BY date DESC, id DESC LIMIT 1),
            payment_method = (SELECT method FROM supplier_payments
                              WHERE supplier_payments.invoice_id = supplier_invoices.id
                              ORDER BY date DESC, id DESC LIMIT 1),
            payment_reference = (SELECT reference FROM supplier_payments
                                 WHERE supplier_payments.invoice_id = supplier_invoices.id
                                 ORDER BY date DESC, id DESC LIMIT 1)
    """))

    op.drop_index('ix_supplier_payments_invoice_id', table_name='supplier_payments')
    op.drop_table('supplier_payments')
