"""Freeze the curves used by a release for its post-verification DB commit.

These private build inputs are never copied into the public site. Reading them
avoids querying and projecting every observation a second time after deployment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.generation.site_data_writer import _write_if_changed, remove_stale_json_files

SERIES_FIELDS = (
    "dates", "cases", "deaths", "incidence_rates", "period_granularity",
    "data_layer", "selected_series_codes", "point_series_codes",
)


def write_projection_snapshot(context: dict, directory: Path, high_water: dict) -> None:
    from src.generation.public_series_store import PROJECTION_VERSION

    selected = (
        set(context["incremental_country_codes"])
        if context.get("incremental") else None
    )
    entries = {}
    for country in context["country_exports"]:
        code = country["code"]
        if selected is not None and code not in selected:
            continue
        if country.get("unchanged_curve"):
            raise ValueError(f"Release projection requires a fresh curve for {code}")
        watermark = high_water.get(code)
        payload = {
            "country_code": code,
            "source_high_water": watermark.isoformat() if watermark else None,
            "disease_series": {
                disease_id: {key: series[key] for key in SERIES_FIELDS if key in series}
                for disease_id, series in (country["country_data"].get("disease_series") or {}).items()
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        name = f"{code.lower()}.json"
        _write_if_changed(directory / name, body)
        entries[code] = {"file": name, "sha256": hashlib.sha256(body).hexdigest()}
    manifest = {"schema": 1, "projection_version": PROJECTION_VERSION, "countries": entries}
    _write_if_changed(directory / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
    remove_stale_json_files(directory, {"manifest.json", *(item["file"] for item in entries.values())})


def load_projection_manifest(directory: Path) -> dict:
    from src.generation.public_series_store import PROJECTION_VERSION

    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("projection_version") != PROJECTION_VERSION:
        raise ValueError("Unsupported prepared release projection")
    if not isinstance(manifest.get("countries"), dict):
        raise ValueError("Prepared release projection has no country map")
    return manifest


def read_projection_country(directory: Path, manifest: dict, code: str) -> dict:
    entry = manifest["countries"][code]
    name = f"{code.lower()}.json"
    if entry.get("file") != name or Path(name).name != name:
        raise ValueError(f"Invalid prepared projection path for {code}")
    body = (directory / name).read_bytes()
    if hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise ValueError(f"Prepared release projection checksum mismatch: {code}")
    payload = json.loads(body)
    if payload.get("country_code") != code:
        raise ValueError(f"Prepared release projection country mismatch: {code}")
    return payload
