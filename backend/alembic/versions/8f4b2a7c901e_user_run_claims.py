"""Cross-process ownership for budget, simulation, and goal runs."""
from alembic import op
import sqlalchemy as sa

revision = '8f4b2a7c901e'
down_revision = '7c3a1f0e2b64'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_run_claims',
                    sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), primary_key=True),
                    sa.Column('owner', sa.String(), nullable=False),
                    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('user_run_claims')
