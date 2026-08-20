"""Add Situation Room v3.2 labels, calibration runs, and policy decisions."""

from alembic import op
import sqlalchemy as sa


revision = "0010_situation_v32"
down_revision = "0009_situation_v3"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "situation_event_labels_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label_id", sa.String(120), nullable=False, unique=True),
        sa.Column("disease_id", sa.String(100), nullable=False),
        sa.Column("geographies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("event_started_at", sa.Date()),
        sa.Column("first_official_published_at", sa.Date(), nullable=False),
        sa.Column("authoritative_source", sa.String(180), nullable=False),
        sa.Column("source_url", sa.String(1500), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column(
            "adjudication",
            sa.String(30),
            nullable=False,
            server_default="indeterminate",
        ),
        sa.Column("split", sa.String(30), nullable=False, server_default="unassigned"),
        sa.Column("created_by", sa.String(160)),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index(
        "idx_situation_v3_label_identity",
        "situation_event_labels_v3",
        ["disease_id", "first_official_published_at"],
    )
    op.create_index(
        "idx_situation_v3_label_split",
        "situation_event_labels_v3",
        ["split", "adjudication"],
    )

    op.create_table(
        "situation_calibration_runs_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calibration_id", sa.String(120), nullable=False, unique=True),
        sa.Column("method_version", sa.String(80), nullable=False),
        sa.Column("config_hash", sa.String(128), nullable=False),
        sa.Column("artifact_hash", sa.String(128), nullable=False),
        sa.Column("artifact_uri", sa.String(1500)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.Date()),
        sa.Column("window_end", sa.Date()),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("group_results", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index(
        "idx_situation_v3_calibration_status",
        "situation_calibration_runs_v3",
        ["status", "calibrated_at"],
    )
    op.create_index(
        "idx_situation_v3_calibration_artifact",
        "situation_calibration_runs_v3",
        ["artifact_hash"],
    )

    op.create_table(
        "situation_policy_decisions_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_id", sa.String(120), nullable=False, unique=True),
        sa.Column(
            "run_id",
            sa.String(120),
            sa.ForeignKey("situation_analysis_runs_v3.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_id", sa.String(180), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("basis", sa.String(40), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("calibration_hash", sa.String(128)),
        sa.Column("gate_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("matched_event_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
        sa.UniqueConstraint(
            "run_id",
            "signal_id",
            name="uq_situation_v3_policy_run_signal",
        ),
    )
    op.create_index(
        "idx_situation_v3_policy_run",
        "situation_policy_decisions_v3",
        ["run_id", "status"],
    )
    op.create_index(
        "idx_situation_v3_policy_signal",
        "situation_policy_decisions_v3",
        ["signal_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("situation_policy_decisions_v3")
    op.drop_table("situation_calibration_runs_v3")
    op.drop_table("situation_event_labels_v3")
