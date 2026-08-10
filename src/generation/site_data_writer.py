"""Filesystem boundary helpers for the static site data export.

The export orchestrator deliberately retains control of write ordering and
progress reporting.  This module owns only directory lifecycle and the exact
JSON encodings used by the build-time and browser-facing artifacts.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def clean_generated_dir(dir_path: Path) -> None:
    """Remove stale generated CSV/JSON files before rewriting."""

    if not dir_path.exists():
        return
    for pattern in ("*.json", "*.csv"):
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                file_path.unlink()


def reset_public_data_dir(dir_path: Path) -> None:
    """Replace generated public data files while preserving other assets."""

    if dir_path.exists():
        shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)


def existing_site_export_has_content(output_dir: Path) -> bool:
    """Return whether an on-disk site export contains usable report data."""

    meta_path = output_dir / "meta.json"
    if not meta_path.exists():
        return False

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if int(meta.get("total_reports") or 0) > 0:
        return True

    for country in meta.get("countries") or []:
        if int(country.get("disease_count") or 0) > 0:
            return True
        if int(country.get("total_cases") or 0) > 0:
            return True
    return False


def prepare_site_output_dirs(output_dir: Path, public_site_data_dir: Path) -> None:
    """Prepare output trees without discarding reusable generated files.

    Full directory resets made every release rewrite all JSON, even when a
    country or disease had not changed.  Call ``remove_stale_json_files`` after
    successful writes to reconcile deletions safely.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "countries").mkdir(exist_ok=True)
    (output_dir / "diseases").mkdir(exist_ok=True)
    (output_dir / "disease-knowledge").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)
    (output_dir / "situation" / "archive").mkdir(parents=True, exist_ok=True)
    (public_site_data_dir / "countries").mkdir(parents=True, exist_ok=True)
    (public_site_data_dir / "diseases").mkdir(parents=True, exist_ok=True)
    (public_site_data_dir / "situation" / "archive").mkdir(parents=True, exist_ok=True)


def remove_stale_json_files(dir_path: Path, expected_names: set[str]) -> int:
    """Remove only obsolete top-level JSON artifacts after a successful export."""

    removed = 0
    if not dir_path.exists():
        return removed
    for path in dir_path.glob("*.json"):
        if path.name not in expected_names:
            path.unlink()
            removed += 1
    return removed


def _write_if_changed(path: Path, content: bytes) -> bool:
    """Atomically replace a file only when its bytes have changed."""

    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def write_pretty_json(path: Path, payload: Any) -> bool:
    """Write build-time JSON using the historical human-readable encoding."""

    return _write_if_changed(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def write_compact_json(path: Path, payload: Any) -> bool:
    """Write browser-facing JSON using the historical compact encoding."""

    return _write_if_changed(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


__all__ = [
    "clean_generated_dir",
    "existing_site_export_has_content",
    "remove_stale_json_files",
    "prepare_site_output_dirs",
    "reset_public_data_dir",
    "write_compact_json",
    "write_pretty_json",
]
