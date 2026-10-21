"""Client bills with cents, free periods, free lines and the client's paperwork

Revision ID: c4e6a8b0d2f9
Revises: a7c9e1b3d5f7
Create Date: 2026-10-21

The amounts of client bills, their lines and their payments become numeric
with two decimals; every existing amount keeps its value (1 500 000 becomes
1 500 000.00). A bill gains the exact days it covers and the fields a
client's paperwork asks for; a line may be a free one, typed rather than
worked out from the machines; a client gains an e-mail, a P.O. box and
whom its bills are addressed to.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e6a8b0d2f9'
down_revision = 'a7c9e1b3d5f7'
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 2)


def upgrade():
    with op.batch_alter_table('client_invoices') as b:
        b.alter_column('total', existing_type=sa.Integer(), type_=MONEY, existing_nullable=False)
        b.add_column(sa.Column('period_start', sa.String(10), nullable=True))
        b.add_column(sa.Column('period_end', sa.String(10), nullable=True))
        b.add_column(sa.Column('contract_no', sa.String(60), nullable=True))
        b.add_column(sa.Column('attention', sa.String(120), nullable=True))
        b.add_column(sa.Column('equipments', sa.String(120), nullable=True))
        b.add_column(sa.Column('work_purpose', sa.String(80), nullable=True))
        b.add_column(sa.Column('equipment_type', sa.String(80), nullable=True))
        b.add_column(sa.Column('availability', sa.String(40), nullable=True))
        b.add_column(sa.Column('intro', sa.String(255), nullable=True))
    with op.batch_alter_table('client_invoice_lines') as b:
        b.add_column(sa.Column('kind', sa.String(10), nullable=False, server_default='machine'))
        b.add_column(sa.Column('title', sa.String(160), nullable=True))
        b.add_column(sa.Column('description', sa.Text(), nullable=True))
        b.add_column(sa.Column('discount', MONEY, nullable=False, server_default='0'))
        b.alter_column('vehicle_code', existing_type=sa.String(30), nullable=True)
        b.alter_column('category_label', existing_type=sa.String(80), nullable=True)
        b.alter_column('unit_type', existing_type=sa.String(10), nullable=True)
        b.alter_column('rate', existing_type=sa.Integer(), type_=MONEY, existing_nullable=False)
        b.alter_column('amount', existing_type=sa.Integer(), type_=MONEY, existing_nullable=False)
    with op.batch_alter_table('client_payments') as b:
        b.alter_column('amount', existing_type=sa.Integer(), type_=MONEY, existing_nullable=False)
    with op.batch_alter_table('clients') as b:
        b.add_column(sa.Column('email', sa.String(120), nullable=True))
        b.add_column(sa.Column('po_box', sa.String(60), nullable=True))
        b.add_column(sa.Column('attention', sa.String(120), nullable=True))


def downgrade():
    # Cents are lost going back: amounts are rounded to whole francs.
    with op.batch_alter_table('clients') as b:
        b.drop_column('attention')
        b.drop_column('po_box')
        b.drop_column('email')
    with op.batch_alter_table('client_payments') as b:
        b.alter_column('amount', existing_type=MONEY, type_=sa.Integer(), existing_nullable=False,
                       postgresql_using='round(amount)::integer')
    with op.batch_alter_table('client_invoice_lines') as b:
        b.alter_column('amount', existing_type=MONEY, type_=sa.Integer(), existing_nullable=False,
                       postgresql_using='round(amount)::integer')
        b.alter_column('rate', existing_type=MONEY, type_=sa.Integer(), existing_nullable=False,
                       postgresql_using='round(rate)::integer')
        b.alter_column('unit_type', existing_type=sa.String(10), nullable=False)
        b.alter_column('category_label', existing_type=sa.String(80), nullable=False)
        b.alter_column('vehicle_code', existing_type=sa.String(30), nullable=False)
        b.drop_column('discount')
        b.drop_column('description')
        b.drop_column('title')
        b.drop_column('kind')
    with op.batch_alter_table('client_invoices') as b:
        for c in ('intro', 'availability', 'equipment_type', 'work_purpose', 'equipments',
                  'attention', 'contract_no', 'period_end', 'period_start'):
            b.drop_column(c)
        b.alter_column('total', existing_type=MONEY, type_=sa.Integer(), existing_nullable=False,
                       postgresql_using='round(total)::integer')
