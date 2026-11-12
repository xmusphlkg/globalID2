"""Index durable request deduplication for long-running review parents.

Revision ID: 0028_ai_work_request_index
Revises: 0027_ai_work_leased_route_index
"""
from alembic import op

revision = "0028_ai_work_request_index"
down_revision = "0027_ai_work_leased_route_index"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ai_work_items_parent_request "
                       "ON ai_work_items (parent_task_uuid, (payload->>'request_key'), id DESC)")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_ai_work_items_parent_request", table_name="ai_work_items")
