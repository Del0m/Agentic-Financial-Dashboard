"""recurring streams

Revision ID: c4e8f2a1b6d3
Revises: a3d9e1b7c2f5
Create Date: 2026-11-02 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8f2a1b6d3'
down_revision: Union[str, Sequence[str], None] = 'a3d9e1b7c2f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recurring_streams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("merchant_key", sa.String(), nullable=False),
        sa.Column("merchant_name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("detailed", sa.String(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=False),
        sa.Column("typical_cents", sa.Integer(), nullable=False),
        sa.Column("last_cents", sa.Integer(), nullable=False),
        sa.Column("previous_cents", sa.Integer(), nullable=True),
        sa.Column("amount_varies", sa.Boolean(), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("next_date", sa.Date(), nullable=False),
        sa.Column("gap_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_date", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["first_seen_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "direction", "merchant_key", name="uq_recurring_streams_user_key"),
    )
    op.create_index(op.f("ix_recurring_streams_user_id"), "recurring_streams", ["user_id"], unique=False)
    op.create_index("ix_recurring_streams_user_status", "recurring_streams", ["user_id", "status"], unique=False)
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stream_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("signal_key", sa.String(), nullable=True))
        batch_op.create_foreign_key("fk_alerts_stream_id_recurring_streams", "recurring_streams", ["stream_id"], ["id"])
        batch_op.create_index("ix_alerts_user_signal_key", ["user_id", "signal_key"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index("ix_alerts_user_signal_key")
        batch_op.drop_constraint("fk_alerts_stream_id_recurring_streams", type_="foreignkey")
        batch_op.drop_column("signal_key")
        batch_op.drop_column("stream_id")
    op.drop_index("ix_recurring_streams_user_status", table_name="recurring_streams")
    op.drop_index(op.f("ix_recurring_streams_user_id"), table_name="recurring_streams")
    op.drop_table("recurring_streams")
