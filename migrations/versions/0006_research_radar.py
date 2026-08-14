"""Add Research Radar literature storage and task type."""

from alembic import op
import sqlalchemy as sa


revision = "0006_research_radar"
down_revision = "0005_situation_source_task"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'SYNC_LITERATURE'")
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'sync_literature'")

    op.create_table(
        "literature_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), nullable=False, unique=True),
        sa.Column("slug", sa.String(320), nullable=False, unique=True),
        sa.Column("doi", sa.String(300), unique=True),
        sa.Column("pmid", sa.String(40), unique=True),
        sa.Column("pmcid", sa.String(40), unique=True),
        sa.Column("openalex_id", sa.String(80), unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("journal", sa.String(500)),
        sa.Column("issn", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("publisher", sa.String(500)),
        sa.Column("authors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("article_type", sa.String(80), nullable=False, server_default="journal-article"),
        sa.Column("study_type", sa.String(120)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.Column("abstract_text", sa.Text()),
        sa.Column("abstract_license", sa.String(500)),
        sa.Column("source_urls", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("open_access_status", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("open_access_url", sa.String(2000)),
        sa.Column("license_url", sa.String(2000)),
        sa.Column("peer_review_status", sa.String(40), nullable=False, server_default="peer_reviewed"),
        sa.Column("integrity_status", sa.String(40), nullable=False, server_default="current"),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("public_health_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("discovery_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("publication_status", sa.String(40), nullable=False, server_default="review"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index("idx_literature_article_published", "literature_articles", ["published_at"])
    op.create_index("idx_literature_article_status", "literature_articles", ["publication_status"])
    op.create_index("idx_literature_article_discovery", "literature_articles", ["discovery_score"])
    op.create_index("idx_literature_article_integrity", "literature_articles", ["integrity_status"])

    op.create_table(
        "literature_disease_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("disease_id", sa.String(100), sa.ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("match_terms", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        *_timestamps(),
        sa.UniqueConstraint("article_id", "disease_id", name="uq_literature_article_disease"),
    )
    op.create_index("idx_literature_disease_link_disease", "literature_disease_links", ["disease_id"])

    op.create_table(
        "literature_country_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_code", sa.String(20), nullable=False),
        sa.Column("country_name", sa.String(200), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("article_id", "country_code", name="uq_literature_article_country"),
    )
    op.create_index("idx_literature_country_link_country", "literature_country_links", ["country_code"])

    op.create_table(
        "literature_topic_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("article_id", "topic", name="uq_literature_article_topic"),
    )
    op.create_index("idx_literature_topic_link_topic", "literature_topic_links", ["topic"])

    op.create_table(
        "literature_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(12), nullable=False),
        sa.Column("research_question", sa.Text()),
        sa.Column("study_design", sa.Text()),
        sa.Column("population_setting", sa.Text()),
        sa.Column("main_findings", sa.Text()),
        sa.Column("public_health_relevance", sa.Text()),
        sa.Column("limitations", sa.Text()),
        sa.Column("gids_interpretation", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("generated_by", sa.String(120)),
        *_timestamps(),
        sa.UniqueConstraint("article_id", "language", name="uq_literature_summary_language"),
    )
    op.create_index("idx_literature_summary_status", "literature_summaries", ["status"])

    op.create_table(
        "literature_status_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("previous_status", sa.String(60)),
        sa.Column("current_status", sa.String(60), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_index("idx_literature_status_event_article", "literature_status_events", ["article_id"])

    op.create_table(
        "literature_ingest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("from_indexed_at", sa.DateTime(timezone=True)),
        sa.Column("through_indexed_at", sa.DateTime(timezone=True)),
        sa.Column("checkpoint", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text()),
        *_timestamps(),
    )
    op.create_index("idx_literature_ingest_run_started", "literature_ingest_runs", ["started_at"])
    op.create_index("idx_literature_ingest_run_status", "literature_ingest_runs", ["status"])


def downgrade() -> None:
    for table in (
        "literature_ingest_runs",
        "literature_status_events",
        "literature_summaries",
        "literature_topic_links",
        "literature_country_links",
        "literature_disease_links",
        "literature_articles",
    ):
        op.drop_table(table)
