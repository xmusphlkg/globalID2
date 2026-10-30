"""Align collection and model-queue JSON columns with the JSONB contract.

Revision ID: 0024_collection_jsonb
Revises: 0023_ai_work_items

Only the newly introduced collection/model-queue tables are changed. Original
source bytes remain immutable files; these columns contain structured metadata
and model-work references.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_collection_jsonb"
down_revision = "0023_ai_work_items"
branch_labels = None
depends_on = None

JSON_COLUMNS = (
    ("run_stages", "metrics", False, True),
    ("source_artifacts", "metadata", False, True),
    ("ai_work_items", "payload", False, False),
    ("ai_work_items", "result", True, False),
)


def upgrade() -> None:
    for table, column, nullable, object_default in JSON_COLUMNS:
        if object_default:
            op.alter_column(table, column, server_default=None)
        op.alter_column(
            table,
            column,
            existing_type=sa.JSON(),
            existing_nullable=nullable,
            type_=postgresql.JSONB(),
            postgresql_using=f'"{column}"::jsonb',
        )
        if object_default:
            op.alter_column(table, column, server_default=sa.text("'{}'::jsonb"))


def downgrade() -> None:
    for table, column, nullable, object_default in reversed(JSON_COLUMNS):
        if object_default:
            op.alter_column(table, column, server_default=None)
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(),
            existing_nullable=nullable,
            type_=sa.JSON(),
            postgresql_using=f'"{column}"::json',
        )
        if object_default:
            op.alter_column(table, column, server_default=sa.text("'{}'::json"))
