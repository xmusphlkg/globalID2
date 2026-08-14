"""Add the durable Situation source-refresh task type."""

from alembic import op


revision = "0005_situation_source_task"
down_revision = "0004_situation_history_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'REFRESH_SITUATION_SOURCES'")
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'refresh_situation_sources'")


def downgrade() -> None:
    pass
