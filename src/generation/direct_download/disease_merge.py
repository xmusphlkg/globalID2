"""Retain validated published disease rows outside the selected countries."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .rows import _sort_rows


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_disease_baseline_json(output_dir: Path, relative: str, file: dict) -> bytes:
    """Recover an interrupted in-place write only from matching local HEAD bytes."""
    def matches(body: bytes) -> bool:
        return len(body) == file.get("bytes") and _sha256(body) == file.get("sha256")

    try:
        body = (output_dir / relative).read_bytes()
        if matches(body):
            return body
    except FileNotFoundError:
        pass
    if (output_dir / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{relative}"], cwd=output_dir,
                check=True, capture_output=True, timeout=60,
            )
            if matches(result.stdout):
                return result.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    raise ValueError("baseline JSON does not match its manifest checksum in the worktree or local Git HEAD")


def _merge_existing_disease_rows(
    disease_data: dict,
    fresh_rows: list[dict],
    *,
    changed_country_codes: set[str],
    country_names: dict[str, str],
    output_dir: Path,
    existing_parts: dict[str, dict],
) -> tuple[list[dict], int]:
    """Replace selected countries while retaining every existing unselected row.

    Old public-only rows are retained as historical published content, never
    reconstructed or relabelled as recovered source observations. Each JSON
    body must match its manifest before any retained row is used.
    """
    disease_id = disease_data["disease_id"]
    retained = []
    legacy_count = 0
    for part in existing_parts.values():
        file = (part.get("files") or {}).get("json") or {}
        relative = str(file.get("relative_path") or "")
        expected = f"diseases/{str(disease_id).lower()}/{part.get('id')}.json"
        if relative != expected or ".." in Path(relative).parts:
            raise ValueError(f"Disease {disease_id}: invalid baseline partition path {relative!r}")
        try:
            body = _read_disease_baseline_json(output_dir, relative, file)
            payload = json.loads(body)
            del body
            metadata = payload["metadata"]
            if metadata.get("dataset_kind") != "disease" or metadata.get("dataset_id") != disease_id:
                raise ValueError("baseline JSON belongs to another dataset")
            records = payload["records"]
            if len(records) != metadata.get("record_count"):
                raise ValueError("baseline JSON record count is inconsistent")
            for row in records:
                code = row.get("country_code")
                if code in changed_country_codes or code not in country_names:
                    continue
                if row.get("disease_id") != disease_id or row.get("record_kind") not in {"public_projection", "source_series_observation"}:
                    raise ValueError(f"invalid retained row for country {code}")
                # Preserve the published per-source fields. Only independently
                # supplied display metadata is allowed to refresh these rows.
                row.update({
                    "dataset_slug": disease_data.get("slug"),
                    "dataset_name": disease_data.get("name_en"),
                    "disease_name_en": disease_data.get("name_en"),
                    "disease_name_zh": disease_data.get("name_zh"),
                    "category": disease_data.get("category"),
                    "country_name": country_names[code],
                })
                retained.append(row)
                legacy_count += int(row.get("record_kind") == "public_projection" and row.get("projection_policy") == "published_series")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Disease {disease_id}, partition {part.get('id')}: {error}") from error
    return _sort_rows([*retained, *fresh_rows]), legacy_count
