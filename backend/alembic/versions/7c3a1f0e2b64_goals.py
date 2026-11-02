"""goals

Revision ID: 7c3a1f0e2b64
Revises: 5b7c2e9d4a10
Create Date: 2026-11-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3a1f0e2b64'
down_revision: Union[str, Sequence[str], None] = '5b7c2e9d4a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("monthly_cents", sa.Integer(), nullable=False),
        sa.Column("target_cents", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("strategy", sa.String(), nullable=True),
        sa.Column("rationale", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_goals_user_id"), "goals", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_goals_user_id"), table_name="goals")
    op.drop_table("goals")
