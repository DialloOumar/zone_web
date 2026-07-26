"""Add photo_key to citernes (S3 object key of the citerne photo)

Like a vehicle, a citerne can now carry one photo, stored under
{S3_PREFIX}citernes/. Only the object key lives in the DB. Batch mode for
SQLite + Postgres.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4b5c6d7e8f9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.add_column(sa.Column('photo_key', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.drop_column('photo_key')
