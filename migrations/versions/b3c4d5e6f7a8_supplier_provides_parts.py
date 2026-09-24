"""A supplier that sells parts, next to one that leases machines

Two independent flags now: a lessor, and a parts supplier a bon de commande
can be addressed to. The store only lists and creates parts suppliers; the
bills page creates both kinds.

Revision ID: b3c4d5e6f7a8
Revises: e8a0c3d6f975
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b3c4d5e6f7a8'
down_revision = 'e8a0c3d6f975'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.add_column(sa.Column('provides_parts', sa.Boolean(), nullable=False,
                                      server_default=sa.true()))
    # Nothing vanishes from the store's picker: everyone sells parts, except
    # a lessor that has never been on a bon de commande.
    op.execute("""
        UPDATE suppliers
           SET provides_parts = FALSE
         WHERE provides_machines = TRUE
           AND id NOT IN (SELECT DISTINCT supplier_id FROM purchase_orders)
    """)


def downgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.drop_column('provides_parts')
