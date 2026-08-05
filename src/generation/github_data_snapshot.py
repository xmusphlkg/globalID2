"""Assemble a bounded, GitHub-friendly tree of validated data releases.

The module performs local filesystem work only. It deliberately contains no
Git commands, GitHub API calls, credentials, or remote publishing behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from src.generation.sharded_data_package import (
    MANIFEST_FILENAME,
    UnsafePackagePathError,
    validate_sharded_data_package,
)


SNAPSHOT_MARKER_FILENAME = ".globalid-github-snapshot-v2"
SNAPSHOT_MARKER_CONTENT = "globalid-github-data-snapshot-v2\n"
LATEST_FILENAME = "latest.json"
RELEASES_DIRNAME = "releases"
DEFAULT_RETAIN_RELEASES = 3
DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TREE_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_DIRECTORY_ENTRIES = 3_000
_RELEASE_ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
_READ_CHUNK_BYTES = 1024 * 1024


class GitHubSnapshotError(RuntimeError):
    """Raised when a local snapshot tree is unsafe or inconsistent."""


@dataclass(frozen=True)
class GitHubSnapshotValidationResult:
    latest_release_id: str
    release_count: int
    file_count: int
    total_bytes: int
    largest_file_bytes: int


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _release_id(manifest: dict[str, Any]) -> str:
    release = manifest.get("release")
    if not isinstance(release, dict):
        raise GitHubSnapshotError("Package manifest.release must be an object")
    release_id = release.get("release_id")
    if not isinstance(release_id, str) or not _RELEASE_ID_PATTERN.fullmatch(
        release_id
    ):
        raise GitHubSnapshotError(
            f"Package release_id is missing or path-unsafe: {release_id!r}"
        )
    return release_id


def _safe_output_dir(output_dir: Path) -> Path:
    candidate = Path(output_dir).expanduser()
    if not candidate.name or candidate.name in {".", ".."}:
        raise UnsafePackagePathError(
            f"Output must name a dedicated snapshot directory: {output_dir}"
        )
    if candidate.is_symlink():
        raise UnsafePackagePathError("Snapshot output directory cannot be a symlink")
    resolved = candidate.parent.resolve() / candidate.name
    if resolved == Path(resolved.anchor):
        raise UnsafePackagePathError("Filesystem root cannot be a snapshot output")
    if resolved.exists():
        marker = resolved / SNAPSHOT_MARKER_FILENAME
        if (
            not resolved.is_dir()
            or marker.is_symlink()
            or not marker.is_file()
            or marker.read_text(encoding="utf-8") != SNAPSHOT_MARKER_CONTENT
        ):
            raise UnsafePackagePathError(
                "Refusing to replace a directory that was not created by the "
                "GitHub snapshot builder"
            )
    return resolved


def _reject_symlinks(root: Path) -> None:
    for current_root, directory_names, filenames in os.walk(
        root, followlinks=False
    ):
        current = Path(current_root)
        for name in [*directory_names, *filenames]:
            candidate = current / name
            if candidate.is_symlink():
                raise UnsafePackagePathError(
                    f"Snapshot trees cannot contain symlinks: {candidate}"
                )


def _same_release(first: Path, second: Path) -> bool:
    first_files = {
        path.relative_to(first).as_posix(): path
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path
        for path in second.rglob("*")
        if path.is_file()
    }
    if set(first_files) != set(second_files):
        return False
    return all(
        first_files[name].stat().st_size == second_files[name].stat().st_size
        and _sha256_file(first_files[name]) == _sha256_file(second_files[name])
        for name in first_files
    )


def _release_sort_key(release_dir: Path) -> tuple[str, str]:
    manifest = json.loads(
        (release_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    release = manifest.get("release") or {}
    generated_at = release.get("generated_at")
    return (str(generated_at or ""), release_dir.name)


def _copy_existing_releases(source: Path, destination: Path) -> None:
    source_releases = source / RELEASES_DIRNAME
    if not source_releases.exists():
        return
    _reject_symlinks(source_releases)
    for release_dir in sorted(source_releases.iterdir()):
        if not release_dir.is_dir():
            raise GitHubSnapshotError(
                f"Unexpected file in releases directory: {release_dir}"
            )
        validate_sharded_data_package(release_dir)
        shutil.copytree(release_dir, destination / release_dir.name)


def _install_release(package_dir: Path, releases_dir: Path, release_id: str) -> None:
    destination = releases_dir / release_id
    if destination.exists():
        if not _same_release(package_dir, destination):
            raise GitHubSnapshotError(
                f"Immutable release {release_id} already exists with different bytes"
            )
        return
    shutil.copytree(package_dir, destination)


def _prune_releases(
    releases_dir: Path,
    *,
    latest_release_id: str,
    retain_releases: int,
) -> None:
    release_dirs = [path for path in releases_dir.iterdir() if path.is_dir()]
    previous = sorted(
        (path for path in release_dirs if path.name != latest_release_id),
        key=_release_sort_key,
        reverse=True,
    )
    retained_names = {latest_release_id}
    retained_names.update(path.name for path in previous[: retain_releases - 1])
    for release_dir in release_dirs:
        if release_dir.name not in retained_names:
            shutil.rmtree(release_dir)


def _write_snapshot_metadata(
    staging_dir: Path,
    *,
    release_id: str,
    release_manifest: dict[str, Any],
    retain_releases: int,
) -> None:
    relative_manifest = (
        PurePosixPath(RELEASES_DIRNAME) / release_id / MANIFEST_FILENAME
    ).as_posix()
    manifest_path = staging_dir.joinpath(*PurePosixPath(relative_manifest).parts)
    latest = {
        "schema_version": 1,
        "protocol": "globalid.github-snapshot.v2",
        "release_id": release_id,
        "generated_at": (release_manifest.get("release") or {}).get(
            "generated_at"
        ),
        "manifest_path": relative_manifest,
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": _sha256_file(manifest_path),
        "retained_release_count": len(
            [path for path in (staging_dir / RELEASES_DIRNAME).iterdir() if path.is_dir()]
        ),
        "retention_limit": retain_releases,
    }
    (staging_dir / LATEST_FILENAME).write_bytes(_canonical_json_bytes(latest))
    (staging_dir / SNAPSHOT_MARKER_FILENAME).write_text(
        SNAPSHOT_MARKER_CONTENT,
        encoding="utf-8",
    )
    (staging_dir / "README.md").write_text(
        "# GlobalID canonical data snapshot\n\n"
        "This branch tree is generated. Start with `latest.json`, verify the "
        "referenced manifest SHA-256, then download only the country or disease "
        "index and gzip NDJSON shards you need.\n\n"
        "Do not edit release files manually.\n",
        encoding="utf-8",
    )


def _atomic_replace_directory(staging_dir: Path, output_dir: Path) -> None:
    if not output_dir.exists():
        os.replace(staging_dir, output_dir)
        return
    backup = output_dir.parent / f".{output_dir.name}.backup-{uuid4().hex}"
    os.replace(output_dir, backup)
    try:
        os.replace(staging_dir, output_dir)
    except BaseException:
        os.replace(backup, output_dir)
        raise
    shutil.rmtree(backup)


def validate_github_snapshot(
    snapshot_dir: Path,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_tree_bytes: int = DEFAULT_MAX_TREE_BYTES,
    max_directory_entries: int = DEFAULT_MAX_DIRECTORY_ENTRIES,
) -> GitHubSnapshotValidationResult:
    """Validate all retained releases and GitHub repository health guards."""

    for name, value in (
        ("max_file_bytes", max_file_bytes),
        ("max_tree_bytes", max_tree_bytes),
        ("max_directory_entries", max_directory_entries),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise GitHubSnapshotError(f"{name} must be a positive integer")
    root = Path(snapshot_dir)
    if root.is_symlink() or not root.is_dir():
        raise UnsafePackagePathError("Snapshot directory must be a real directory")
    marker = root / SNAPSHOT_MARKER_FILENAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_text(encoding="utf-8") != SNAPSHOT_MARKER_CONTENT
    ):
        raise GitHubSnapshotError("GitHub snapshot marker is missing or invalid")
    _reject_symlinks(root)

    try:
        latest_bytes = (root / LATEST_FILENAME).read_bytes()
        latest = json.loads(latest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubSnapshotError(f"Cannot read latest.json: {exc}") from exc
    if latest_bytes != _canonical_json_bytes(latest):
        raise GitHubSnapshotError("latest.json is not canonical JSON")
    if not isinstance(latest, dict) or latest.get("schema_version") != 1:
        raise GitHubSnapshotError("latest.json schema_version must be 1")
    if latest.get("protocol") != "globalid.github-snapshot.v2":
        raise GitHubSnapshotError("latest.json protocol is invalid")
    release_id = latest.get("release_id")
    if not isinstance(release_id, str) or not _RELEASE_ID_PATTERN.fullmatch(
        release_id
    ):
        raise GitHubSnapshotError("latest.json release_id is unsafe")
    expected_manifest = (
        PurePosixPath(RELEASES_DIRNAME) / release_id / MANIFEST_FILENAME
    ).as_posix()
    if latest.get("manifest_path") != expected_manifest:
        raise GitHubSnapshotError("latest.json manifest_path is inconsistent")

    releases_dir = root / RELEASES_DIRNAME
    if releases_dir.is_symlink() or not releases_dir.is_dir():
        raise GitHubSnapshotError("Snapshot releases directory is missing")
    release_entries = sorted(releases_dir.iterdir())
    if any(not path.is_dir() for path in release_entries):
        raise GitHubSnapshotError("Snapshot releases directory contains a file")
    release_dirs = release_entries
    if not release_dirs:
        raise GitHubSnapshotError("Snapshot must retain at least one release")
    retention_limit = latest.get("retention_limit")
    if (
        isinstance(retention_limit, bool)
        or not isinstance(retention_limit, int)
        or retention_limit < 1
        or len(release_dirs) > retention_limit
    ):
        raise GitHubSnapshotError("latest.json retention_limit is inconsistent")
    if len(release_dirs) != latest.get("retained_release_count"):
        raise GitHubSnapshotError("latest.json retained_release_count is inconsistent")
    if not (releases_dir / release_id).is_dir():
        raise GitHubSnapshotError("Latest release directory is missing")
    for release_dir in release_dirs:
        result = validate_sharded_data_package(release_dir)
        if _release_id(result.manifest) != release_dir.name:
            raise GitHubSnapshotError(
                f"Release directory name does not match manifest: {release_dir}"
            )

    manifest_path = root.joinpath(*PurePosixPath(expected_manifest).parts)
    if latest.get("manifest_bytes") != manifest_path.stat().st_size:
        raise GitHubSnapshotError("Latest manifest byte size mismatch")
    if latest.get("manifest_sha256") != _sha256_file(manifest_path):
        raise GitHubSnapshotError("Latest manifest SHA-256 mismatch")

    file_count = 0
    total_bytes = 0
    largest_file = 0
    for current_root, directory_names, filenames in os.walk(root):
        entry_count = len(directory_names) + len(filenames)
        if entry_count > max_directory_entries:
            relative = Path(current_root).relative_to(root)
            raise GitHubSnapshotError(
                f"Directory {relative} has {entry_count} entries; maximum is "
                f"{max_directory_entries}"
            )
        for filename in filenames:
            path = Path(current_root) / filename
            size = path.stat().st_size
            file_count += 1
            total_bytes += size
            largest_file = max(largest_file, size)
            if size > max_file_bytes:
                raise GitHubSnapshotError(
                    f"GitHub snapshot file exceeds {max_file_bytes} bytes: "
                    f"{path.relative_to(root)} ({size} bytes)"
                )
    if total_bytes > max_tree_bytes:
        raise GitHubSnapshotError(
            f"GitHub snapshot tree is {total_bytes} bytes; maximum is "
            f"{max_tree_bytes}"
        )

    return GitHubSnapshotValidationResult(
        latest_release_id=release_id,
        release_count=len(release_dirs),
        file_count=file_count,
        total_bytes=total_bytes,
        largest_file_bytes=largest_file,
    )


def build_github_snapshot(
    package_dir: Path,
    output_dir: Path,
    *,
    retain_releases: int = DEFAULT_RETAIN_RELEASES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_tree_bytes: int = DEFAULT_MAX_TREE_BYTES,
    max_directory_entries: int = DEFAULT_MAX_DIRECTORY_ENTRIES,
) -> GitHubSnapshotValidationResult:
    """Add one package to a bounded snapshot tree and atomically install it."""

    if (
        isinstance(retain_releases, bool)
        or not isinstance(retain_releases, int)
        or retain_releases < 1
    ):
        raise GitHubSnapshotError("retain_releases must be a positive integer")
    for name, value in (
        ("max_file_bytes", max_file_bytes),
        ("max_tree_bytes", max_tree_bytes),
        ("max_directory_entries", max_directory_entries),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise GitHubSnapshotError(f"{name} must be a positive integer")
    package_root = Path(package_dir)
    _reject_symlinks(package_root)
    package_result = validate_sharded_data_package(package_root)
    if package_result.manifest.get("package_mode") != "canonical_facts":
        raise GitHubSnapshotError(
            "GitHub snapshots require a canonical_facts package"
        )
    release_id = _release_id(package_result.manifest)

    destination = _safe_output_dir(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )
    try:
        releases_dir = staging_dir / RELEASES_DIRNAME
        releases_dir.mkdir()
        if destination.exists():
            _copy_existing_releases(destination, releases_dir)
        _install_release(package_root, releases_dir, release_id)
        _prune_releases(
            releases_dir,
            latest_release_id=release_id,
            retain_releases=retain_releases,
        )
        _write_snapshot_metadata(
            staging_dir,
            release_id=release_id,
            release_manifest=package_result.manifest,
            retain_releases=retain_releases,
        )
        result = validate_github_snapshot(
            staging_dir,
            max_file_bytes=max_file_bytes,
            max_tree_bytes=max_tree_bytes,
            max_directory_entries=max_directory_entries,
        )
        _atomic_replace_directory(staging_dir, destination)
        return result
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


__all__ = [
    "DEFAULT_MAX_DIRECTORY_ENTRIES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_TREE_BYTES",
    "DEFAULT_RETAIN_RELEASES",
    "GitHubSnapshotError",
    "GitHubSnapshotValidationResult",
    "build_github_snapshot",
    "validate_github_snapshot",
]
