"""Vehicle soft delete: add deleted_at, free the code of a deleted machine

Archive (is_active=False) stays reversible and visible in the archived list.
A real delete sets deleted_at: the machine disappears from every UI but its
row stays for history. Code uniqueness moves from an all-rows constraint to a
partial unique index over live rows only (deleted_at IS NULL), so a deleted
machine's code can be reused.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6d7e8f9a0b1'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
        batch_op.drop_constraint('uq_vehicles_code', type_='unique')
    op.create_index('uq_vehicles_code_live', 'vehicles', ['code'], unique=True,
                    sqlite_where=sa.text('deleted_at IS NULL'),
                    postgresql_where=sa.text('deleted_at IS NULL'))


def downgrade():
    op.drop_index('uq_vehicles_code_live', table_name='vehicles')
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.create_unique_constraint('uq_vehicles_code', ['code'])
        batch_op.drop_column('deleted_at')
