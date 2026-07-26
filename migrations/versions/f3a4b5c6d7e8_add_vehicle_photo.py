"""Add photo_key to vehicles (S3 object key of the vehicle photo)

A vehicle can now carry one photo, stored in Linode Object Storage under
{S3_PREFIX}vehicles/. Only the object key lives in the DB; the image is shown
via a short-lived presigned URL. Batch mode for SQLite + Postgres.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('photo_key', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_column('photo_key')
