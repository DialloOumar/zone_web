"""Pay: dated legal figures, allowances on salary lines, the payslip's facts

- staff: declared (CNSS / RTS), category, marital status, children;
- pay_allowances: the kinds of allowance (taxed? CNSS due?), kept by the
  super admin; staff_salary_allowances: their amounts on a salary line;
- pay_settings + pay_rts_brackets: CNSS rates, ceiling and floor, RTS scale,
  employer's taxes -- one version per day it applies from.

All empty to start with: the figures are typed in by whoever knows them.

Revision ID: a9c5e1f7b3d4
Revises: f8b4d0e6a2c3
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'a9c5e1f7b3d4'
down_revision = 'f8b4d0e6a2c3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('staff') as batch:
        batch.add_column(sa.Column('declared', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('category', sa.String(20)))
        batch.add_column(sa.Column('marital_status', sa.String(12)))
        batch.add_column(sa.Column('children', sa.Integer()))

    op.create_table(
        'pay_allowances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('taxable', sa.Boolean(), nullable=False),
        sa.Column('cnss', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
    )
    op.create_table(
        'staff_salary_allowances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('salary_id', sa.Integer(), sa.ForeignKey('staff_salaries.id'), nullable=False),
        sa.Column('allowance_id', sa.Integer(), sa.ForeignKey('pay_allowances.id'), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.UniqueConstraint('salary_id', 'allowance_id', name='uq_salary_allowance'),
    )
    op.create_index('ix_staff_salary_allowances_salary_id', 'staff_salary_allowances', ['salary_id'])
    op.create_table(
        'pay_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('effective_on', sa.String(10), nullable=False, unique=True),
        sa.Column('cnss_employee_rate', sa.Numeric(6, 3), nullable=False),
        sa.Column('cnss_employer_rate', sa.Numeric(6, 3), nullable=False),
        sa.Column('cnss_ceiling', sa.Integer()),
        sa.Column('cnss_floor', sa.Integer()),
        sa.Column('vf_rate', sa.Numeric(6, 3), nullable=False),
        sa.Column('ta_rate', sa.Numeric(6, 3), nullable=False),
        sa.Column('note', sa.String(255)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'pay_rts_brackets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('setting_id', sa.Integer(), sa.ForeignKey('pay_settings.id'), nullable=False),
        sa.Column('lower', sa.Integer(), nullable=False),
        sa.Column('rate', sa.Numeric(6, 3), nullable=False),
    )
    op.create_index('ix_pay_rts_brackets_setting_id', 'pay_rts_brackets', ['setting_id'])


def downgrade():
    op.drop_index('ix_pay_rts_brackets_setting_id', 'pay_rts_brackets')
    op.drop_table('pay_rts_brackets')
    op.drop_table('pay_settings')
    op.drop_index('ix_staff_salary_allowances_salary_id', 'staff_salary_allowances')
    op.drop_table('staff_salary_allowances')
    op.drop_table('pay_allowances')
    with op.batch_alter_table('staff') as batch:
        batch.drop_column('children')
        batch.drop_column('marital_status')
        batch.drop_column('category')
        batch.drop_column('declared')
