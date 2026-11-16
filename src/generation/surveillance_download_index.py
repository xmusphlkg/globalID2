"""Small, versioned download-directory input from an existing download manifest.

Schema 1 retains the manifest's own generated_at and surveillance_release_id.
Each entry keeps totals across all periods and the true parts_count, while
parts contains at most one original current part (or the last available part).
Country classification comes from the same export context as surveillance meta.
Full period selectors remain in each country/disease summary.download payload.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Iterable


ENTRY_FIELDS = (
    "kind", "id", "code", "disease_id", "slug", "name", "name_en", "name_zh", "name_fr",
    "aliases", "generated_at", "record_count", "projection_record_count",
    "source_observation_count", "source_series_count", "includes_series_provenance",
    "date_range", "country_count", "site_json_path", "location_type", "parent_code",
)
PART_FIELDS = (
    "id", "label", "is_current", "window", "date_range", "period_start", "period_end",
    "record_count", "projection_record_count", "source_observation_count", "source_series_count",
)
FILE_FIELDS = ("url", "filename", "bytes", "sha256", "relative_path")


def _fields(value: dict, names: tuple[str, ...]) -> dict:
    return {name: deepcopy(value[name]) for name in names if name in value}


def _entry(entry: dict, country: dict | None = None) -> dict:
    result = _fields(entry, ENTRY_FIELDS)
    if country is not None:
        result.update(_fields(country, ("location_type", "parent_code")))
    parts = entry.get("parts") or []
    current = next((part for part in parts if part.get("is_current")), parts[-1] if parts else None)
    result["parts_count"] = len(parts)
    result["parts"] = []
    if current is not None:
        part = _fields(current, PART_FIELDS)
        part["files"] = {
            format_name: _fields(metadata, FILE_FIELDS)
            for format_name, metadata in (current.get("files") or {}).items()
            if format_name in {"csv", "json", "xlsx"}
        }
        result["parts"].append(part)
    return result


def build_download_index(downloads: dict | None, *, countries: Iterable[dict] = ()) -> dict:
    """Project download metadata without reading assets, querying DB or relabelling dates."""
    downloads = downloads or {}
    metadata = {str(country["code"]).upper(): country for country in countries}
    return {
        "schema_version": 1,
        "generated_at": downloads.get("generated_at"),
        "surveillance_release_id": downloads.get("surveillance_release_id"),
        "formats": list(downloads.get("formats") or ["csv", "json", "xlsx"]),
        "countries": [_entry(entry, metadata.get(str(entry.get("code", "")).upper()))
                      for entry in downloads.get("countries") or []],
        "diseases": [_entry(entry) for entry in downloads.get("diseases") or []],
    }


def refresh_summary_download(summary: dict, download: dict | None) -> dict:
    """Replace only the archive snapshot, preserving unchanged curve facts and date.

    Used for unchanged_curve branches: summary.generated_at belongs to its
    surveillance facts; download.generated_at belongs to the archive and may
    differ. Copying prevents edits to a frozen input through shared references.
    """
    return {**summary, "download": deepcopy(download)}
