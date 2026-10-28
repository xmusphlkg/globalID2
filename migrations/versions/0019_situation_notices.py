"""Add the notice-based Situation module tables.

The new module is built on official notices from international authorities
(WHO, ECDC, Africa CDC, PAHO) with peer-reviewed AI enrichment. The legacy
Situation v2/v3 tables are dropped in a later revision, once the release
pipeline no longer reads them.

Revision ID: 0019_situation_notices
Revises: 0018_obs_mapping_override_idx
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_situation_notices"
down_revision = "0018_obs_mapping_override_idx"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]


def _json_list() -> dict:
    return {"nullable": False, "server_default": sa.text("'[]'")}


def upgrade() -> None:
    op.create_table(
        "situation_source_runs",
        *_base(),
        sa.Column("run_id", sa.String(120), nullable=False),
        sa.Column("source_id", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("run_id", "source_id", name="uq_situation_source_run"),
    )
    op.create_index("idx_situation_source_run_source", "situation_source_runs", ["source_id", "started_at"])

    op.create_table(
        "situation_notices",
        *_base(),
        sa.Column("notice_id", sa.String(200), nullable=False, unique=True),
        sa.Column("source_id", sa.String(40), nullable=False),
        sa.Column("native_id", sa.String(200), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("title_en", sa.String(1000), nullable=False),
        sa.Column("summary_en", sa.Text()),
        sa.Column("url", sa.String(1500), nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("redistribution", sa.String(40), nullable=False),
        sa.Column("published_date", sa.Date(), nullable=False),
        sa.Column("updated_at_source", sa.String(40)),
        sa.Column("iso_week_key", sa.String(10), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ai_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_last_error", sa.Text()),
        sa.Column("ai_next_attempt_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_id", "native_id", name="uq_situation_notice_source_native"),
    )
    op.create_index("idx_situation_notice_published", "situation_notices", ["published_date"])
    op.create_index("idx_situation_notice_week", "situation_notices", ["iso_week_key"])
    op.create_index("idx_situation_notice_ai_queue", "situation_notices", ["ai_status", "ai_next_attempt_at"])

    op.create_table(
        "situation_notice_entities",
        *_base(),
        sa.Column(
            "notice_id",
            sa.String(200),
            sa.ForeignKey("situation_notices.notice_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(40), nullable=False),
        sa.Column("name_en", sa.String(240)),
        sa.Column("name_zh", sa.String(240)),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("notice_id", "entity_type", "entity_id", name="uq_situation_notice_entity"),
    )
    op.create_index(
        "idx_situation_notice_entity_lookup",
        "situation_notice_entities",
        ["entity_type", "entity_id", "active"],
    )

    op.create_table(
        "situation_ai_assessments",
        *_base(),
        sa.Column(
            "notice_id",
            sa.String(200),
            sa.ForeignKey("situation_notices.notice_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("title_zh", sa.String(1000)),
        sa.Column("title_fr", sa.String(1000)),
        sa.Column("summary_en", sa.Text()),
        sa.Column("summary_zh", sa.Text()),
        sa.Column("summary_fr", sa.Text()),
        sa.Column("event_type", sa.String(40)),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("risk_rationale", sa.Text()),
        sa.Column("disease_ids", sa.JSON(), **_json_list()),
        sa.Column("country_codes", sa.JSON(), **_json_list()),
        sa.Column("author_model", sa.String(200)),
        sa.Column("reviewer_model", sa.String(200)),
        sa.Column("review_verdict", sa.String(20)),
        sa.Column("review_notes", sa.JSON(), **_json_list()),
        sa.Column("review_rounds", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("idx_situation_ai_assessment_notice", "situation_ai_assessments", ["notice_id", "status"])

    op.create_table(
        "situation_events",
        *_base(),
        sa.Column("event_id", sa.String(200), nullable=False, unique=True),
        sa.Column("disease_id", sa.String(40), nullable=False),
        sa.Column("country_codes", sa.JSON(), **_json_list()),
        sa.Column("first_published", sa.Date(), nullable=False),
        sa.Column("last_published", sa.Date(), nullable=False),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ids", sa.JSON(), **_json_list()),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.create_index("idx_situation_event_disease", "situation_events", ["disease_id", "last_published"])
    op.create_index("idx_situation_event_status", "situation_events", ["status", "last_published"])

    op.create_table(
        "situation_event_notices",
        *_base(),
        sa.Column(
            "event_id",
            sa.String(200),
            sa.ForeignKey("situation_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notice_id",
            sa.String(200),
            sa.ForeignKey("situation_notices.notice_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", "notice_id", name="uq_situation_event_notice"),
    )

    op.create_table(
        "situation_digests",
        *_base(),
        sa.Column("period_key", sa.String(10), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("headline_en", sa.String(500)),
        sa.Column("headline_zh", sa.String(500)),
        sa.Column("headline_fr", sa.String(500)),
        sa.Column("points", sa.JSON(), **_json_list()),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("author_model", sa.String(200)),
        sa.Column("reviewer_model", sa.String(200)),
        sa.Column("review_verdict", sa.String(20)),
        sa.Column("review_notes", sa.JSON(), **_json_list()),
        sa.Column("review_score", sa.Float()),
    )
    op.create_index("idx_situation_digest_period", "situation_digests", ["period_key", "status"])


def downgrade() -> None:
    op.drop_table("situation_digests")
    op.drop_table("situation_event_notices")
    op.drop_table("situation_events")
    op.drop_table("situation_ai_assessments")
    op.drop_table("situation_notice_entities")
    op.drop_table("situation_notices")
    op.drop_table("situation_source_runs")
