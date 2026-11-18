"""Persist R2 object receipts and publication references in PostgreSQL."""
from alembic import op
import sqlalchemy as sa

revision = "0034_r2_publication_index"
down_revision = "0033_not_applicable_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "r2_targets",
        sa.Column("target", sa.String(64), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("inventory_at", sa.Float()),
        sa.Column("stored_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("endpoint", "bucket", name="uq_r2_object_targets_endpoint_bucket"),
    )
    op.create_table(
        "r2_objects",
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text()),
        sa.Column("content_encoding", sa.Text()),
        sa.Column("cache_control", sa.Text()),
        sa.Column("metadata_known", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmed_at", sa.Float(), nullable=False),
        sa.Column("content_sha256", sa.String(64)),
        sa.ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target", "key"),
        sa.CheckConstraint("bytes >= 0", name="ck_r2_objects_bytes_nonnegative"),
    )
    op.create_table(
        "r2_reservations",
        sa.Column("token", sa.String(32), primary_key=True),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("growth", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
        sa.UniqueConstraint("target", "key", name="uq_r2_upload_reservations_target_key"),
        sa.CheckConstraint("bytes >= 0 AND growth >= 0", name="ck_r2_upload_reservations_sizes_nonnegative"),
    )
    op.create_index("ix_r2_objects_target_etag", "r2_objects", ["target", "etag"])
    op.create_table(
        "r2_publication_channels",
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("pointer_key", sa.Text(), nullable=False),
        sa.Column("active_digest", sa.String(64)),
        sa.ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target", "channel"),
        sa.UniqueConstraint("target", "pointer_key", name="uq_r2_publication_pointer"),
    )
    op.create_table(
        "r2_publication_releases",
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.ForeignKeyConstraint(["target", "channel"], ["r2_publication_channels.target", "r2_publication_channels.channel"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target", "channel", "digest"),
        sa.CheckConstraint("state IN ('staged', 'active')", name="ck_r2_publication_release_state"),
    )
    op.create_table(
        "r2_publication_refs",
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["target", "channel", "digest"], ["r2_publication_releases.target", "r2_publication_releases.channel", "r2_publication_releases.digest"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target", "channel", "digest", "key"),
    )
    op.create_index("ix_r2_publication_refs_key", "r2_publication_refs", ["target", "key"])
    op.create_table(
        "r2_publication_garbage",
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target", "key"),
    )


def downgrade():
    op.drop_table("r2_publication_garbage")
    op.drop_index("ix_r2_publication_refs_key", table_name="r2_publication_refs")
    op.drop_table("r2_publication_refs")
    op.drop_table("r2_publication_releases")
    op.drop_table("r2_publication_channels")
    op.drop_index("ix_r2_objects_target_etag", table_name="r2_objects")
    op.drop_table("r2_reservations")
    op.drop_table("r2_objects")
    op.drop_table("r2_targets")
