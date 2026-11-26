"""Tarifs shared by machines

- billing_tags: a named price machines share
- billing_tag_rates: its dated prices, append-only like client_rates
- vehicle_tags: which tarif a machine carries, from a date, append-only

Revision ID: d9f5b1c7e3a4
Revises: c8e4a0b6d2f3
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9f5b1c7e3a4'
down_revision = 'c8e4a0b6d2f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'billing_tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
    )
    op.create_table(
        'billing_tag_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tag_id', sa.Integer(), sa.ForeignKey('billing_tags.id'), nullable=False),
        sa.Column('rate_per_unit', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.String(10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
    )
    op.create_index('ix_billing_tag_rates_tag_id', 'billing_tag_rates', ['tag_id'])
    op.create_table(
        'vehicle_tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('tag_id', sa.Integer(), sa.ForeignKey('billing_tags.id'), nullable=False),
        sa.Column('effective_from', sa.String(10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
    )
    op.create_index('ix_vehicle_tags_vehicle_id', 'vehicle_tags', ['vehicle_id'])
    op.create_index('ix_vehicle_tags_tag_id', 'vehicle_tags', ['tag_id'])


def downgrade():
    op.drop_index('ix_vehicle_tags_tag_id', table_name='vehicle_tags')
    op.drop_index('ix_vehicle_tags_vehicle_id', table_name='vehicle_tags')
    op.drop_table('vehicle_tags')
    op.drop_index('ix_billing_tag_rates_tag_id', table_name='billing_tag_rates')
    op.drop_table('billing_tag_rates')
    op.drop_table('billing_tags')
