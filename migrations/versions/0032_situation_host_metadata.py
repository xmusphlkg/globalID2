"""Preserve explicit human and animal hosts without classifying legacy alerts.

Revision ID: 0032_situation_host_metadata
Revises: 0031_ai_success_log_index
"""

from alembic import op
import sqlalchemy as sa

revision = "0032_situation_host_metadata"
down_revision = "0031_ai_success_log_index"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("situation_alerts", sa.Column("host_type", sa.String(20), nullable=False, server_default="unknown"))
    op.add_column("situation_alerts", sa.Column("host_species", sa.String(240), nullable=True))
    op.add_column("situation_alerts", sa.Column("count_unit", sa.String(20), nullable=False, server_default="unknown"))
    op.add_column("situation_alerts", sa.Column("host_evidence", sa.Text(), nullable=True))


def downgrade():
    for name in ("host_evidence", "count_unit", "host_species", "host_type"):
        op.drop_column("situation_alerts", name)
