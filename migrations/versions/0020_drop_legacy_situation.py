"""Drop the legacy statistical Situation Room tables (v2 and v3).

The notice-based Situation module (revision 0019) replaces them; no code reads
these tables any more. This is destructive and intentionally has no data-
preserving downgrade: the old module's code is gone, so recreating empty
tables would serve no reader.

Revision ID: 0020_drop_legacy_situation
Revises: 0019_situation_notices
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_drop_legacy_situation"
down_revision = "0019_situation_notices"
branch_labels = None
depends_on = None

# Children before parents so foreign keys never block a drop.
LEGACY_TABLES = (
    "situation_signal_results_v3",
    "situation_event_cluster_items_v3",
    "situation_event_clusters_v3",
    "situation_report_members_v3",
    "situation_review_decisions_v3",
    "situation_publication_pointers_v3",
    "situation_period_reports_v3",
    "situation_policy_decisions_v3",
    "situation_calibration_runs_v3",
    "situation_event_labels_v3",
    "situation_analysis_runs_v3",
    "situation_overrides",
    "situation_snapshots",
    "public_health_events",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in LEGACY_TABLES:
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    # Dropped data cannot be recreated and no code reads these tables, so the
    # downgrade only moves the revision pointer. Restore a database backup to
    # recover the legacy rows. ``upgrade`` skips absent tables, so a
    # downgrade/upgrade round trip stays safe.
    return None
