"""Add normalized Situation Room v3 runs, reports, decisions, and pointer."""

from alembic import op
import sqlalchemy as sa


revision = "0009_situation_v3"
down_revision = "0008_literature_evidence_gaps"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]


def upgrade() -> None:
    op.create_index(
        "idx_disease_series_observation_identity_time",
        "disease_series_observations",
        ["series_code", "geography_key", "dimension_key", "time"],
    )
    op.create_table(
        "situation_analysis_runs_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(120), nullable=False, unique=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method_version", sa.String(80), nullable=False),
        sa.Column("config_hash", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="running"),
        sa.Column("timings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("coverage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("quality_gate", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ledger_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text()),
        *_timestamps(),
    )
    op.create_index("idx_situation_v3_run_checked", "situation_analysis_runs_v3", ["checked_at"])
    op.create_index("idx_situation_v3_run_status", "situation_analysis_runs_v3", ["status", "checked_at"])
    op.create_index("idx_situation_v3_run_input", "situation_analysis_runs_v3", ["input_hash"])

    op.create_table(
        "situation_signal_results_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(120), sa.ForeignKey("situation_analysis_runs_v3.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_id", sa.String(180), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("disease_id", sa.String(100)),
        sa.Column("country_code", sa.String(20)),
        sa.Column("canonical_geography_key", sa.String(300)),
        sa.Column("series_code", sa.String(240)),
        sa.Column("source_system", sa.String(180)),
        sa.Column("metric_type", sa.String(120)),
        sa.Column("cadence", sa.String(30)),
        sa.Column("raw_p_value", sa.Float()),
        sa.Column("q_value", sa.Float()),
        sa.Column("anomaly_state", sa.String(30)),
        sa.Column("review_priority", sa.String(30)),
        sa.Column("rejection_reason", sa.String(120)),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "signal_id", name="uq_situation_v3_run_signal"),
    )
    op.create_index("idx_situation_v3_signal_run_state", "situation_signal_results_v3", ["run_id", "anomaly_state"])
    op.create_index("idx_situation_v3_signal_identity", "situation_signal_results_v3", ["disease_id", "country_code", "series_code"])
    op.create_index("idx_situation_v3_signal_q", "situation_signal_results_v3", ["q_value", "anomaly_state"])

    op.create_table(
        "situation_event_clusters_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", sa.String(120), nullable=False, unique=True),
        sa.Column("disease_id", sa.String(100), nullable=False),
        sa.Column("disease_name", sa.String(300), nullable=False),
        sa.Column("geographies", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("first_published_at", sa.String(40), nullable=False),
        sa.Column("last_published_at", sa.String(40), nullable=False),
        sa.Column("source_state", sa.String(30), nullable=False, server_default="active"),
        sa.Column("review_state", sa.String(30), nullable=False, server_default="unreviewed"),
        sa.Column("corrected_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index("idx_situation_v3_event_disease", "situation_event_clusters_v3", ["disease_id", "last_published_at"])
    op.create_index("idx_situation_v3_event_review", "situation_event_clusters_v3", ["review_state", "last_published_at"])
    op.create_table(
        "situation_event_cluster_items_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", sa.String(120), sa.ForeignKey("situation_event_clusters_v3.cluster_id", ondelete="CASCADE"), nullable=False),
        sa.Column("update_id", sa.String(120), nullable=False, unique=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(1500), nullable=False),
        sa.Column("published_at", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index("idx_situation_v3_event_item_cluster", "situation_event_cluster_items_v3", ["cluster_id", "published_at"])

    op.create_table(
        "situation_period_reports_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.String(140), nullable=False, unique=True),
        sa.Column("report_kind", sa.String(20), nullable=False),
        sa.Column("period_key", sa.String(20), nullable=False),
        sa.Column("period_start", sa.String(40), nullable=False),
        sa.Column("period_end", sa.String(40), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_report_id", sa.String(140)),
        sa.Column("method_version", sa.String(80), nullable=False),
        sa.Column("config_hash", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("quality_gate", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("coverage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
        sa.UniqueConstraint("report_kind", "period_key", "revision", name="uq_situation_v3_report_revision"),
    )
    op.create_index("idx_situation_v3_report_period", "situation_period_reports_v3", ["report_kind", "period_key", "revision"])
    op.create_index("idx_situation_v3_report_status", "situation_period_reports_v3", ["status", "as_of"])

    op.create_table(
        "situation_report_members_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.String(140), sa.ForeignKey("situation_period_reports_v3.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(120), sa.ForeignKey("situation_analysis_runs_v3.run_id", ondelete="RESTRICT"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("report_id", "run_id", name="uq_situation_v3_report_member"),
    )
    op.create_index("idx_situation_v3_report_member_run", "situation_report_members_v3", ["run_id"])

    op.create_table(
        "situation_review_decisions_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_id", sa.String(64), nullable=False, unique=True),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(180), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(160)),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index("idx_situation_v3_review_target", "situation_review_decisions_v3", ["target_type", "target_id", "created_at"])

    op.create_table(
        "situation_publication_pointers_v3",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(40), nullable=False, unique=True),
        sa.Column("report_id", sa.String(140), sa.ForeignKey("situation_period_reports_v3.report_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_report_id", sa.String(140)),
        *_timestamps(),
    )
    op.create_index("idx_situation_v3_pointer_report", "situation_publication_pointers_v3", ["report_id"])


def downgrade() -> None:
    op.drop_table("situation_publication_pointers_v3")
    op.drop_table("situation_review_decisions_v3")
    op.drop_table("situation_report_members_v3")
    op.drop_table("situation_period_reports_v3")
    op.drop_table("situation_event_cluster_items_v3")
    op.drop_table("situation_event_clusters_v3")
    op.drop_table("situation_signal_results_v3")
    op.drop_table("situation_analysis_runs_v3")
    op.drop_index("idx_disease_series_observation_identity_time", table_name="disease_series_observations")
