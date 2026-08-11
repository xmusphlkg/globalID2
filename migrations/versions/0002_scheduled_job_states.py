"""Add the persisted cross-scheduler state projection."""

from alembic import op
import sqlalchemy as sa

revision = "0002_scheduled_job_states"
down_revision = "0001_control_plane_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("scheduled_job_states"):
        op.create_table(
            "scheduled_job_states",
            sa.Column("job_kind", sa.String(length=40), nullable=False),
            sa.Column("job_id", sa.String(length=100), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status", sa.String(length=30), nullable=False, server_default="idle"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_task_uuid", sa.String(length=36), nullable=True),
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "job_kind",
                "job_id",
                name="uq_scheduled_job_state_kind_id",
            ),
        )
        op.create_index(
            "idx_scheduled_job_state_next_run",
            "scheduled_job_states",
            ["next_run_at"],
        )
        return

    columns = {
        column["name"]: column
        for column in inspector.get_columns("scheduled_job_states")
    }
    for column_name in ("next_run_at", "last_started_at", "last_finished_at"):
        column_type = columns[column_name]["type"]
        if not getattr(column_type, "timezone", False):
            op.alter_column(
                "scheduled_job_states",
                column_name,
                existing_type=column_type,
                type_=sa.DateTime(timezone=True),
                postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            )

    op.alter_column(
        "scheduled_job_states",
        "last_status",
        existing_type=columns["last_status"]["type"],
        existing_nullable=False,
        server_default=sa.text("'idle'"),
    )
    op.alter_column(
        "scheduled_job_states",
        "created_at",
        existing_type=columns["created_at"]["type"],
        existing_nullable=False,
        server_default=sa.text("now()"),
    )
    op.alter_column(
        "scheduled_job_states",
        "updated_at",
        existing_type=columns["updated_at"]["type"],
        existing_nullable=False,
        server_default=sa.text("now()"),
    )

    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("scheduled_job_states")
    }
    if "uq_scheduled_job_state_kind_id" not in unique_names:
        op.create_unique_constraint(
            "uq_scheduled_job_state_kind_id",
            "scheduled_job_states",
            ["job_kind", "job_id"],
        )

    index_names = {
        index["name"] for index in inspector.get_indexes("scheduled_job_states")
    }
    if "idx_scheduled_job_state_next_run" not in index_names:
        op.create_index(
            "idx_scheduled_job_state_next_run",
            "scheduled_job_states",
            ["next_run_at"],
        )


def downgrade() -> None:
    op.drop_index("idx_scheduled_job_state_next_run", table_name="scheduled_job_states")
    op.drop_table("scheduled_job_states")
