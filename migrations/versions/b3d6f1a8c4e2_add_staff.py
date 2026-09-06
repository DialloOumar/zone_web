"""Personnel, their positions, and the cost that names one

The seed of an HR module. A person here is the company's, not a client's, so
they carry no fleet — and they are deliberately not the operators table, whose
rows are drivers attached to one fleet and feed the roster.

The expense gains a real link to a person. The free-text `operator` column
beside it is left untouched: nothing writes it any more, but the driver sheet
still reads it for its history, and dropping it would empty that chart.

Revision ID: b3d6f1a8c4e2
Revises: a1c4e7b9d2f0
Create Date: 2026-09-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b3d6f1a8c4e2'
down_revision = 'a1c4e7b9d2f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'staff_positions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_staff_positions_name'),
    )
    op.create_table(
        'staff',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('position_id', sa.Integer(), nullable=True),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('matricule', sa.String(length=40), nullable=True),
        sa.Column('hired_on', sa.String(length=10), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_staff_name'),
        sa.ForeignKeyConstraint(['position_id'], ['staff_positions.id'],
                                name='fk_staff_position_id'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_staff_created_by'),
    )
    op.create_index('ix_staff_position_id', 'staff', ['position_id'])
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('staff_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_expenses_staff_id', 'staff',
                                    ['staff_id'], ['id'])
    op.create_index('ix_expenses_staff_id', 'expenses', ['staff_id'])


def downgrade():
    op.drop_index('ix_expenses_staff_id', table_name='expenses')
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_constraint('fk_expenses_staff_id', type_='foreignkey')
        batch_op.drop_column('staff_id')
    op.drop_index('ix_staff_position_id', table_name='staff')
    op.drop_table('staff')
    op.drop_table('staff_positions')
