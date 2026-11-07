"""Add quarterly and annual report periods.

Revision ID: 0025_report_period_types
Revises: 0024_collection_jsonb
"""

from alembic import op

revision = "0025_report_period_types"
down_revision = "0024_collection_jsonb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'QUARTERLY'")
    op.execute("ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'ANNUAL'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely. Keep the values
    # during downgrade so existing quarterly/annual report rows remain valid.
    pass
