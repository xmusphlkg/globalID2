"""Persist the latest realtime monitor report in PostgreSQL."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035_realtime_monitor_state"
down_revision = "0034_r2_publication_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "realtime_monitor_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("monitor_id", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("monitor_id", name="uq_realtime_monitor_states_monitor_id"),
    )
    op.create_index("ix_realtime_monitor_states_checked_at", "realtime_monitor_states", ["checked_at"])


def downgrade():
    op.drop_index("ix_realtime_monitor_states_checked_at", table_name="realtime_monitor_states")
    op.drop_table("realtime_monitor_states")

