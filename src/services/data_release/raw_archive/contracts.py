"""Shared types for raw archive publication and verification."""
from __future__ import annotations

from dataclasses import dataclass


class RawArchiveError(RuntimeError):
    """Raised when the raw-data mirror cannot be updated safely."""


@dataclass(frozen=True)
class PartRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LargeFileRecord:
    path: str
    size: int
    sha256: str
    parts: tuple[PartRecord, ...]


@dataclass(frozen=True)
class ArchiveResult:
    mode: str
    branch: str
    source_file_count: int
    source_bytes: int
    added_file_count: int
    updated_file_count: int
    removed_storage_path_count: int
    split_file_count: int
    commit_count: int
    changed: bool


@dataclass(frozen=True)
class _SyncItem:
    source_path: str
    storage_paths: tuple[str, ...]
    stored_bytes: int
