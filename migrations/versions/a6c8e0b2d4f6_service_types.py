"""Service types, kept like the store's units

The kinds of service were a list in the code. They are a table now,
seeded with the eight the app had, so the team can add its own.

Revision ID: a6c8e0b2d4f6
Revises: f3a5b7c9d1e3
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a6c8e0b2d4f6'
down_revision = 'f3a5b7c9d1e3'
branch_labels = None
depends_on = None

STARTERS = [("oil_change", "Vidange"), ("filter", "Filtres"), ("tires", "Pneus"),
            ("brakes", "Freins"), ("repair", "Réparation"), ("parts", "Pièces détachées"),
            ("revision", "Révision"), ("other", "Autre")]


def upgrade():
    op.create_table(
        'service_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=40), nullable=False, unique=True),
        sa.Column('name', sa.String(length=60), nullable=False, unique=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    conn = op.get_bind()
    for i, (code, name) in enumerate(STARTERS):
        conn.execute(sa.text(
            "INSERT INTO service_types (code, name, sort_order, is_active, created_at) "
            "VALUES (:c, :n, :o, TRUE, CURRENT_TIMESTAMP)"), dict(c=code, n=name, o=i))
    # A type a record or a rule already carries that the starters do not
    # name (none expected) becomes a type too, so nothing prints as a code.
    for (code,) in conn.execute(sa.text(
            "SELECT DISTINCT type FROM maintenance_records WHERE type NOT IN (SELECT code FROM service_types)")).fetchall():
        conn.execute(sa.text(
            "INSERT INTO service_types (code, name, sort_order, is_active, created_at) "
            "VALUES (:c, :c, 99, TRUE, CURRENT_TIMESTAMP)"), dict(c=code))


def downgrade():
    op.drop_table('service_types')
