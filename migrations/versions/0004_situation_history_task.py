"""Add the durable Situation history synchronization task type."""

from alembic import op


revision = "0004_situation_history_task"
down_revision = "0003_situation_room_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # PostgreSQL enum values must be committed before they can be used by a
    # later task insert, hence the explicit autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'SYNC_SITUATION_HISTORY'")
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'sync_situation_history'")


def downgrade() -> None:
    # PostgreSQL does not safely remove a single enum value in place. Keeping an
    # unused label is non-destructive and permits application rollback.
    pass
