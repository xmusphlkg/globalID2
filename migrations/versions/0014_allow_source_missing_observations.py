"""Allow explicit source-missing observation cells with NULL values."""

from alembic import op


revision = "0014_source_missing_observations"
down_revision = "0013_literature_incremental"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_disease_series_observation_value_or_suppressed",
        "disease_series_observations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_disease_series_observation_value_or_suppressed",
        "disease_series_observations",
        "suppressed OR value IS NOT NULL OR "
        "metadata->>'missing_value_status' = 'source_missing'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_disease_series_observation_value_or_suppressed",
        "disease_series_observations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_disease_series_observation_value_or_suppressed",
        "disease_series_observations",
        "suppressed OR value IS NOT NULL",
    )
