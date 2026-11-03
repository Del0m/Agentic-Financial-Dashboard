"""low balance signal fields

Revision ID: d5f9a3b2c7e4
Revises: c4e8f2a1b6d3
"""
from alembic import op
import sqlalchemy as sa

revision = "d5f9a3b2c7e4"
down_revision = "c4e8f2a1b6d3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("signal_cents", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("signal_days", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("alerts") as batch:
        batch.drop_column("signal_days")
        batch.drop_column("signal_cents")
