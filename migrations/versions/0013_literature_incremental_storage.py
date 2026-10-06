"""Add incremental Research Radar payload maintenance metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0013_literature_incremental"
down_revision = "0012_literature_source_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "literature_articles",
        sa.Column(
            "source_payload_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "literature_articles",
        sa.Column("source_payload_compacted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_literature_article_status_id",
        "literature_articles",
        ["publication_status", "id"],
    )
    op.create_index(
        "idx_literature_article_payload_version_id",
        "literature_articles",
        ["source_payload_version", "id"],
    )
    op.create_index(
        "idx_literature_summary_status_id",
        "literature_summaries",
        ["status", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_literature_summary_status_id",
        table_name="literature_summaries",
    )
    op.drop_index(
        "idx_literature_article_payload_version_id",
        table_name="literature_articles",
    )
    op.drop_index(
        "idx_literature_article_status_id",
        table_name="literature_articles",
    )
    op.drop_column("literature_articles", "source_payload_compacted_at")
    op.drop_column("literature_articles", "source_payload_version")
