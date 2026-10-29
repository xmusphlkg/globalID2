"""Rebuild the Situation module around alerts read from full report text.

Adds ``situation_documents``, ``situation_extractions``, ``situation_alerts``
and ``situation_outbreaks``. Drops the title-based AI tables they replace
(assessments, events, digests, keyword entities): they held only AI output
derived from metadata and are regenerated from the stored reports.

``situation_notices`` keeps its rows. Every report goes back to ``pending`` so
the next run fetches its document and extracts alerts from it.

Revision ID: 0021_situation_alerts
Revises: 0020_drop_legacy_situation
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_situation_alerts"
down_revision = "0020_drop_legacy_situation"
branch_labels = None
depends_on = None

OBSOLETE_TABLES = (
    "situation_event_notices",
    "situation_events",
    "situation_digests",
    "situation_ai_assessments",
    "situation_notice_entities",
)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]


def _json(default: str) -> dict:
    return {"nullable": False, "server_default": sa.text(f"'{default}'")}


def _notice_fk() -> sa.Column:
    return sa.Column(
        "notice_id", sa.String(200), sa.ForeignKey("situation_notices.notice_id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in OBSOLETE_TABLES:
        if inspector.has_table(table):
            op.drop_table(table)

    op.create_table(
        "situation_documents",
        *_base(),
        _notice_fk(),
        sa.Column("ref", sa.JSON(), **_json("{}")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("media_type", sa.String(60)),
        sa.Column("text", sa.Text()),
        sa.Column("text_hash", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("cache_path", sa.String(400)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("notice_id", name="uq_situation_documents_notice_id"),
    )
    op.create_index("idx_situation_document_queue", "situation_documents", ["status", "next_attempt_at"])

    op.create_table(
        "situation_extractions",
        *_base(),
        _notice_fk(),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("document_type", sa.String(30)),
        sa.Column("input_mode", sa.String(20)),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("author_model", sa.String(200)),
        sa.Column("reviewer_model", sa.String(200)),
        sa.Column("review_verdict", sa.String(20)),
        sa.Column("review_notes", sa.JSON(), **_json("[]")),
        sa.Column("review_rounds", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_score", sa.Float()),
    )
    op.create_index("idx_situation_extraction_notice", "situation_extractions", ["notice_id", "status"])

    op.create_table(
        "situation_alerts",
        *_base(),
        sa.Column("alert_id", sa.String(80), nullable=False, unique=True),
        _notice_fk(),
        sa.Column(
            "extraction_id",
            sa.Integer(),
            sa.ForeignKey("situation_extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outbreak_id", sa.String(80)),
        sa.Column("disease_id", sa.String(40)),
        sa.Column("disease_label", sa.String(240), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("admin_area", sa.String(240)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cases", sa.BigInteger()),
        sa.Column("deaths", sa.BigInteger()),
        sa.Column("count_basis", sa.String(20)),
        sa.Column("as_of", sa.Date()),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("summary_en", sa.Text()),
        sa.Column("summary_zh", sa.Text()),
        sa.Column("summary_fr", sa.Text()),
        sa.Column("published_date", sa.Date(), nullable=False),
        sa.Column("source_id", sa.String(40), nullable=False),
    )
    op.create_index("idx_situation_alert_place", "situation_alerts", ["country_code", "disease_id", "published_date"])
    op.create_index("idx_situation_alert_outbreak", "situation_alerts", ["outbreak_id", "published_date"])
    op.create_index("idx_situation_alert_notice", "situation_alerts", ["notice_id"])

    op.create_table(
        "situation_outbreaks",
        *_base(),
        sa.Column("outbreak_id", sa.String(80), nullable=False, unique=True),
        sa.Column("disease_id", sa.String(40)),
        sa.Column("disease_label", sa.String(240), nullable=False),
        sa.Column("first_published", sa.Date(), nullable=False),
        sa.Column("last_published", sa.Date(), nullable=False),
        sa.Column("country_codes", sa.JSON(), **_json("[]")),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ids", sa.JSON(), **_json("[]")),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.create_index("idx_situation_outbreak_disease", "situation_outbreaks", ["disease_id", "last_published"])
    op.create_index("idx_situation_outbreak_status", "situation_outbreaks", ["status", "last_published"])

    # The old reading was title-only; every report is read again from its text.
    op.execute(
        "UPDATE situation_notices SET ai_status = 'pending', ai_attempts = 0, "
        "ai_last_error = NULL, ai_next_attempt_at = NULL"
    )


def downgrade() -> None:
    # The dropped AI tables held derived output only. They come back empty so
    # revision 0020 stays structurally valid; reports are untouched.
    for table in ("situation_outbreaks", "situation_alerts", "situation_extractions", "situation_documents"):
        op.drop_table(table)

    op.create_table(
        "situation_notice_entities",
        *_base(),
        _notice_fk(),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(40), nullable=False),
        sa.Column("name_en", sa.String(240)),
        sa.Column("name_zh", sa.String(240)),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("notice_id", "entity_type", "entity_id", name="uq_situation_notice_entity"),
    )
    op.create_index(
        "idx_situation_notice_entity_lookup", "situation_notice_entities", ["entity_type", "entity_id", "active"]
    )
    op.create_table(
        "situation_ai_assessments",
        *_base(),
        _notice_fk(),
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
        sa.Column("disease_ids", sa.JSON(), **_json("[]")),
        sa.Column("country_codes", sa.JSON(), **_json("[]")),
        sa.Column("author_model", sa.String(200)),
        sa.Column("reviewer_model", sa.String(200)),
        sa.Column("review_verdict", sa.String(20)),
        sa.Column("review_notes", sa.JSON(), **_json("[]")),
        sa.Column("review_rounds", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("idx_situation_ai_assessment_notice", "situation_ai_assessments", ["notice_id", "status"])
    op.create_table(
        "situation_events",
        *_base(),
        sa.Column("event_id", sa.String(200), nullable=False, unique=True),
        sa.Column("disease_id", sa.String(40), nullable=False),
        sa.Column("country_codes", sa.JSON(), **_json("[]")),
        sa.Column("first_published", sa.Date(), nullable=False),
        sa.Column("last_published", sa.Date(), nullable=False),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_ids", sa.JSON(), **_json("[]")),
        sa.Column("risk_level", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.create_index("idx_situation_event_disease", "situation_events", ["disease_id", "last_published"])
    op.create_index("idx_situation_event_status", "situation_events", ["status", "last_published"])
    op.create_table(
        "situation_event_notices",
        *_base(),
        sa.Column(
            "event_id", sa.String(200), sa.ForeignKey("situation_events.event_id", ondelete="CASCADE"), nullable=False
        ),
        _notice_fk(),
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
        sa.Column("points", sa.JSON(), **_json("[]")),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("author_model", sa.String(200)),
        sa.Column("reviewer_model", sa.String(200)),
        sa.Column("review_verdict", sa.String(20)),
        sa.Column("review_notes", sa.JSON(), **_json("[]")),
        sa.Column("review_score", sa.Float()),
    )
    op.create_index("idx_situation_digest_period", "situation_digests", ["period_key", "status"])
