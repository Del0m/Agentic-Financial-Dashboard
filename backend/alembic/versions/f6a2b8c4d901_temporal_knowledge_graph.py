"""Add bitemporal knowledge graph assertions.

Revision ID: f6a2b8c4d901
Revises: d5f9a3b2c7e4
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a2b8c4d901"
down_revision = "d5f9a3b2c7e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kg_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("props_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_kg_nodes_user_kind_key"),
    )
    op.create_index("uq_kg_nodes_shared_kind_key", "kg_nodes", ["kind", "key"], unique=True,
                    sqlite_where=sa.text("user_id IS NULL"), postgresql_where=sa.text("user_id IS NULL"))
    op.create_table(
        "kg_edges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("src_id", sa.Integer(), sa.ForeignKey("kg_nodes.id"), nullable=False),
        sa.Column("rel", sa.String(), nullable=False),
        sa.Column("dst_id", sa.Integer(), sa.ForeignKey("kg_nodes.id"), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_kg_edges_valid_interval"),
        sa.CheckConstraint("expired_at IS NULL OR expired_at > recorded_at", name="ck_kg_edges_transaction_interval"),
    )
    op.create_index("ix_kg_edges_user_rel_src", "kg_edges", ["user_id", "rel", "src_id"])
    op.create_index("ix_kg_edges_user_dst", "kg_edges", ["user_id", "dst_id"])
    op.create_index("ix_kg_edges_user_valid_from", "kg_edges", ["user_id", "valid_from"])
    indexes = (
        ("uq_kg_edges_current_open", ["src_id", "rel", "dst_id"],
         "expired_at IS NULL AND valid_to IS NULL AND rel != 'CATEGORIZED_AS'"),
        ("uq_kg_edges_category_current_open", ["src_id", "rel"],
         "expired_at IS NULL AND valid_to IS NULL AND rel = 'CATEGORIZED_AS'"),
        ("uq_kg_edges_current_start", ["src_id", "rel", "dst_id", "valid_from"],
         "expired_at IS NULL AND rel != 'CATEGORIZED_AS'"),
        ("uq_kg_edges_category_current_start", ["src_id", "rel", "valid_from"],
         "expired_at IS NULL AND rel = 'CATEGORIZED_AS'"),
    )
    for name, columns, predicate in indexes:
        op.create_index(name, "kg_edges", columns, unique=True,
                        sqlite_where=sa.text(predicate), postgresql_where=sa.text(predicate))


def downgrade():
    op.drop_table("kg_edges")
    op.drop_table("kg_nodes")
