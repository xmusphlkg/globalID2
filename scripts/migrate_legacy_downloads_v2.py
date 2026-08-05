#!/usr/bin/env python3
"""Convert the current v1 download tree into a local GitHub-ready v2 snapshot."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.download_package_v2 import (  # noqa: E402
    build_frontend_download_manifest,
    build_globalid_download_package,
)
from src.core.data_share import (  # noqa: E402
    derive_github_raw_base_url,
    get_data_share_repo_url,
)
from src.generation.github_data_snapshot import (  # noqa: E402
    DEFAULT_RETAIN_RELEASES,
    build_github_snapshot,
)
from src.generation.sharded_data_package import (  # noqa: E402
    DEFAULT_MAX_UNCOMPRESSED_BYTES,
    PackageBuildError,
)
from scripts.verify_sharded_downloads import verify_package  # noqa: E402


DEFAULT_LEGACY_DIR = ROOT / "exports" / "site-downloads"
DEFAULT_V2_OUTPUT = ROOT / "exports" / "site-downloads-v2"
DEFAULT_GITHUB_SNAPSHOT_OUTPUT = ROOT / "exports" / "github-data-snapshot-v2"
DEFAULT_FRONTEND_MANIFEST_OUTPUT = ROOT / "astro-site" / "src" / "data" / "downloads.json"
DEFAULT_SNAPSHOT_BRANCH = "snapshot-v2"
DEFAULT_SNAPSHOT_URL_BASE = (
    derive_github_raw_base_url(get_data_share_repo_url(), DEFAULT_SNAPSHOT_BRANCH)
    or "/downloads-v2"
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"{label} must contain a JSON object: {path}")
    return document


def _safe_legacy_path(root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise RuntimeError("Legacy download path must be a non-empty string")
    if "\\" in relative_path or "\x00" in relative_path:
        raise RuntimeError(f"Unsafe legacy download path: {relative_path!r}")
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute() or any(
        part in {"", ".", ".."} for part in pure_path.parts
    ):
        raise RuntimeError(f"Unsafe legacy download path: {relative_path!r}")
    candidate = root.joinpath(*pure_path.parts)
    if candidate.is_symlink():
        raise RuntimeError(f"Legacy download file cannot be a symlink: {candidate}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve())
    except FileNotFoundError as exc:
        raise RuntimeError(f"Legacy download file is missing: {candidate}") from exc
    except ValueError as exc:
        raise RuntimeError(
            f"Legacy download file escapes its root: {candidate}"
        ) from exc
    return candidate


def _entries(manifest: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    value = manifest.get(kind)
    if not isinstance(value, list):
        raise RuntimeError(f"Legacy manifest field {kind!r} must be a list")
    if not all(isinstance(entry, dict) for entry in value):
        raise RuntimeError(f"Legacy manifest field {kind!r} contains non-objects")
    return value


def _record_total(entries: list[dict[str, Any]], kind: str) -> int:
    total = 0
    for index, entry in enumerate(entries):
        count = entry.get("record_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RuntimeError(
                f"Legacy {kind}[{index}].record_count must be a non-negative integer"
            )
        total += count
    return total


def iter_legacy_country_rows(
    legacy_dir: Path,
    country_entries: list[dict[str, Any]],
) -> Iterator[Mapping[str, Any]]:
    """Load one legacy country JSON at a time and stream its records."""

    for entry in country_entries:
        code = str(entry.get("code") or entry.get("id") or "").strip().lower()
        relative_path = entry.get("relative_json_path") or f"countries/{code}.json"
        path = _safe_legacy_path(legacy_dir, relative_path)
        payload = _load_object(path, "legacy country download")
        records = payload.get("records")
        if not isinstance(records, list):
            raise RuntimeError(f"Legacy download records must be a list: {path}")
        expected = entry.get("record_count")
        if len(records) != expected:
            raise RuntimeError(
                f"Legacy country record count mismatch for {code}: "
                f"manifest={expected}, file={len(records)}"
            )
        for row in records:
            if not isinstance(row, Mapping):
                raise RuntimeError(f"Legacy country row must be an object: {path}")
            yield row


def migrate_legacy_downloads(
    legacy_dir: Path,
    output_dir: Path,
    *,
    github_snapshot_output: Path | None,
    frontend_manifest_output: Path | None = None,
    snapshot_url_base: str = DEFAULT_SNAPSHOT_URL_BASE,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    retain_releases: int = DEFAULT_RETAIN_RELEASES,
) -> dict[str, Any]:
    legacy_root = Path(legacy_dir)
    manifest_path = legacy_root / "manifest.json"
    manifest = _load_object(manifest_path, "legacy manifest")
    country_entries = _entries(manifest, "countries")
    disease_entries = _entries(manifest, "diseases")
    country_total = _record_total(country_entries, "countries")
    disease_total = _record_total(disease_entries, "diseases")
    if country_total != disease_total:
        raise RuntimeError(
            "Legacy country and disease views disagree: "
            f"countries={country_total}, diseases={disease_total}"
        )
    generated_at = manifest.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise PackageBuildError("Legacy manifest generated_at is required")

    source_info_by_country: dict[str, Mapping[str, Any]] = {}
    for entry in country_entries:
        code = str(entry.get("code") or "").strip().upper()
        source_info = entry.get("source_info")
        if not code or not isinstance(source_info, Mapping):
            raise RuntimeError(
                f"Legacy country entry lacks code/source_info: {entry.get('id')!r}"
            )
        source_info_by_country[code] = source_info

    package_manifest = build_globalid_download_package(
        iter_legacy_country_rows(legacy_root, country_entries),
        output_dir,
        generated_at=generated_at,
        country_entries=country_entries,
        disease_entries=disease_entries,
        source_info_by_country=source_info_by_country,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    package_summary = verify_package(
        output_dir,
        legacy_manifest=manifest_path,
        expected_records=country_total,
    )
    summary: dict[str, Any] = {
        "status": "ok",
        "legacy_record_count": country_total,
        "release_id": package_manifest["release"]["release_id"],
        "v2_package": package_summary,
    }
    if github_snapshot_output is not None:
        snapshot = build_github_snapshot(
            output_dir,
            github_snapshot_output,
            retain_releases=retain_releases,
        )
        summary["github_snapshot"] = {
            "path": str(Path(github_snapshot_output).resolve()),
            "latest_release_id": snapshot.latest_release_id,
            "release_count": snapshot.release_count,
            "file_count": snapshot.file_count,
            "total_bytes": snapshot.total_bytes,
            "largest_file_bytes": snapshot.largest_file_bytes,
        }
    if frontend_manifest_output is not None:
        frontend_manifest = build_frontend_download_manifest(
            package_manifest,
            snapshot_url_base=snapshot_url_base,
            country_entries=country_entries,
            disease_entries=disease_entries,
        )
        frontend_path = Path(frontend_manifest_output)
        frontend_path.parent.mkdir(parents=True, exist_ok=True)
        frontend_path.write_text(
            json.dumps(frontend_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary["frontend_manifest"] = {
            "path": str(frontend_path.resolve()),
            "bytes": frontend_path.stat().st_size,
            "manifest_version": frontend_manifest["manifest_version"],
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate existing v1 country JSON downloads into a canonical, "
            "GitHub-friendly v2 snapshot without database or network access"
        )
    )
    parser.add_argument("--legacy-dir", type=Path, default=DEFAULT_LEGACY_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_V2_OUTPUT)
    parser.add_argument(
        "--github-snapshot-output",
        type=Path,
        default=DEFAULT_GITHUB_SNAPSHOT_OUTPUT,
    )
    parser.add_argument(
        "--skip-github-snapshot",
        action="store_true",
        help="Build and validate only the canonical release package",
    )
    parser.add_argument(
        "--frontend-manifest-output",
        type=Path,
        default=DEFAULT_FRONTEND_MANIFEST_OUTPUT,
    )
    parser.add_argument(
        "--snapshot-url-base",
        default=DEFAULT_SNAPSHOT_URL_BASE,
    )
    parser.add_argument(
        "--max-uncompressed-bytes",
        type=int,
        default=DEFAULT_MAX_UNCOMPRESSED_BYTES,
    )
    parser.add_argument(
        "--retain-releases",
        type=int,
        default=DEFAULT_RETAIN_RELEASES,
    )
    args = parser.parse_args()
    result = migrate_legacy_downloads(
        args.legacy_dir,
        args.output,
        github_snapshot_output=(
            None if args.skip_github_snapshot else args.github_snapshot_output
        ),
        frontend_manifest_output=args.frontend_manifest_output,
        snapshot_url_base=args.snapshot_url_base,
        max_uncompressed_bytes=args.max_uncompressed_bytes,
        retain_releases=args.retain_releases,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
