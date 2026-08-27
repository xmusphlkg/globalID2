"""Bind Research Radar ingest runs to their owning task."""

from alembic import op
import sqlalchemy as sa


revision = "0011_ingest_task_binding"
down_revision = "0010_situation_v32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "literature_ingest_runs",
        sa.Column("task_uuid", sa.String(36), nullable=True),
    )
    op.create_index(
        "idx_literature_ingest_run_task",
        "literature_ingest_runs",
        ["task_uuid", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_literature_ingest_run_task",
        table_name="literature_ingest_runs",
    )
    op.drop_column("literature_ingest_runs", "task_uuid")
