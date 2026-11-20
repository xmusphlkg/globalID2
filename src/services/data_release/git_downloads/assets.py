"""Validate partition manifests and atomically synchronize their managed files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from src.generation.direct_download.manifest import expand_manifest, MAX_MANIFEST_BYTES


def files_match(source_path: Path, target_path: Path) -> bool:
    """Compare file contents without relying on filecmp's stat-based cache."""

    if source_path.stat().st_size != target_path.stat().st_size:
        return False
    with source_path.open("rb") as source_file, target_path.open("rb") as target_file:
        while source_chunk := source_file.read(1024 * 1024):
            if source_chunk != target_file.read(len(source_chunk)):
                return False
        return not target_file.read(1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_relative_path(relative_path: str, *, asset_directories=("countries", "diseases", "cells")) -> Path:
    """Return a safe, managed download asset path from manifest metadata."""

    candidate = Path(relative_path)
    if (
        not relative_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or not candidate.parts
        or candidate.parts[0] not in asset_directories
    ):
        raise RuntimeError(f"Unsafe manifest asset path: {relative_path!r}")
    return candidate


def _layout_directories(schema_version: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the directories a manifest owns, then the directories it must not contain."""

    if schema_version == 5:
        return ("cells",), ("countries", "diseases")
    if schema_version == 4:
        return ("countries", "diseases"), ("cells",)
    raise RuntimeError("Download manifest must use partition schema version 4 or 5")


def validate_source(source_dir: Path, branch: str, *, parse_asset_path=_asset_relative_path, asset_directories=("countries", "diseases", "cells"), max_file_bytes=100 * 1024 * 1024) -> dict:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing generated manifest: {manifest_path}. "
            "Run scripts/generate_site_data.py first."
        )
    if manifest_path.stat().st_size >= MAX_MANIFEST_BYTES:
        raise RuntimeError("Download manifest exceeds the GitHub 100 MiB file limit")
    manifest = expand_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    allowed_directories, stray_directories = _layout_directories(manifest.get("schema_version"))
    if manifest.get("schema_version") == 5 and (manifest.get("partitioning") or {}).get("grain") != "country_disease_window":
        raise RuntimeError("Schema 5 requires country_disease_window grain")
    if manifest.get("schema_version") != 5:
        allowed_directories = tuple(
            name for name in asset_directories if name in {"countries", "diseases"}
        ) or ("countries", "diseases")
    if manifest.get("formats") != ["csv", "json", "xlsx"]:
        raise RuntimeError("Download manifest must advertise CSV, JSON and XLSX")
    base = str(manifest.get("download_url_base") or "")
    if not base.startswith("https://raw.githubusercontent.com/") or not base.endswith(
        f"/{branch}"
    ):
        raise RuntimeError(
            f"Manifest Raw base must target branch {branch!r}; got {base!r}"
        )
    entries = list(manifest.get("countries") or []) + list(
        manifest.get("diseases") or []
    )
    if not entries:
        raise RuntimeError("Refusing to publish an empty download manifest")
    declared_paths: dict[Path, tuple[int, str]] = {}
    country_paths: set[Path] = set()
    disease_paths: set[Path] = set()
    country_entry_ids = {id(entry) for entry in manifest.get("countries") or []}
    for entry in entries:
        for part in entry.get("parts") or []:
            files = part.get("files") or {}
            for format_name in ("csv", "json", "xlsx"):
                file_meta = files.get(format_name) or {}
                relative = str(file_meta.get("relative_path") or "")
                public_url = str(file_meta.get("url") or "")
                asset_path = parse_asset_path(relative)
                if asset_path.parts[0] not in allowed_directories:
                    raise RuntimeError(f"Unsafe manifest asset path: {relative!r}")
                if manifest.get("schema_version") == 5:
                    country = str(part.get("country_code") or "").lower()
                    disease = str(part.get("disease_id") or "").lower()
                    expected = f"cells/{country}/{disease}/{part.get('id')}.{format_name}"
                    owner_matches = (str(entry.get("code") or "").lower() == country
                        if id(entry) in country_entry_ids
                        else str(entry.get("disease_id") or "").lower() == disease)
                    if not country or not disease or relative != expected or not owner_matches:
                        raise RuntimeError(f"Cell identity does not match manifest path: {relative}")
                    (country_paths if id(entry) in country_entry_ids else disease_paths).add(asset_path)
                identity = (int(file_meta.get("bytes") or -1), str(file_meta.get("sha256") or "").lower())
                previous = declared_paths.get(asset_path)
                if previous is not None and previous != identity:
                    raise RuntimeError(f"Manifest declares conflicting copies of {relative}")
                declared_paths[asset_path] = identity
                file_path = source_dir / asset_path
                if not file_path.is_file():
                    raise FileNotFoundError(
                        f"Missing {format_name.upper()} asset for "
                        f"{entry.get('id')}/{part.get('id')}: {file_path}"
                    )
                if public_url != f"{base}/{asset_path.as_posix()}":
                    raise RuntimeError(
                        f"Public URL does not match generated file: {public_url!r}"
                    )
                size = file_path.stat().st_size
                if size != int(file_meta.get("bytes") or -1):
                    raise RuntimeError(f"Manifest size mismatch: {file_path}")
                if size >= max_file_bytes:
                    raise RuntimeError(
                        f"GitHub rejects files of 100 MiB or more: {file_path} ({size} bytes)"
                    )
                digest = str(file_meta.get("sha256") or "")
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
                    raise RuntimeError(f"Manifest has an invalid SHA-256: {file_path}")

    if manifest.get("schema_version") == 5 and country_paths != disease_paths:
        raise RuntimeError("Country and disease catalogues must reference the same cell assets")
    actual_paths = {
        path.relative_to(source_dir)
        for directory in allowed_directories
        for path in (source_dir / directory).rglob("*")
        if path.is_file()
    }
    for directory in stray_directories:
        stray_root = source_dir / directory
        if stray_root.is_dir() and any(path.is_file() for path in stray_root.rglob("*")):
            raise RuntimeError(
                f"Download directory {directory} is not part of schema {manifest.get('schema_version')}"
            )
    unexpected_paths = sorted(actual_paths - set(declared_paths))
    if unexpected_paths:
        displayed = ", ".join(path.as_posix() for path in unexpected_paths[:10])
        suffix = "" if len(unexpected_paths) <= 10 else f" (+{len(unexpected_paths) - 10} more)"
        raise RuntimeError(
            "Generated download assets are not declared by manifest.json: "
            f"{displayed}{suffix}"
        )
    return manifest


def _manifest_assets(manifest_path: Path, *, asset_path=_asset_relative_path) -> dict[str, tuple[int, str]]:
    """Index declared asset sizes and hashes without reading the asset bytes."""
    if not manifest_path.is_file():
        return {}
    manifest = expand_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    indexed: dict[str, tuple[int, str]] = {}
    for entry in [*(manifest.get("countries") or []), *(manifest.get("diseases") or [])]:
        for part in entry.get("parts") or []:
            for file_meta in (part.get("files") or {}).values():
                relative = str(file_meta.get("relative_path") or "")
                path = asset_path(relative).as_posix()
                indexed[path] = (int(file_meta.get("bytes") or -1), str(file_meta.get("sha256") or ""))
    return indexed


def sync_managed_assets(source_dir: Path, workdir: Path, *, manifest_assets=_manifest_assets, compare_files=files_match, sha256_file=_sha256_file) -> dict[str, int]:
    """Copy changed assets using manifest hashes; avoid rereading multi-GB files."""

    copied = 0
    removed = 0
    expected: set[Path] = set()
    source_manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    target_manifest_path = workdir / "manifest.json"
    if target_manifest_path.exists():
        target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
        if target_manifest.get("schema_version") == 5 and source_manifest.get("schema_version") == 4:
            raise RuntimeError("Refusing to replace schema 5 cells with a frozen schema 4 candidate; regenerate the candidate")
    active_directories, inactive_directories = _layout_directories(source_manifest.get("schema_version"))
    source_assets = manifest_assets(source_dir / "manifest.json")
    target_assets = manifest_assets(workdir / "manifest.json")
    for directory_name in active_directories:
        source_root = source_dir / directory_name
        target_root = workdir / directory_name
        if not source_root.is_dir():
            raise FileNotFoundError(f"Expected generated directory missing: {source_root}")
        for source_path in source_root.rglob("*"):
            if not source_path.is_file():
                continue
            relative = source_path.relative_to(source_dir)
            target_path = workdir / relative
            expected.add(target_path)
            relative_key = relative.as_posix()
            source_meta = source_assets.get(relative_key)
            target_meta = target_assets.get(relative_key)
            if source_meta is None:
                raise RuntimeError(f"Asset is missing from generated manifest: {relative_key}")
            if (
                target_path.is_file()
                and source_meta is not None
                and target_meta == source_meta
                and target_path.stat().st_size == source_meta[0]
            ):
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target_path.name}.", dir=target_path.parent)
            os.close(fd)
            temporary_path = Path(temporary_name)
            try:
                shutil.copy2(source_path, temporary_path)
                if sha256_file(temporary_path) != source_meta[1]:
                    raise RuntimeError(f"Manifest checksum mismatch: {source_path}")
                os.replace(temporary_path, target_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            copied += 1
        if target_root.exists():
            for target_path in sorted(target_root.rglob("*"), reverse=True):
                if target_path.is_file() and target_path not in expected:
                    target_path.unlink()
                    removed += 1
                elif target_path.is_dir() and not any(target_path.iterdir()):
                    target_path.rmdir()
    for directory_name in inactive_directories:
        target_root = workdir / directory_name
        if not target_root.exists():
            continue
        for target_path in sorted(target_root.rglob("*"), reverse=True):
            if target_path.is_file():
                target_path.unlink()
                removed += 1
            elif target_path.is_dir() and not any(target_path.iterdir()):
                target_path.rmdir()
        if target_root.is_dir() and not any(target_root.iterdir()):
            target_root.rmdir()

    source_manifest = source_dir / "manifest.json"
    target_manifest = workdir / "manifest.json"
    if not target_manifest.exists() or not compare_files(source_manifest, target_manifest):
        shutil.copy2(source_manifest, target_manifest)
        copied += 1
    return {"copied": copied, "removed": removed}


def write_readme(workdir: Path, manifest: dict) -> None:
    generated_at = manifest.get("generated_at") or "unspecified"
    countries = len(manifest.get("countries") or [])
    diseases = len(manifest.get("diseases") or [])
    base = manifest.get("download_url_base") or ""
    grain = (manifest.get("partitioning") or {}).get("grain")
    partitioning = (
        "one file per country, disease and calendar window; unchanged cells are not rewritten"
        if grain == "country_disease_window"
        else "stable calendar windows; only changed windows are committed"
    )
    readme = f"""# GlobalID Data Downloads

This repository stores directly downloadable datasets for the GlobalID public site.

- Generated at: `{generated_at}`
- Country datasets: `{countries}`
- Disease datasets: `{diseases}`
- Formats: CSV, JSON and XLSX
- Partitioning: {partitioning}
- Manifest: [`manifest.json`](./manifest.json)

Public files are stable branch links under:

`{base}`
"""
    if grain == "country_disease_window":
        readme += """
## Manifest encoding

Schema 5 uses `cell_metadata: country_parts_v1`. Country `parts` own the cell
metadata. Disease `parts` reference it by `country_code` and window `id`,
using the disease entry's `disease_id`. Each format declares its size and SHA-256.
Default paths are `cells/<lowercase-country>/<lowercase-disease>/<window>.<format>`;
prepend `download_url_base` for the public URL. The default download filename is
`globalid-<lowercase-country>-<lowercase-disease>-<window>.<format>`.
The cell content hash defaults to its JSON file's SHA-256. Explicit fields override
these defaults. This stores shared cell metadata once and keeps the manifest below
GitHub's 100 MiB per-file limit.
"""
    path = workdir / "README.md"
    if not path.exists() or path.read_text(encoding="utf-8") != readme:
        # Export backups may hard-link old artifacts. Replace, never truncate.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(readme, encoding="utf-8")
        os.replace(temporary, path)
