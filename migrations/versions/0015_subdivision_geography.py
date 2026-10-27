"""Add location_type and parent_code for subdivision support.

Revision ID: 0015_subdivision_geography
Revises: 0014_source_missing_observations
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_subdivision_geography"
down_revision = "0014_source_missing_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "countries",
        sa.Column("location_type", sa.String(50), nullable=True),
    )
    op.add_column(
        "countries",
        sa.Column("parent_code", sa.String(10), nullable=True),
    )
    op.execute("UPDATE countries SET location_type = 'country' WHERE location_type IS NULL")
    op.alter_column(
        "countries",
        "location_type",
        existing_type=sa.String(50),
        nullable=False,
        server_default="country",
    )
    op.create_index("ix_countries_location_type", "countries", ["location_type"])
    op.create_index("ix_countries_parent_code", "countries", ["parent_code"])


def downgrade() -> None:
    op.drop_index("ix_countries_parent_code", table_name="countries")
    op.drop_index("ix_countries_location_type", table_name="countries")
    op.drop_column("countries", "parent_code")
    op.drop_column("countries", "location_type")
