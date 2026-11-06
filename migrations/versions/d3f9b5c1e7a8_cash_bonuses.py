"""Bonuses paid by the cash box; an account of the plan per pay item

- pay_allowances: kind (allowance | bonus) and ledger_code;
- cash_movements: pay_item_id, the kind of bonus of a "prime" movement.

Revision ID: d3f9b5c1e7a8
Revises: c2e8a4b0d6f7
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3f9b5c1e7a8'
down_revision = 'c2e8a4b0d6f7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pay_allowances') as batch:
        batch.add_column(sa.Column('kind', sa.String(10), nullable=False, server_default='allowance'))
        batch.add_column(sa.Column('ledger_code', sa.String(12)))
    with op.batch_alter_table('cash_movements') as batch:
        batch.add_column(sa.Column('pay_item_id', sa.Integer()))
        batch.create_foreign_key('fk_cash_movements_pay_item_id', 'pay_allowances', ['pay_item_id'], ['id'])


def downgrade():
    # The bonuses go with their column: they would read as nothing else.
    op.execute("DELETE FROM cash_movements WHERE kind = 'prime'")
    with op.batch_alter_table('cash_movements') as batch:
        batch.drop_constraint('fk_cash_movements_pay_item_id', type_='foreignkey')
        batch.drop_column('pay_item_id')
    op.execute("DELETE FROM pay_allowances WHERE kind = 'bonus'")
    with op.batch_alter_table('pay_allowances') as batch:
        batch.drop_column('ledger_code')
        batch.drop_column('kind')
