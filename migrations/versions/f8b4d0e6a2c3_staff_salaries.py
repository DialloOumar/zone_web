"""Salary as its own dated history, out of the contracts

A raise is not a new contract: each line says "from this day, the base salary
is this much". The salaries typed on contracts so far become the first lines
(dated on the contract's start), then the contracts lose their own copy.

Revision ID: f8b4d0e6a2c3
Revises: e7a3c9d5f1b2
Create Date: 2026-11-04
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'f8b4d0e6a2c3'
down_revision = 'e7a3c9d5f1b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'staff_salaries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('effective_on', sa.String(10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(20), nullable=False),
        sa.Column('contract_id', sa.Integer(), sa.ForeignKey('staff_contracts.id')),
        sa.Column('note', sa.String(255)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('staff_id', 'effective_on', name='uq_staff_salary_day'),
    )
    op.create_index('ix_staff_salaries_staff_id', 'staff_salaries', ['staff_id'])

    # Each contract's salary, oldest first per person; a line only where the
    # figure changes.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, staff_id, start_date, base_salary, created_by FROM staff_contracts "
        "WHERE base_salary IS NOT NULL ORDER BY staff_id, start_date")).fetchall()
    last = {}
    salaries = sa.table('staff_salaries', sa.column('staff_id'), sa.column('effective_on'),
                        sa.column('amount'), sa.column('reason'), sa.column('contract_id'),
                        sa.column('created_by'), sa.column('created_at'))
    for cid, sid, start, amount, by in rows:
        if last.get(sid) == amount:
            continue
        reason = 'embauche' if sid not in last else 'renouvellement'
        last[sid] = amount
        bind.execute(salaries.insert().values(
            staff_id=sid, effective_on=start, amount=amount, reason=reason,
            contract_id=cid, created_by=by, created_at=datetime.utcnow()))

    with op.batch_alter_table('staff_contracts') as batch:
        batch.drop_column('base_salary')


def downgrade():
    with op.batch_alter_table('staff_contracts') as batch:
        batch.add_column(sa.Column('base_salary', sa.Integer()))
    # Each contract gets back the salary in force on its first day.
    op.execute(
        "UPDATE staff_contracts SET base_salary = ("
        " SELECT s.amount FROM staff_salaries s"
        " WHERE s.staff_id = staff_contracts.staff_id"
        " AND s.effective_on <= staff_contracts.start_date"
        " ORDER BY s.effective_on DESC LIMIT 1)")
    op.drop_index('ix_staff_salaries_staff_id', 'staff_salaries')
    op.drop_table('staff_salaries')
