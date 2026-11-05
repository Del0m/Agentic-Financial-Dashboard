"""Persist the effective date of category rule edits.

Revision ID: a7c3d9e5f102
Revises: f6a2b8c4d901
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c3d9e5f102"
down_revision = "f6a2b8c4d901"
branch_labels = None
depends_on = None


def upgrade():
    # Old rows have no reliable edit date; projection retains its legacy fallback.
    op.add_column("category_rules", sa.Column("effective_from", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("category_rules", "effective_from")
