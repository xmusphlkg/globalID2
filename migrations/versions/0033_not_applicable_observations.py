"""Retain explicit source not-applicable cells as distinct NULL observations."""

from alembic import op

revision = "0033_not_applicable_observations"
down_revision = "0032_situation_host_metadata"
branch_labels = None
depends_on = None


def upgrade():
    # Every row accepted by the old check is accepted by this extension. NOT
    # VALID avoids scanning the full observation archive under an exclusive
    # lock; PostgreSQL still checks every new or updated row immediately.
    op.execute("ALTER TABLE disease_series_observations "
               "DROP CONSTRAINT ck_disease_series_observation_value_or_suppressed, "
               "ADD CONSTRAINT ck_disease_series_observation_value_or_suppressed "
               "CHECK (suppressed OR value IS NOT NULL OR "
               "metadata->>'missing_value_status' IN ('source_missing', 'not_applicable')) NOT VALID")


def downgrade():
    # Validation deliberately refuses a downgrade while N.A. NULL facts exist.
    # No facts are deleted or reclassified to make a downgrade succeed.
    op.execute("ALTER TABLE disease_series_observations "
               "DROP CONSTRAINT ck_disease_series_observation_value_or_suppressed, "
               "ADD CONSTRAINT ck_disease_series_observation_value_or_suppressed "
               "CHECK (suppressed OR value IS NOT NULL OR "
               "metadata->>'missing_value_status' = 'source_missing')")
