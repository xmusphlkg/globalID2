"""SQLAlchemy metadata for the R2 object and publication index tables."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from .base import Base


r2_targets = Table(
    "r2_targets",
    Base.metadata,
    Column("target", String(64), primary_key=True),
    Column("endpoint", Text, nullable=False),
    Column("bucket", Text, nullable=False),
    Column("inventory_at", Float),
    Column("stored_bytes", BigInteger, nullable=False, server_default=text("0")),
    UniqueConstraint("endpoint", "bucket", name="uq_r2_object_targets_endpoint_bucket"),
)

r2_objects = Table(
    "r2_objects",
    Base.metadata,
    Column("target", String(64), nullable=False),
    Column("key", Text, nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("etag", Text, nullable=False),
    Column("content_type", Text),
    Column("content_encoding", Text),
    Column("cache_control", Text),
    Column("metadata_known", Boolean, nullable=False, server_default=text("false")),
    Column("confirmed_at", Float, nullable=False),
    Column("content_sha256", String(64)),
    ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
    CheckConstraint("bytes >= 0", name="ck_r2_objects_bytes_nonnegative"),
)
r2_objects.append_constraint(PrimaryKeyConstraint("target", "key"))
Index("ix_r2_objects_target_etag", r2_objects.c.target, r2_objects.c.etag)

r2_reservations = Table(
    "r2_reservations",
    Base.metadata,
    Column("token", String(32), primary_key=True),
    Column("target", String(64), nullable=False),
    Column("key", Text, nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("growth", BigInteger, nullable=False),
    Column("created_at", Float, nullable=False),
    ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
    UniqueConstraint("target", "key", name="uq_r2_upload_reservations_target_key"),
    CheckConstraint("bytes >= 0 AND growth >= 0", name="ck_r2_upload_reservations_sizes_nonnegative"),
)

r2_publication_channels = Table(
    "r2_publication_channels",
    Base.metadata,
    Column("target", String(64), nullable=False),
    Column("channel", Text, nullable=False),
    Column("pointer_key", Text, nullable=False),
    Column("active_digest", String(64)),
    ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
    PrimaryKeyConstraint("target", "channel"),
    UniqueConstraint("target", "pointer_key", name="uq_r2_publication_pointer"),
)

r2_publication_releases = Table(
    "r2_publication_releases",
    Base.metadata,
    Column("target", String(64), nullable=False),
    Column("channel", Text, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("state", String(10), nullable=False),
    ForeignKeyConstraint(
        ["target", "channel"],
        ["r2_publication_channels.target", "r2_publication_channels.channel"],
        ondelete="CASCADE",
    ),
    PrimaryKeyConstraint("target", "channel", "digest"),
    CheckConstraint("state IN ('staged', 'active')", name="ck_r2_publication_release_state"),
)

r2_publication_refs = Table(
    "r2_publication_refs",
    Base.metadata,
    Column("target", String(64), nullable=False),
    Column("channel", Text, nullable=False),
    Column("digest", String(64), nullable=False),
    Column("key", Text, nullable=False),
    ForeignKeyConstraint(
        ["target", "channel", "digest"],
        [
            "r2_publication_releases.target",
            "r2_publication_releases.channel",
            "r2_publication_releases.digest",
        ],
        ondelete="CASCADE",
    ),
    PrimaryKeyConstraint("target", "channel", "digest", "key"),
)
Index("ix_r2_publication_refs_key", r2_publication_refs.c.target, r2_publication_refs.c.key)

r2_publication_garbage = Table(
    "r2_publication_garbage",
    Base.metadata,
    Column("target", String(64), nullable=False),
    Column("key", Text, nullable=False),
    ForeignKeyConstraint(["target"], ["r2_targets.target"], ondelete="CASCADE"),
    PrimaryKeyConstraint("target", "key"),
)


__all__ = [
    "r2_targets",
    "r2_objects",
    "r2_reservations",
    "r2_publication_channels",
    "r2_publication_releases",
    "r2_publication_refs",
    "r2_publication_garbage",
]
