"""Index the observation keys used by mapping coverage.

Coverage groups every surveillance observation by series and by the source
disease code and label stored in its JSON payload. Those expressions are
immutable, so PostgreSQL can satisfy the grouping from an index instead of
parsing each JSON value after the scan.

Revision ID: 0018_obs_mapping_override_idx
Revises: 0017_public_disease_series
"""

from alembic import op


revision = "0018_obs_mapping_override_idx"
down_revision = "0017_public_disease_series"
branch_labels = None
depends_on = None

_CREATE_INDEX = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dso_mapping_override
ON disease_series_observations (
    series_code,
    (COALESCE(NULLIF((dimensions ->> 'source_disease_code'), ''), NULLIF((metadata ->> 'local_code'), ''))),
    (lower(btrim(COALESCE(NULLIF((dimensions ->> 'source_disease_label'), ''), NULLIF((metadata ->> 'local_label'), '')))))
)
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("SET maintenance_work_mem = '1GB'")
        op.execute(_CREATE_INDEX)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_dso_mapping_override")
