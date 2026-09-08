"""A person's surname and given name, kept apart

They were one line of text. A list of people is sorted and searched by
surname, and neither is possible once the two are run together — so they are
two columns, and how a person is written on screen is decided in one place,
by Staff.name.

What is already recorded moves into the surname whole. Splitting "Mamadou
Diallo" would mean deciding which half is which, and both orders are in daily
use here; guessing would put the wrong name on somebody's advance. Whoever
knows the person sets it right on the next edit, and until then the full name
still reads exactly as it was typed.

Revision ID: e1b7c4d2a805
Revises: d8f3a6c1e094
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e1b7c4d2a805'
down_revision = 'd8f3a6c1e094'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('staff') as batch_op:
        batch_op.add_column(sa.Column('last_name', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('first_name', sa.String(length=80), nullable=True))

    op.execute(sa.text("UPDATE staff SET last_name = name"))

    with op.batch_alter_table('staff') as batch_op:
        batch_op.alter_column('last_name', existing_type=sa.String(length=80),
                              nullable=False)
        batch_op.drop_column('name')
        batch_op.create_unique_constraint('uq_staff_last_first',
                                          ['last_name', 'first_name'])


def downgrade():
    with op.batch_alter_table('staff') as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(length=120), nullable=True))

    # Back to one line, written the way the property wrote it.
    op.execute(sa.text("""
        UPDATE staff SET name = TRIM(COALESCE(last_name, '') || ' ' ||
                                     COALESCE(first_name, ''))
    """))

    with op.batch_alter_table('staff') as batch_op:
        batch_op.alter_column('name', existing_type=sa.String(length=120),
                              nullable=False)
        batch_op.drop_constraint('uq_staff_last_first', type_='unique')
        batch_op.create_unique_constraint('uq_staff_name', ['name'])
        batch_op.drop_column('first_name')
        batch_op.drop_column('last_name')
