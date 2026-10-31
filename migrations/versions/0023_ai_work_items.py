"""Durable model work queue.

Revision ID: 0023_ai_work_items
Revises: 0022_collection_stages
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_ai_work_items"
down_revision = "0022_collection_stages"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_work_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("parent_task_uuid", sa.String(36), nullable=False),
        sa.Column("line", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False, server_default="text"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("route_key", sa.String(200)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued','leased','succeeded','failed','cancelled')",
            name="ck_ai_work_items_status",
        ),
        sa.CheckConstraint(
            "priority IN ('urgent','high','normal','low')",
            name="ck_ai_work_items_priority",
        ),
        sa.CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_ai_work_items_attempt"),
    )
    op.create_index(
        "ix_ai_work_items_claim",
        "ai_work_items",
        ["priority", "created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_ai_work_items_parent", "ai_work_items", ["parent_task_uuid", "status"]
    )


def downgrade():
    op.drop_table("ai_work_items")
