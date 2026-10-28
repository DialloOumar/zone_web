"""Seeing the purchase orders becomes a permission of its own

The orders -- suppliers, prices -- were open to whoever saw the stock. They
now take "purchase_order.view"; the store asks for parts and receives them
from its own page without it. Every role that sees the stock today is given
it, so nothing changes until it is taken off a role.

Revision ID: f8b0c2d4e6a8
Revises: e7a9b1d3f5c7
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = 'f8b0c2d4e6a8'
down_revision = 'e7a9b1d3f5c7'
branch_labels = None
depends_on = None

KEY = "purchase_order.view"


def upgrade():
    conn = op.get_bind()
    pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": KEY}).scalar()
    if pid is None:
        conn.execute(sa.text(
            "INSERT INTO permissions (key, label, category, resource, action) "
            "VALUES (:k, 'View purchase orders', 'Stock', 'purchase_order', 'view')"), {"k": KEY})
        pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": KEY}).scalar()
    conn.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id, requires_approval) "
        "SELECT rp.role_id, :pid, false FROM role_permissions rp "
        "JOIN permissions p ON p.id = rp.permission_id "
        "WHERE p.key = 'stock.view' AND rp.role_id NOT IN "
        "(SELECT role_id FROM role_permissions WHERE permission_id = :pid)"), {"pid": pid})


def downgrade():
    conn = op.get_bind()
    pid = conn.execute(sa.text("SELECT id FROM permissions WHERE key = :k"), {"k": KEY}).scalar()
    if pid is not None:
        conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :pid"), {"pid": pid})
        conn.execute(sa.text("DELETE FROM permissions WHERE id = :pid"), {"pid": pid})
