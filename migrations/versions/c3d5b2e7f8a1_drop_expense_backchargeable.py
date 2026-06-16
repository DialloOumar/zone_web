"""Drop is_backchargeable from expenses

The back-chargeable flag was removed from the expenses feature.
Batch mode so the column drop also works on SQLite.

Revision ID: c3d5b2e7f8a1
Revises: b2f4a1c6d3e7
Create Date: 2026-06-16 21:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d5b2e7f8a1'
down_revision = 'b2f4a1c6d3e7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_column('is_backchargeable')


def downgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('is_backchargeable', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
