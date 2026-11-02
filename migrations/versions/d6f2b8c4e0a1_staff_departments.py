"""Departments: where someone works in the company

A short list kept by hand, like the positions (Logistique, Finance, RH,
HSSE), and the person's file points at one. Empty to start with: the RH
side names its own departments.

Revision ID: d6f2b8c4e0a1
Revises: c5e1a7b3d9f2
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6f2b8c4e0a1'
down_revision = 'c5e1a7b3d9f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'departments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table('staff') as b:
        b.add_column(sa.Column('department_id', sa.Integer()))
        b.create_foreign_key('fk_staff_department', 'departments', ['department_id'], ['id'])


def downgrade():
    with op.batch_alter_table('staff') as b:
        b.drop_constraint('fk_staff_department', type_='foreignkey')
        b.drop_column('department_id')
    op.drop_table('departments')
