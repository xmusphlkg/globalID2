"""Persist bounded literature review workflows and keyset drain cursors.

Revision ID: 0029_ai_review_workflows
Revises: 0028_ai_work_request_index
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_ai_review_workflows"
down_revision = "0028_ai_work_request_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_review_drains",
        sa.Column(
            "parent_task_uuid",
            sa.String(36),
            sa.ForeignKey("tasks.task_uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True, server_default="0"),
        sa.Column("cursor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("high_watermark", sa.BigInteger(), nullable=False),
        sa.Column("scanned", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("admitted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("exhausted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('articles','summaries')", name="ck_ai_review_drain_kind"
        ),
    )
    op.create_table(
        "ai_review_workflows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "parent_task_uuid",
            sa.String(36),
            sa.ForeignKey("tasks.task_uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("step", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("outcome", sa.String(16)),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("activity_id", sa.BigInteger(), sa.ForeignKey("ai_work_items.id")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('articles','summaries')", name="ck_ai_review_workflow_kind"
        ),
        sa.CheckConstraint(
            "status IN ('open','terminal')", name="ck_ai_review_workflow_status"
        ),
        sa.CheckConstraint(
            "step IN ('ready','author','reviewer','terminal')",
            name="ck_ai_review_workflow_step",
        ),
    )
    op.create_index(
        "uq_ai_review_workflow_open_subject",
        "ai_review_workflows",
        ["kind", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_ai_review_workflow_parent",
        "ai_review_workflows",
        ["parent_task_uuid", "generation", "status", "id"],
    )
    op.create_index(
        "ix_ai_review_workflow_subject",
        "ai_review_workflows",
        ["kind", "subject_id", "input_fingerprint", "policy_version"],
    )


def downgrade() -> None:
    op.drop_table("ai_review_workflows")
    op.drop_table("ai_review_drains")
