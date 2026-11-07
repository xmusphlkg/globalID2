"""Record complete observation changes alongside legacy publication scopes.

Revision ID: 0026_publish_dirty_changes
Revises: 0025_report_period_types
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0026_publish_dirty_changes"
down_revision = "0025_report_period_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_dirty", sa.Column(
        "change_set", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("publish_dirty", "change_set")
