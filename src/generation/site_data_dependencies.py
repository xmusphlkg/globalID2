"""Completed country-to-disease projection dependencies for incremental exports.

The receipt is promoted only after all curve artifacts succeed. It cannot be
replaced by comparing two country files: an interrupted writer may have already
replaced its country file while its disease files still describe an older run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .site_data_writer import write_pretty_json


RECEIPT_NAME = "curve-dependencies.json"


def _digest(value: object) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def dependency_version() -> str:
    digest = hashlib.sha256(b"country-disease-dependencies-v1")
    root = Path(__file__).resolve().parents[2]
    # The completed baseline is only reusable with the exact projection rules
    # that produced it, including helpers outside this package. Otherwise an
    # unchanged disease could silently retain obsolete source/period selection.
    for name in (
        "generation/site_data_dependencies.py", "generation/site_data_export.py",
        "generation/site_data_views.py", "generation/site_data_queries.py",
        "generation/site_series_projection.py", "generation/site_data_catalogue.py",
        "generation/site_data_about.py", "generation/country_projection_inputs.py",
        "generation/country_projection_workers.py", "core/reporting_period.py",
        "core/disease_cutover.py", "core/country_library.py",
        "services/disease_series_policy.py",
    ):
        digest.update((root / "src" / name).read_bytes())
    for name in ("standard_diseases.csv", "disease_ontology.json", "disease_cutover.json", "country_bootstrap.json"):
        path = root / "configs" / name
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def file_identity(path: Path) -> list[int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def country_inputs(data: dict, *, name: str, name_zh: str | None, source_info: dict) -> dict:
    """Hash every series field, including provenance and non-public measures."""
    return {
        "metadata": _digest({"name": name, "name_zh": name_zh, "source_info": source_info}),
        "series": {str(did): _digest(series) for did, series in (data.get("disease_series") or {}).items()},
    }


def load_receipt(output_dir: Path) -> dict:
    try:
        value = json.loads((output_dir / RECEIPT_NAME).read_text(encoding="utf-8"))
        if value.get("schema_version") == 1 and value.get("version") == dependency_version() and isinstance(value.get("countries"), dict) and isinstance(value.get("diseases"), dict):
            return value
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return {"countries": {}, "diseases": {}}


def changed_diseases(code: str, current: dict, receipt: dict, output_dir: Path, public_dir: Path) -> set[str] | None:
    previous = receipt["countries"].get(code)
    if not isinstance(previous, dict) or not isinstance(previous.get("series"), dict):
        return None
    candidates = set(previous["series"]) | set(current["series"])
    if previous.get("metadata") != current["metadata"]:
        return candidates
    changed = {did for did in candidates if previous["series"].get(did) != current["series"].get(did)}
    for did in candidates - changed:
        identities = receipt["diseases"].get(did)
        if not isinstance(identities, dict) or identities != {
            "full": file_identity(output_dir / "diseases" / f"{did.lower()}.json"),
            "public": file_identity(public_dir / "diseases" / f"{did.lower()}.json"),
        } or any(value is None for value in identities.values()):
            changed.add(did)
    return changed


def save_receipt(context: dict, output_dir: Path, public_dir: Path) -> None:
    """Promote completed projection dependencies after all curve writes."""
    previous = load_receipt(output_dir)
    countries = {code: value for code, value in previous["countries"].items() if code not in set(context.get("retired_country_codes") or ())}
    countries.update(context.get("country_dependency_inputs") or {})
    diseases = {
        item["disease_id"]: {
            "full": file_identity(output_dir / "diseases" / f"{item['disease_id'].lower()}.json"),
            "public": file_identity(public_dir / "diseases" / f"{item['disease_id'].lower()}.json"),
        }
        for item in context["disease_exports"]
    }
    write_pretty_json(output_dir / RECEIPT_NAME, {"schema_version": 1, "version": dependency_version(), "countries": countries, "diseases": diseases})
