"""A third signature on the purchase order: the gerant's, after finance

- roles.approves_manager: who gives it;
- purchase_orders.manager_by / manager_at: who gave it, and when.

Revision ID: b5d1f7a3c9e2
Revises: e3a7c9d1f5b2
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = 'b5d1f7a3c9e2'
down_revision = 'e3a7c9d1f5b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roles') as batch:
        batch.add_column(sa.Column('approves_manager', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('purchase_orders') as batch:
        batch.add_column(sa.Column('manager_by', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('manager_at', sa.DateTime(), nullable=True))
        batch.create_foreign_key('fk_purchase_orders_manager_by', 'users', ['manager_by'], ['id'])


def downgrade():
    with op.batch_alter_table('purchase_orders') as batch:
        batch.drop_constraint('fk_purchase_orders_manager_by', type_='foreignkey')
        batch.drop_column('manager_at')
        batch.drop_column('manager_by')
    with op.batch_alter_table('roles') as batch:
        batch.drop_column('approves_manager')
