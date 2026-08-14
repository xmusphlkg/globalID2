"""Add revision-aware Situation Room v2 snapshots and release schedule."""

from alembic import op
import sqlalchemy as sa


revision = "0003_situation_room_v2"
down_revision = "0002_scheduled_job_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("situation_snapshots"):
        columns = {column["name"] for column in inspector.get_columns("situation_snapshots")}
        additions = {
            "period_key": sa.Column("period_key", sa.String(length=20), nullable=True),
            "checked_at": sa.Column("checked_at", sa.String(length=40), nullable=True),
            "content_updated_at": sa.Column("content_updated_at", sa.String(length=40), nullable=True),
            "quality_gate_status": sa.Column("quality_gate_status", sa.String(length=30), nullable=False, server_default="pending"),
            "quality_gate": sa.Column("quality_gate", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("situation_snapshots", column)

        op.execute(
            """
            UPDATE situation_snapshots
               SET period_key = CASE
                   WHEN snapshot_kind = 'weekly' THEN COALESCE(iso_week, substring(generated_at, 1, 10))
                   WHEN snapshot_kind = 'monthly' THEN substring(generated_at, 1, 7)
                   ELSE substring(generated_at, 1, 10)
               END
             WHERE period_key IS NULL
            """
        )
        op.execute("UPDATE situation_snapshots SET checked_at = generated_at WHERE checked_at IS NULL")
        op.execute("UPDATE situation_snapshots SET content_updated_at = generated_at WHERE content_updated_at IS NULL")
        op.execute(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (PARTITION BY snapshot_kind, period_key ORDER BY created_at, id) AS next_revision,
                       lag(snapshot_id) OVER (PARTITION BY snapshot_kind, period_key ORDER BY created_at, id) AS prior_snapshot_id
                  FROM situation_snapshots
            )
            UPDATE situation_snapshots AS target
               SET revision = ranked.next_revision,
                   supersedes_snapshot_id = COALESCE(target.supersedes_snapshot_id, ranked.prior_snapshot_id)
              FROM ranked
             WHERE target.id = ranked.id
            """
        )
        op.alter_column("situation_snapshots", "period_key", existing_type=sa.String(length=20), nullable=False)
        op.alter_column("situation_snapshots", "checked_at", existing_type=sa.String(length=40), nullable=False)
        op.alter_column("situation_snapshots", "content_updated_at", existing_type=sa.String(length=40), nullable=False)
        index_names = {index["name"] for index in sa.inspect(bind).get_indexes("situation_snapshots")}
        if "idx_situation_snapshot_kind_period" not in index_names:
            op.create_index("idx_situation_snapshot_kind_period", "situation_snapshots", ["snapshot_kind", "period_key"])
        if "uq_situation_snapshot_period_revision" not in index_names:
            op.create_index("uq_situation_snapshot_period_revision", "situation_snapshots", ["snapshot_kind", "period_key", "revision"], unique=True)
        if "idx_situation_snapshot_kind_week" in index_names:
            op.drop_index("idx_situation_snapshot_kind_week", table_name="situation_snapshots")

    if inspector.has_table("data_release_jobs"):
        op.execute(
            """
            UPDATE data_release_jobs
               SET daily_time = '02:00',
                   timezone = 'UTC',
                   interval_minutes = NULL,
                   auto_after_crawls = false
             WHERE job_id = 'site-release'
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("situation_snapshots"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("situation_snapshots")}
    if "uq_situation_snapshot_period_revision" in indexes:
        op.drop_index("uq_situation_snapshot_period_revision", table_name="situation_snapshots")
    if "idx_situation_snapshot_kind_period" in indexes:
        op.drop_index("idx_situation_snapshot_kind_period", table_name="situation_snapshots")
    if "idx_situation_snapshot_kind_week" not in indexes:
        op.create_index("idx_situation_snapshot_kind_week", "situation_snapshots", ["snapshot_kind", "iso_week"])
    for column in ("quality_gate", "quality_gate_status", "content_updated_at", "checked_at", "period_key"):
        op.drop_column("situation_snapshots", column)
