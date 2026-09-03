"""Cash box: money paid into the till

The costs entered on the Dépenses page are spent out of a float someone is
handed. Only the money coming in needs a table of its own — what goes out is
already in the ledger — so the balance is deposits minus those costs.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cash_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='depot'),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=5), nullable=False, server_default='GNF'),
        sa.Column('source', sa.String(length=120), nullable=True),
        sa.Column('method', sa.String(length=20), nullable=True),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_cash_movements_created_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    # The list is read by date, newest first.
    op.create_index('ix_cash_movements_date', 'cash_movements', ['date'])


def downgrade():
    op.drop_index('ix_cash_movements_date', table_name='cash_movements')
    op.drop_table('cash_movements')
