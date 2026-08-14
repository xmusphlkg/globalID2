"""Add reviewable AI enrichment metadata for Research Radar."""

from alembic import op
import sqlalchemy as sa


revision = "0007_literature_enrichment"
down_revision = "0006_research_radar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'ENRICH_LITERATURE'")
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'enrich_literature'")

    op.add_column("literature_summaries", sa.Column("model", sa.String(160)))
    op.add_column("literature_summaries", sa.Column("provider", sa.String(80)))
    op.add_column("literature_summaries", sa.Column("quality_score", sa.Float()))
    op.add_column(
        "literature_summaries",
        sa.Column("evidence_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "literature_summaries",
        sa.Column("generation_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("literature_summaries", sa.Column("generated_at", sa.DateTime(timezone=True)))
    op.add_column("literature_summaries", sa.Column("review_notes", sa.Text()))


def downgrade() -> None:
    for column in (
        "review_notes",
        "generated_at",
        "generation_metadata",
        "evidence_map",
        "quality_score",
        "provider",
        "model",
    ):
        op.drop_column("literature_summaries", column)
