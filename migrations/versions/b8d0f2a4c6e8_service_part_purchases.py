"""Parts bought outside for a service, as requests to the cash box

Revision ID: b8d0f2a4c6e8
Revises: a6c8e0b2d4f6
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d0f2a4c6e8'
down_revision = 'a6c8e0b2d4f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'service_part_purchases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('record_id', sa.Integer(), sa.ForeignKey('maintenance_records.id'), nullable=False),
        sa.Column('description', sa.String(length=160), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False, server_default='1'),
        sa.Column('supplier', sa.String(length=120), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False, server_default='pending'),
        sa.Column('expense_id', sa.Integer(), sa.ForeignKey('expenses.id'), nullable=True, unique=True),
        sa.Column('refused_note', sa.String(length=255), nullable=True),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('settled_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_service_part_purchases_record_id', 'service_part_purchases', ['record_id'])
    op.create_index('ix_service_part_purchases_state', 'service_part_purchases', ['state'])


def downgrade():
    op.drop_index('ix_service_part_purchases_state', table_name='service_part_purchases')
    op.drop_index('ix_service_part_purchases_record_id', table_name='service_part_purchases')
    op.drop_table('service_part_purchases')
