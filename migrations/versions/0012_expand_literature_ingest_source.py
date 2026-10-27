"""Allow complete literature ingest source labels."""

from alembic import op
import sqlalchemy as sa


revision = "0012_literature_source_length"
down_revision = "0011_ingest_task_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "literature_ingest_runs",
        "source",
        existing_type=sa.String(length=80),
        type_=sa.String(length=256),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Existing rows may contain labels longer than 80 after this migration.
    # Refuse a lossy downgrade instead of silently truncating audit history.
    bind = op.get_bind()
    oversized = bind.execute(
        sa.text(
            "SELECT 1 FROM literature_ingest_runs "
            "WHERE length(source) > 80 LIMIT 1"
        )
    ).first()
    if oversized:
        raise RuntimeError(
            "Cannot downgrade literature_ingest_runs.source while labels exceed 80 characters"
        )
    op.alter_column(
        "literature_ingest_runs",
        "source",
        existing_type=sa.String(length=256),
        type_=sa.String(length=80),
        existing_nullable=False,
    )
