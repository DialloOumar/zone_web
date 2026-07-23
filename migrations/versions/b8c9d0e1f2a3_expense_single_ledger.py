"""Make Expense the single money ledger

Adds the fields the new expense screens need (label, payment method + reference),
lets an expense exist without a fleet (a company cost), and moves every existing
maintenance cost into the ledger as an "entretien" expense linked back to its
service record — after which maintenance_records.cost is dropped, so a service's
cost has exactly one home and can never be double counted.

Batch mode for SQLite + Postgres.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('label', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('payment_method', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('payment_reference', sa.String(length=60), nullable=True))
        batch_op.alter_column('fleet_id', existing_type=sa.Integer(), nullable=True)

    # Move recorded service costs into the ledger. The fleet comes from the
    # vehicle, so the new expense keeps the same visibility as its service.
    op.execute(sa.text("""
        INSERT INTO expenses (vehicle_id, fleet_id, category, date, amount, currency,
                              operator, supplier, description, maintenance_record_id,
                              created_by, created_at)
        SELECT m.vehicle_id, v.fleet_id, 'entretien', m.date, m.cost, 'GNF',
               m.operator, m.supplier, m.description, m.id,
               m.recorded_by, m.recorded_at
        FROM maintenance_records m
        JOIN vehicles v ON v.id = m.vehicle_id
        WHERE m.cost IS NOT NULL
    """))

    with op.batch_alter_table('maintenance_records') as batch_op:
        batch_op.drop_column('cost')


def downgrade():
    with op.batch_alter_table('maintenance_records') as batch_op:
        batch_op.add_column(sa.Column('cost', sa.Integer(), nullable=True))

    # Put the amounts back on the service records, then drop those ledger rows.
    op.execute(sa.text("""
        UPDATE maintenance_records SET cost = (
            SELECT e.amount FROM expenses e
            WHERE e.maintenance_record_id = maintenance_records.id
            LIMIT 1
        )
    """))
    op.execute(sa.text("DELETE FROM expenses WHERE category = 'entretien'"))

    with op.batch_alter_table('expenses') as batch_op:
        batch_op.alter_column('fleet_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('payment_reference')
        batch_op.drop_column('payment_method')
        batch_op.drop_column('label')
