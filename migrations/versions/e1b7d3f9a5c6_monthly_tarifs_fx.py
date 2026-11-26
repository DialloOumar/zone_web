"""Monthly tarifs, half days, dollar bills

- billing_tags.basis: per worked unit, or per month (price / days of the
  month x days worked)
- billing_tags.currency: GNF or USD
- daily_entries.half_day: the machine served half the day
- client_invoices.currency / fx_rate / fx_total: a bill written in dollars,
  converted to GNF at the BCRG rate written on it

Revision ID: e1b7d3f9a5c6
Revises: d9f5b1c7e3a4
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1b7d3f9a5c6'
down_revision = 'd9f5b1c7e3a4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('billing_tags', sa.Column('basis', sa.String(5), nullable=False, server_default='unit'))
    op.add_column('billing_tags', sa.Column('currency', sa.String(3), nullable=False, server_default='GNF'))
    op.add_column('daily_entries', sa.Column('half_day', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('client_invoices', sa.Column('currency', sa.String(3), nullable=False, server_default='GNF'))
    op.add_column('client_invoices', sa.Column('fx_rate', sa.Numeric(12, 4), nullable=True))
    op.add_column('client_invoices', sa.Column('fx_total', sa.Numeric(18, 2), nullable=True))


def downgrade():
    op.drop_column('client_invoices', 'fx_total')
    op.drop_column('client_invoices', 'fx_rate')
    op.drop_column('client_invoices', 'currency')
    op.drop_column('daily_entries', 'half_day')
    op.drop_column('billing_tags', 'currency')
    op.drop_column('billing_tags', 'basis')
