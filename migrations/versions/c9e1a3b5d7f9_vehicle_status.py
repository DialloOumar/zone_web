"""A machine's working state, with its history

En service, en maintenance, en panne -- on top of active/inactive, which
says whether it is in the fleet at all. Each change is kept.

Revision ID: c9e1a3b5d7f9
Revises: b8d0f2a4c6e8
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e1a3b5d7f9'
down_revision = 'b8d0f2a4c6e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=12), nullable=False, server_default='active'))
        batch_op.add_column(sa.Column('status_since', sa.String(length=10), nullable=True))
        batch_op.create_index('ix_vehicles_status', ['status'])
    op.create_table(
        'vehicle_status_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('from_status', sa.String(length=12), nullable=False),
        sa.Column('to_status', sa.String(length=12), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_vehicle_status_changes_vehicle_id', 'vehicle_status_changes', ['vehicle_id'])
    op.create_index('ix_vehicle_status_changes_date', 'vehicle_status_changes', ['date'])


def downgrade():
    op.drop_index('ix_vehicle_status_changes_date', table_name='vehicle_status_changes')
    op.drop_index('ix_vehicle_status_changes_vehicle_id', table_name='vehicle_status_changes')
    op.drop_table('vehicle_status_changes')
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_index('ix_vehicles_status')
        batch_op.drop_column('status_since')
        batch_op.drop_column('status')
