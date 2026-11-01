"""period close and styles

Revision ID: 5b7c2e9d4a10
Revises: e0cdfdbdab45
Create Date: 2026-10-31 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b7c2e9d4a10'
down_revision: Union[str, Sequence[str], None] = 'e0cdfdbdab45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# a copy of budget_engine/groups.py as of this revision; migrations never import app code, which can change later
NEEDS = {"RENT_AND_UTILITIES", "LOAN_PAYMENTS", "TRANSPORTATION", "MEDICAL", "FOOD_AND_DRINK"}
RESERVED = {"SAVINGS_TRANSFER": ("Savings", "saving"), "FREE": ("Free", "free")}


def _name(category: str) -> str:
    if category in RESERVED:
        return RESERVED[category][0]
    words = category.split("_")
    return " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])


def _group(category: str) -> str:
    if category in RESERVED:
        return RESERVED[category][1]
    return "needs" if category in NEEDS else "wants"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('category_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('merchant_name', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'merchant_name', name='uq_category_rules_user_merchant')
    )
    with op.batch_alter_table('category_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_category_rules_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('budget_periods', schema=None) as batch_op:
        batch_op.add_column(sa.Column('income_cents', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('total_cents', sa.Integer(), nullable=True))

    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_category', sa.String(), nullable=True))

    # nullable first, backfilled from the category, then required
    with op.batch_alter_table('budget_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('group', sa.String(), nullable=True))
    conn = op.get_bind()
    for line_id, category in conn.execute(sa.text("SELECT id, category FROM budget_lines")).all():
        conn.execute(sa.text('UPDATE budget_lines SET name = :name, "group" = :group WHERE id = :id'),
                     {"name": _name(category), "group": _group(category), "id": line_id})
    with op.batch_alter_table('budget_lines', schema=None) as batch_op:
        batch_op.alter_column('name', existing_type=sa.String(), nullable=False)
        batch_op.alter_column('group', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('budget_lines', schema=None) as batch_op:
        batch_op.drop_column('group')
        batch_op.drop_column('name')

    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('user_category')

    with op.batch_alter_table('budget_periods', schema=None) as batch_op:
        batch_op.drop_column('total_cents')
        batch_op.drop_column('income_cents')

    with op.batch_alter_table('category_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_category_rules_user_id'))

    op.drop_table('category_rules')
