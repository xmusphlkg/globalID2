"""Cover sampled health success totals without reading historical log rows.

Revision ID: 0031_ai_success_log_index
Revises: 0030_ai_activity_leases
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_ai_success_log_index"
down_revision = "0030_ai_activity_leases"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_ai_model_call_logs_success_model"


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                INDEX_NAME,
                "ai_model_call_logs",
                ["model_key"],
                postgresql_where=sa.text("status = 'success'"),
                postgresql_concurrently=True,
            )
    else:
        op.create_index(
            INDEX_NAME,
            "ai_model_call_logs",
            ["model_key"],
            sqlite_where=sa.text("status = 'success'"),
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(
                INDEX_NAME,
                table_name="ai_model_call_logs",
                postgresql_concurrently=True,
            )
    else:
        op.drop_index(INDEX_NAME, table_name="ai_model_call_logs")
