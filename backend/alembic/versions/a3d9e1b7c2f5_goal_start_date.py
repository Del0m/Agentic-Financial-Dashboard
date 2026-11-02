"""goal start date

Revision ID: a3d9e1b7c2f5
Revises: 8f4b2a7c901e
Create Date: 2026-11-02 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d9e1b7c2f5'
down_revision: Union[str, Sequence[str], None] = '8f4b2a7c901e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# nullable: rows from before this have no real start month, and they can't be accepted
def upgrade() -> None:
    op.add_column("goals", sa.Column("start_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("goals") as batch:
        batch.drop_column("start_date")
