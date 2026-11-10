"""Owner-scoped activity leases, fencing tokens and execution deadlines.

Revision ID: 0030_ai_activity_leases
Revises: 0029_ai_review_workflows
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_ai_activity_leases"
down_revision = "0029_ai_review_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ai_work_items",
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("ai_work_items", sa.Column("lease_owner", sa.String(64)))
    op.add_column("ai_work_items", sa.Column("lease_token", sa.String(36)))
    op.add_column("ai_work_items", sa.Column("leased_at", sa.DateTime(timezone=True)))
    op.add_column("ai_work_items", sa.Column("deadline_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_ai_work_items_owner", "ai_work_items", ["lease_owner", "status"]
    )
    # This is a coordinated cutover, not a rolling upgrade. Old workers must be
    # stopped first: their request files contain the removed routing/retry API,
    # and their leases cannot be fenced. Parent replay submits the new contract.
    op.execute(
        sa.text("""
        UPDATE ai_work_items
        SET status = 'failed', finished_at = CURRENT_TIMESTAMP,
            lease_expires_at = NULL, route_key = NULL,
            error = 'Superseded by AI runtime cutover; replay the parent task'
        WHERE status IN ('queued', 'leased')
    """)
    )
    # Translate persisted parent arguments once. The new runtime deliberately
    # does not accept the removed coroutine-pool controls on every invocation.
    op.execute(
        sa.text("""
        UPDATE tasks
        SET input_data = jsonb_set(input_data::jsonb, '{arguments}',
            jsonb_strip_nulls(jsonb_build_object(
                'kind', input_data::jsonb #> '{arguments,kind}',
                'buffer_size', CASE
                    WHEN jsonb_typeof(input_data::jsonb #> '{arguments,buffer_size}') = 'number'
                    THEN CASE WHEN (input_data::jsonb #>> '{arguments,buffer_size}')::numeric BETWEEN 1 AND 512
                          AND trunc((input_data::jsonb #>> '{arguments,buffer_size}')::numeric)
                              = (input_data::jsonb #>> '{arguments,buffer_size}')::numeric
                         THEN input_data::jsonb #> '{arguments,buffer_size}'
                         ELSE '512'::jsonb END
                    ELSE '512'::jsonb END,
                'max_items', CASE
                    WHEN jsonb_typeof(input_data::jsonb #> '{arguments,max_claims}') = 'number'
                    THEN CASE WHEN (input_data::jsonb #>> '{arguments,max_claims}')::numeric > 0
                         THEN input_data::jsonb #> '{arguments,max_claims}' END
                    ELSE NULL END
            )))
        WHERE status::text IN ('PENDING', 'QUEUED', 'RUNNING', 'RETRYING', 'FAILED', 'CANCELLED')
          AND input_data::jsonb ->> 'operation' = 'governance_stream'
          AND (input_data::jsonb -> 'arguments') ?| ARRAY[
              'workers', 'fetch_size', 'commit_every', 'commit_seconds',
              'stall_limit', 'pause_seconds', 'max_claims']
    """)
    )


def downgrade():
    op.drop_index("ix_ai_work_items_owner", table_name="ai_work_items")
    for column in (
        "deadline_at",
        "leased_at",
        "lease_token",
        "lease_owner",
        "cancel_requested",
    ):
        op.drop_column("ai_work_items", column)
