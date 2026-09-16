"""Suppliers get a kind, and a code that says which

Two kinds: permanent -- the garages and lessors the company works with all
year -- and divers, the supplier of a day. Each supplier carries a code issued
by the app, never typed: FP-001, FP-002... for the permanent ones, FD-001,
FD-002... for the others, so a glance at the code says which it is.

Every supplier already recorded starts as divers and is coded FD-001 onwards
in the order it was created; the habitual ones are moved to permanent by hand
afterwards, and take an FP code then. Nobody is declared permanent on the
company's behalf.

Revision ID: d2b8f5a1c794
Revises: c9a4e7f2d318
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd2b8f5a1c794'
down_revision = 'c9a4e7f2d318'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=10), nullable=False,
                                      server_default='divers'))
        batch_op.add_column(sa.Column('code', sa.String(length=12), nullable=True))

    # Code the suppliers that exist, oldest first, so their numbers follow the
    # order they were recorded in.
    bind = op.get_bind()
    ids = [r[0] for r in bind.execute(sa.text("SELECT id FROM suppliers ORDER BY id")).fetchall()]
    for n, sid in enumerate(ids, start=1):
        bind.execute(sa.text("UPDATE suppliers SET code = :code WHERE id = :id"),
                     {"code": "FD-%03d" % n, "id": sid})

    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.alter_column('code', existing_type=sa.String(length=12), nullable=False)
        batch_op.create_unique_constraint('uq_suppliers_code', ['code'])


def downgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.drop_constraint('uq_suppliers_code', type_='unique')
        batch_op.drop_column('code')
        batch_op.drop_column('kind')
