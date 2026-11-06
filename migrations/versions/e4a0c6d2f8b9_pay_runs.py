"""The month's pay: prepared, validated, frozen

- roles.approves_payroll: who validates the pay;
- pay_runs: one per month, draft | submitted | validated;
- pay_run_adjustments: the month's changes to one person's pay;
- pay_run_lines: each employee's pay once the month is validated, frozen.

Revision ID: e4a0c6d2f8b9
Revises: d3f9b5c1e7a8
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'e4a0c6d2f8b9'
down_revision = 'd3f9b5c1e7a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roles') as batch:
        batch.add_column(sa.Column('approves_payroll', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'pay_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('month', sa.String(7), nullable=False, unique=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('note', sa.String(255)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('submitted_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('submitted_at', sa.DateTime()),
        sa.Column('validated_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('validated_at', sa.DateTime()),
    )
    op.create_index('ix_pay_runs_status', 'pay_runs', ['status'])
    op.create_table(
        'pay_run_adjustments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('pay_runs.id'), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('kind', sa.String(6), nullable=False),
        sa.Column('label', sa.String(120), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('taxable', sa.Boolean(), nullable=False),
        sa.Column('cnss', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_pay_run_adjustments_run_id', 'pay_run_adjustments', ['run_id'])
    op.create_index('ix_pay_run_adjustments_staff_id', 'pay_run_adjustments', ['staff_id'])
    op.create_table(
        'pay_run_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('pay_runs.id'), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('declared', sa.Boolean(), nullable=False),
        sa.Column('pay_method', sa.String(12)),
        sa.Column('gross', sa.Integer(), nullable=False),
        sa.Column('cnss_employee', sa.Integer(), nullable=False),
        sa.Column('cnss_employer', sa.Integer(), nullable=False),
        sa.Column('rts', sa.Integer(), nullable=False),
        sa.Column('vf', sa.Integer(), nullable=False),
        sa.Column('ta', sa.Integer(), nullable=False),
        sa.Column('advances', sa.Integer(), nullable=False),
        sa.Column('prepaid', sa.Integer(), nullable=False),
        sa.Column('other', sa.Integer(), nullable=False),
        sa.Column('net', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Integer(), nullable=False),
        sa.Column('slip', sa.Text(), nullable=False),
        sa.UniqueConstraint('run_id', 'staff_id', name='uq_pay_run_line'),
    )
    op.create_index('ix_pay_run_lines_run_id', 'pay_run_lines', ['run_id'])
    op.create_index('ix_pay_run_lines_staff_id', 'pay_run_lines', ['staff_id'])


def downgrade():
    op.drop_index('ix_pay_run_lines_staff_id', 'pay_run_lines')
    op.drop_index('ix_pay_run_lines_run_id', 'pay_run_lines')
    op.drop_table('pay_run_lines')
    op.drop_index('ix_pay_run_adjustments_staff_id', 'pay_run_adjustments')
    op.drop_index('ix_pay_run_adjustments_run_id', 'pay_run_adjustments')
    op.drop_table('pay_run_adjustments')
    op.drop_index('ix_pay_runs_status', 'pay_runs')
    op.drop_table('pay_runs')
    with op.batch_alter_table('roles') as batch:
        batch.drop_column('approves_payroll')
