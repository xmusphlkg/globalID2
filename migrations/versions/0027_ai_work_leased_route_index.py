"""Index active model leases for shared dispatcher admission.

Revision ID: 0027_ai_work_leased_route_index
Revises: 0026_publish_dirty_changes
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_ai_work_leased_route_index"
down_revision = "0026_publish_dirty_changes"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ai_work_items_leased_route_expiry "
                       "ON ai_work_items (route_key, lease_expires_at) WHERE status = 'leased'")
    else:
        op.create_index("ix_ai_work_items_leased_route_expiry", "ai_work_items",
                        ["route_key", "lease_expires_at"], sqlite_where=sa.text("status = 'leased'"))


def downgrade():
    op.drop_index("ix_ai_work_items_leased_route_expiry", table_name="ai_work_items")
