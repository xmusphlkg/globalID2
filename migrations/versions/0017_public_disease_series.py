"""Add the public disease-series read model.

The ingestion tables stay unchanged. These tables hold the projected curves
that a successful crawl refreshes for one country.

Revision ID: 0017_public_disease_series
Revises: 0016_ai_model_call_logs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0017_public_disease_series"
down_revision = "0016_ai_model_call_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "public_disease_series" not in tables:
        op.create_table(
            "public_disease_series",
            sa.Column("country_code", sa.String(length=10), nullable=False),
            sa.Column("disease_id", sa.String(length=100), nullable=False),
            sa.Column("period_starts", postgresql.ARRAY(sa.Date()), nullable=False),
            sa.Column("cases", postgresql.ARRAY(sa.Integer()), nullable=False),
            sa.Column("deaths", postgresql.ARRAY(sa.Integer()), nullable=False),
            sa.Column("incidence", postgresql.ARRAY(postgresql.DOUBLE_PRECISION()), nullable=False),
            sa.Column("granularity", sa.String(length=40), nullable=False),
            sa.Column("data_layer", sa.String(length=40), nullable=False),
            sa.Column("source_series_code", sa.String(length=180), nullable=True),
            sa.Column("point_count", sa.Integer(), nullable=False),
            sa.Column("total_cases", sa.BigInteger(), nullable=False),
            sa.Column("total_deaths", sa.BigInteger(), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=True),
            sa.Column("period_end", sa.Date(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("source_high_water", sa.DateTime(timezone=True), nullable=True),
            sa.Column("projection_version", sa.String(length=40), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "cardinality(period_starts) = cardinality(cases) "
                "AND cardinality(period_starts) = cardinality(deaths) "
                "AND cardinality(period_starts) = cardinality(incidence)",
                name="ck_public_disease_series_array_lengths",
            ),
            sa.CheckConstraint(
                "char_length(content_hash) = 64",
                name="ck_public_disease_series_content_hash",
            ),
            sa.ForeignKeyConstraint(
                ["country_code"],
                ["countries.code"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["disease_id"],
                ["standard_diseases.disease_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("country_code", "disease_id"),
        )
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("public_disease_series")}
    if "idx_public_disease_series_disease" not in indexes:
        op.create_index(
            "idx_public_disease_series_disease",
            "public_disease_series",
            ["disease_id", "country_code"],
        )

    inspector = sa.inspect(bind)
    if "country_publish_state" not in set(inspector.get_table_names()):
        op.create_table(
            "country_publish_state",
            sa.Column("country_code", sa.String(length=10), nullable=False),
            sa.Column("source_high_water", sa.DateTime(timezone=True), nullable=True),
            sa.Column("series_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("point_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("projection_version", sa.String(length=40), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "char_length(content_hash) = 64",
                name="ck_country_publish_state_content_hash",
            ),
            sa.ForeignKeyConstraint(
                ["country_code"],
                ["countries.code"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("country_code"),
        )


def downgrade() -> None:
    op.drop_table("country_publish_state")
    op.drop_index(
        "idx_public_disease_series_disease",
        table_name="public_disease_series",
    )
    op.drop_table("public_disease_series")
