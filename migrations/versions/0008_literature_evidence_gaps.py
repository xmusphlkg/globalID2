"""Add persistent evidence-gap discovery and review relationships."""

from alembic import op
import sqlalchemy as sa


revision = "0008_literature_evidence_gaps"
down_revision = "0007_literature_enrichment"
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
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'DISCOVER_LITERATURE_GAPS'")
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'discover_literature_gaps'")

    op.create_table(
        "literature_evidence_gaps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gap_id", sa.String(64), nullable=False, unique=True),
        sa.Column("signal_id", sa.String(160), nullable=False),
        sa.Column("snapshot_id", sa.String(200)),
        sa.Column("signal_kind", sa.String(60), nullable=False),
        sa.Column("signal_section", sa.String(60), nullable=False),
        sa.Column("disease_id", sa.String(100), sa.ForeignKey("standard_diseases.disease_id", ondelete="CASCADE"), nullable=False),
        sa.Column("disease_name", sa.String(300), nullable=False),
        sa.Column("country_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("country_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("gap_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="open"),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("query_plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("latest_metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True)),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_searched_at", sa.DateTime(timezone=True)),
        sa.Column("next_search_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
        sa.UniqueConstraint("signal_id", "disease_id", name="uq_literature_gap_signal_disease"),
    )
    op.create_index("idx_literature_gap_status_priority", "literature_evidence_gaps", ["status", "priority_score"])
    op.create_index("idx_literature_gap_disease", "literature_evidence_gaps", ["disease_id"])
    op.create_index("idx_literature_gap_next_search", "literature_evidence_gaps", ["next_search_at"])

    op.create_table(
        "literature_signal_article_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gap_id", sa.String(64), sa.ForeignKey("literature_evidence_gaps.gap_id", ondelete="SET NULL")),
        sa.Column("signal_id", sa.String(160), nullable=False),
        sa.Column("article_id", sa.String(48), sa.ForeignKey("literature_articles.article_id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_level", sa.String(60), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="review"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("match_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(160)),
        sa.Column("review_note", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
        sa.UniqueConstraint("signal_id", "article_id", name="uq_literature_signal_article"),
    )
    op.create_index("idx_literature_signal_article_status", "literature_signal_article_links", ["status"])
    op.create_index("idx_literature_signal_article_gap", "literature_signal_article_links", ["gap_id"])
    op.create_index("idx_literature_signal_article_article", "literature_signal_article_links", ["article_id"])


def downgrade() -> None:
    op.drop_table("literature_signal_article_links")
    op.drop_table("literature_evidence_gaps")
