"""Stores kept apart: a re-order level per store, and no more transfers

A part's re-order level was one figure for every store, so a store that had
never held the part was told to re-order it. Each store now keeps its own:
today's level is copied onto every store that has moved the part.

Parts can no longer be sent from one store to another. Parts still on their
way go back to the store that sent them: the transfer and the movement that
took them out are removed, as a transfer called back always was.

Revision ID: d6f8a0c2e4b6
Revises: b5d7f9a1c3e5
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6f8a0c2e4b6'
down_revision = 'b5d7f9a1c3e5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'part_store_levels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('part_id', sa.Integer(), sa.ForeignKey('parts.id'), nullable=False),
        sa.Column('store_id', sa.Integer(), sa.ForeignKey('stores.id'), nullable=False),
        sa.Column('reorder_level', sa.Float(), nullable=False),
        sa.UniqueConstraint('part_id', 'store_id'),
    )
    op.create_index('ix_part_store_levels_part_id', 'part_store_levels', ['part_id'])
    op.create_index('ix_part_store_levels_store_id', 'part_store_levels', ['store_id'])
    op.execute("INSERT INTO part_store_levels (part_id, store_id, reorder_level) "
               "SELECT DISTINCT p.id, m.store_id, p.reorder_level FROM parts p "
               "JOIN stock_movements m ON m.part_id = p.id "
               "WHERE p.reorder_level IS NOT NULL AND m.store_id IS NOT NULL")
    with op.batch_alter_table('parts') as b:
        b.drop_column('reorder_level')

    conn = op.get_bind()
    outs = [r[0] for r in conn.execute(sa.text(
        "SELECT out_movement_id FROM stock_transfers "
        "WHERE status = 'transit' AND out_movement_id IS NOT NULL")).fetchall()]
    conn.execute(sa.text("DELETE FROM stock_transfers WHERE status = 'transit'"))
    for mid in outs:
        conn.execute(sa.text("DELETE FROM stock_movements WHERE id = :mid"), {"mid": mid})


def downgrade():
    # One level per part again: the highest any store set. Transfers called
    # back stay called back.
    with op.batch_alter_table('parts') as b:
        b.add_column(sa.Column('reorder_level', sa.Float(), nullable=True))
    op.execute("UPDATE parts SET reorder_level = (SELECT MAX(l.reorder_level) FROM part_store_levels l "
               "WHERE l.part_id = parts.id)")
    op.drop_index('ix_part_store_levels_store_id', 'part_store_levels')
    op.drop_index('ix_part_store_levels_part_id', 'part_store_levels')
    op.drop_table('part_store_levels')
