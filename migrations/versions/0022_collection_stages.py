"""Add the data-line collection tables.

Records three stages (``collect``/``organize``/``store``) per execution attempt
in ``run_stages``, the raw bytes each connector downloaded in
``source_artifacts``, and stored-but-unpublished scopes in ``publish_dirty``.

The ``ai_work_items`` model queue lands in a later revision (0023), not here.

Revision ID: 0022_collection_stages
Revises: 0021_situation_alerts
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_collection_stages"
down_revision = "0021_situation_alerts"
branch_labels = None
depends_on = None

STAGE_NAMES = ("collect", "organize", "store")
STAGE_STATUSES = ("running", "completed", "failed", "skipped")
LINES = ("surveillance", "literature", "situation", "knowledge")


def _json(default: str) -> dict:
    return {"nullable": False, "server_default": sa.text(f"'{default}'")}


def _pk() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True)


def upgrade() -> None:
    op.create_table(
        "run_stages",
        _pk(),
        sa.Column("task_uuid", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("metrics", sa.JSON(), **_json("{}")),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_uuid", "stage", "attempt", name="uq_run_stages_attempt"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name="ck_run_stages_status",
        ),
        sa.CheckConstraint(
            "stage IN ('collect', 'organize', 'store')",
            name="ck_run_stages_name",
        ),
    )
    op.create_index("ix_run_stages_task", "run_stages", ["task_uuid", "attempt"])

    op.create_table(
        "source_artifacts",
        _pk(),
        sa.Column("task_uuid", sa.String(36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default="collect"),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON(), **_json("{}")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_artifacts_task", "source_artifacts", ["task_uuid", "attempt"])

    op.create_table(
        "publish_dirty",
        _pk(),
        sa.Column("line", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("source_task_uuid", sa.String(36), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_task_uuid", sa.String(36)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "line IN ('surveillance', 'literature', 'situation', 'knowledge')",
            name="ck_publish_dirty_line",
        ),
    )
    op.create_index(
        "ix_publish_dirty_pending",
        "publish_dirty",
        ["line", "scope_key", "marked_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("publish_dirty")
    op.drop_table("source_artifacts")
    op.drop_table("run_stages")
