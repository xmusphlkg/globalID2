"""Persist request-level AI model call telemetry."""

from alembic import op
import sqlalchemy as sa


revision = "0016_ai_model_call_logs"
down_revision = "0015_subdivision_geography"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_model_call_logs" not in inspector.get_table_names():
        op.create_table(
            "ai_model_call_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("model_id", sa.Integer(), nullable=True),
            sa.Column("provider_id", sa.Integer(), nullable=True),
            sa.Column("model_key", sa.String(length=200), nullable=False),
            sa.Column("model_name", sa.String(length=120), nullable=False),
            sa.Column("provider_key", sa.String(length=120), nullable=False),
            sa.Column("agent_name", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("latency_ms", sa.Float(), nullable=False),
            sa.Column("error_type", sa.String(length=160), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("ai_model_call_logs")}
    for name, columns in (
        ("idx_ai_model_call_logs_created", ["created_at"]),
        ("idx_ai_model_call_logs_provider_created", ["provider_key", "created_at"]),
        ("idx_ai_model_call_logs_model_created", ["model_key", "created_at"]),
        ("idx_ai_model_call_logs_request", ["request_id"]),
    ):
        if name not in indexes:
            op.create_index(name, "ai_model_call_logs", columns)


def downgrade() -> None:
    op.drop_index("idx_ai_model_call_logs_request", table_name="ai_model_call_logs")
    op.drop_index("idx_ai_model_call_logs_model_created", table_name="ai_model_call_logs")
    op.drop_index("idx_ai_model_call_logs_provider_created", table_name="ai_model_call_logs")
    op.drop_index("idx_ai_model_call_logs_created", table_name="ai_model_call_logs")
    op.drop_table("ai_model_call_logs")
