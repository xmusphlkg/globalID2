"""GlobalID-specific normalization for the canonical download package v2."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from src.generation.sharded_data_package import (
    DEFAULT_MAX_UNCOMPRESSED_BYTES,
    PackageBuildError,
    build_canonical_facts_release,
)


GLOBALID_DOWNLOAD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://global.health/schemas/surveillance-fact-v2.json",
    "title": "GlobalID canonical surveillance fact",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "country_code",
        "disease_id",
        "date",
        "cases",
        "weekly_equiv_cases",
        "deaths",
        "incidence_rate_per_100k",
        "incidence_rate_source",
        "mortality_rate",
        "data_layer",
        "projection_policy",
        "series_codes",
        "loss_risk",
        "coverage_status",
        "legacy_gap_fill_count",
        "coverage_ratio_against_legacy",
        "primary_source_ref",
        "source_refs",
    ],
    "properties": {
        "country_code": {"type": "string", "minLength": 2},
        "disease_id": {"type": "string", "minLength": 1},
        "date": {"type": "string", "format": "date"},
        "cases": {"type": ["integer", "number", "null"]},
        "weekly_equiv_cases": {"type": ["integer", "number", "null"]},
        "deaths": {"type": ["integer", "number", "null"]},
        "incidence_rate_per_100k": {"type": ["number", "null"]},
        "incidence_rate_source": {"type": ["string", "null"]},
        "mortality_rate": {"type": ["number", "null"]},
        "data_layer": {"type": ["string", "null"]},
        "projection_policy": {"type": ["string", "null"]},
        "series_codes": {"type": "array", "items": {"type": "string"}},
        "loss_risk": {"type": ["string", "null"]},
        "coverage_status": {"type": ["string", "null"]},
        "legacy_gap_fill_count": {"type": ["integer", "null"]},
        "coverage_ratio_against_legacy": {"type": ["number", "null"]},
        "primary_source_ref": {"type": ["string", "null"]},
        "source_refs": {"type": "array", "items": {"type": "string"}},
    },
}

GLOBALID_DOWNLOAD_DATASET: dict[str, Any] = {
    "id": "globalid-surveillance-facts",
    "title": "GlobalID canonical infectious-disease surveillance facts",
    "contract": "globalid.downloads.canonical-facts.v2",
    "path_semantics": {
        "package_paths": "package-root-relative-posix",
        "remote_release": "immutable-release-prefix",
    },
}

_RELEASE_ID_SAFE = re.compile(r"[^0-9A-Za-z._-]+")


def _snapshot_url(base_url: str, *parts: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise PackageBuildError("snapshot_url_base must be a non-empty URL")
    clean_parts = [str(part).strip("/") for part in parts if str(part).strip("/")]
    return "/".join([base, *clean_parts])


def _entry_map(
    entries: Iterable[Mapping[str, Any]],
    *,
    key_field: str,
) -> dict[str, Mapping[str, Any]]:
    mapped: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        key = _required_text(entry, key_field).upper()
        if key in mapped:
            raise PackageBuildError(f"Duplicate frontend metadata for {key}")
        mapped[key] = entry
    return mapped


def build_frontend_download_manifest(
    package_manifest: Mapping[str, Any],
    *,
    snapshot_url_base: str,
    country_entries: Iterable[Mapping[str, Any]],
    disease_entries: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the small browser catalogue for immutable v2 dataset indexes."""

    release = package_manifest.get("release")
    indexes = package_manifest.get("indexes")
    totals = package_manifest.get("totals")
    if not isinstance(release, Mapping) or not isinstance(indexes, Mapping):
        raise PackageBuildError("Canonical package manifest lacks release/indexes")
    if not isinstance(totals, Mapping):
        raise PackageBuildError("Canonical package manifest lacks totals")
    release_id = _required_text(release, "release_id")
    generated_at = _required_text(release, "generated_at")
    release_base = _snapshot_url(
        snapshot_url_base,
        "releases",
        release_id,
    )

    countries_by_code = _entry_map(country_entries, key_field="code")
    diseases_by_id = _entry_map(disease_entries, key_field="disease_id")

    def descriptor_map(kind: str, key_field: str) -> dict[str, Mapping[str, Any]]:
        descriptors = indexes.get(kind)
        if not isinstance(descriptors, list):
            raise PackageBuildError(f"Canonical package lacks {kind} indexes")
        mapped: dict[str, Mapping[str, Any]] = {}
        for descriptor in descriptors:
            if not isinstance(descriptor, Mapping):
                raise PackageBuildError(f"Invalid {kind} index descriptor")
            key = _required_text(descriptor, key_field).upper()
            if key in mapped:
                raise PackageBuildError(f"Duplicate {kind} index for {key}")
            mapped[key] = descriptor
        return mapped

    country_indexes = descriptor_map("countries", "country_code")
    disease_indexes = descriptor_map("diseases", "disease_id")
    if set(country_indexes) != set(countries_by_code):
        raise PackageBuildError("Country metadata and v2 indexes do not match")
    if set(disease_indexes) != set(diseases_by_id):
        raise PackageBuildError("Disease metadata and v2 indexes do not match")

    countries: list[dict[str, Any]] = []
    for code, entry in countries_by_code.items():
        descriptor = country_indexes[code]
        countries.append(
            {
                "kind": "country",
                "id": str(entry.get("id") or code.lower()),
                "code": code,
                "name": entry.get("name") or entry.get("name_en"),
                "name_en": entry.get("name_en") or entry.get("name"),
                "name_zh": entry.get("name_zh"),
                "generated_at": generated_at,
                "record_count": descriptor.get("record_count"),
                "date_range": {
                    "start": descriptor.get("date_start"),
                    "end": descriptor.get("date_end"),
                },
                "dataset_index_path": _snapshot_url(
                    release_base, _required_text(descriptor, "path")
                ),
                "site_json_path": entry.get("site_json_path"),
            }
        )

    diseases: list[dict[str, Any]] = []
    for disease_id, entry in diseases_by_id.items():
        descriptor = disease_indexes[disease_id]
        diseases.append(
            {
                "kind": "disease",
                "id": str(entry.get("id") or disease_id.lower()),
                "disease_id": disease_id,
                "slug": entry.get("slug"),
                "name_en": entry.get("name_en"),
                "name_zh": entry.get("name_zh"),
                "generated_at": generated_at,
                "record_count": descriptor.get("record_count"),
                "country_count": entry.get("country_count"),
                "date_range": {
                    "start": descriptor.get("date_start"),
                    "end": descriptor.get("date_end"),
                },
                "dataset_index_path": _snapshot_url(
                    release_base, _required_text(descriptor, "path")
                ),
                "site_json_path": entry.get("site_json_path"),
            }
        )

    return {
        "manifest_version": 2,
        "protocol": "globalid.github-snapshot.v2",
        "generated_at": generated_at,
        "release_id": release_id,
        "latest_path": _snapshot_url(snapshot_url_base, "latest.json"),
        "release_manifest_path": _snapshot_url(release_base, "manifest.json"),
        "record_count": totals.get("record_count"),
        "shard_count": totals.get("shard_count"),
        "format": package_manifest.get("format"),
        "countries": countries,
        "diseases": diseases,
    }


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PackageBuildError(f"Canonical fact requires non-empty {field!r}")
    return value.strip()


def _stable_text_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # v1 uses pipes for series codes and semicolon-space for source fields.
        raw_values = re.split(r"[|;]", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = value
    else:
        raise PackageBuildError(
            "Pipe-delimited or array metadata must contain only text values"
        )

    normalized: set[str] = set()
    for item in raw_values:
        if not isinstance(item, str):
            raise PackageBuildError("Metadata arrays must contain only strings")
        rendered = item.strip()
        if rendered:
            normalized.add(rendered)
    return sorted(normalized)


def source_reference(country_code: str, scope: str) -> str:
    """Return a stable, human-readable source identifier."""

    country = country_code.strip().upper()
    normalized_scope = scope.strip()
    if not country or not normalized_scope:
        raise PackageBuildError("Source references require country code and scope")
    return f"{country}:{normalized_scope}"


def canonicalize_country_download_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Remove v1 dataset/display duplication from one country download row."""

    if not isinstance(row, Mapping):
        raise PackageBuildError("Country download row must be a mapping")
    country_code = _required_text(row, "country_code").upper()
    disease_id = _required_text(row, "disease_id").upper()
    report_date = _required_text(row, "date")

    scopes = _stable_text_values(row.get("source_scopes"))
    primary_scope = row.get("primary_source_scope")
    if primary_scope is not None and (
        not isinstance(primary_scope, str) or not primary_scope.strip()
    ):
        raise PackageBuildError("primary_source_scope must be text or null")
    primary_ref = (
        source_reference(country_code, primary_scope)
        if isinstance(primary_scope, str) and primary_scope.strip()
        else None
    )
    source_refs = {source_reference(country_code, scope) for scope in scopes}
    if primary_ref:
        source_refs.add(primary_ref)

    return {
        "country_code": country_code,
        "disease_id": disease_id,
        "date": report_date,
        "cases": row.get("cases"),
        "weekly_equiv_cases": row.get("weekly_equiv_cases"),
        "deaths": row.get("deaths"),
        "incidence_rate_per_100k": row.get("incidence_rate_per_100k"),
        "incidence_rate_source": row.get("incidence_rate_source"),
        "mortality_rate": row.get("mortality_rate"),
        "data_layer": row.get("data_layer"),
        "projection_policy": row.get("projection_policy"),
        "series_codes": _stable_text_values(row.get("series_codes")),
        "loss_risk": row.get("loss_risk"),
        "coverage_status": row.get("coverage_status"),
        "legacy_gap_fill_count": row.get("legacy_gap_fill_count"),
        "coverage_ratio_against_legacy": row.get(
            "coverage_ratio_against_legacy"
        ),
        "primary_source_ref": primary_ref,
        "source_refs": sorted(source_refs),
    }


def canonicalize_country_download_rows(
    rows: Iterable[Mapping[str, Any]],
) -> Iterator[dict[str, Any]]:
    for row in rows:
        yield canonicalize_country_download_row(row)


def _source_entry(
    country_code: str,
    source: Mapping[str, Any],
    *,
    primary_scope: str | None,
    parser_primary: object = None,
) -> tuple[str, dict[str, Any]]:
    scope = _required_text(source, "scope")
    reference = source_reference(country_code, scope)
    return reference, {
        "source_ref": reference,
        "country_code": country_code,
        "scope": scope,
        "label": source.get("label"),
        "url": source.get("url"),
        "machine_url": source.get("machine_url"),
        "type": source.get("type"),
        "cadence": source.get("cadence"),
        "description": source.get("description"),
        "parser": parser_primary if scope == primary_scope else None,
        "is_primary": scope == primary_scope,
    }


def build_source_catalog(
    source_info_by_country: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Deduplicate expanded v1 source metadata into stable source references."""

    catalog: dict[str, dict[str, Any]] = {}
    for raw_country_code in sorted(source_info_by_country):
        country_code = raw_country_code.strip().upper()
        info = source_info_by_country[raw_country_code]
        if not isinstance(info, Mapping):
            raise PackageBuildError(
                f"Source information for {country_code} must be a mapping"
            )
        raw_primary_scope = info.get("primary_scope")
        primary_scope = (
            raw_primary_scope.strip()
            if isinstance(raw_primary_scope, str) and raw_primary_scope.strip()
            else None
        )
        sources = info.get("sources") or []
        if not isinstance(sources, list):
            raise PackageBuildError(
                f"Source information for {country_code} must contain a sources list"
            )
        for source in sources:
            if not isinstance(source, Mapping):
                raise PackageBuildError(
                    f"Source entries for {country_code} must be mappings"
                )
            reference, entry = _source_entry(
                country_code,
                source,
                primary_scope=primary_scope,
                parser_primary=info.get("parser_primary"),
            )
            existing = catalog.get(reference)
            if existing is not None and existing != entry:
                raise PackageBuildError(
                    f"Conflicting source catalog entries for {reference}"
                )
            catalog[reference] = entry

        if primary_scope:
            primary_ref = source_reference(country_code, primary_scope)
            if primary_ref not in catalog:
                fallback_source = {
                    "scope": primary_scope,
                    "label": info.get("primary_label"),
                    "url": info.get("primary_url"),
                    "type": info.get("primary_type"),
                }
                reference, entry = _source_entry(
                    country_code,
                    fallback_source,
                    primary_scope=primary_scope,
                    parser_primary=info.get("parser_primary"),
                )
                catalog[reference] = entry
    return dict(sorted(catalog.items()))


def _date_range(entry: Mapping[str, Any]) -> dict[str, Any]:
    value = entry.get("date_range") or {}
    if not isinstance(value, Mapping):
        raise PackageBuildError("Download entry date_range must be a mapping")
    return {"start": value.get("start"), "end": value.get("end")}


def build_country_metadata(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for entry in entries:
        code = _required_text(entry, "code").upper()
        if code in catalog:
            raise PackageBuildError(f"Duplicate country metadata for {code}")
        catalog[code] = {
            "code": code,
            "id": str(entry.get("id") or code.lower()),
            "name_en": entry.get("name_en") or entry.get("name"),
            "name_zh": entry.get("name_zh"),
            "record_count": entry.get("record_count"),
            "date_range": _date_range(entry),
            "site_json_path": entry.get("site_json_path"),
        }
    return dict(sorted(catalog.items()))


def build_disease_metadata(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for entry in entries:
        disease_id = _required_text(entry, "disease_id").upper()
        if disease_id in catalog:
            raise PackageBuildError(
                f"Duplicate disease metadata for {disease_id}"
            )
        catalog[disease_id] = {
            "disease_id": disease_id,
            "id": str(entry.get("id") or disease_id.lower()),
            "slug": entry.get("slug"),
            "name_en": entry.get("name_en"),
            "name_zh": entry.get("name_zh"),
            "record_count": entry.get("record_count"),
            "country_count": entry.get("country_count"),
            "site_json_path": entry.get("site_json_path"),
        }
    return dict(sorted(catalog.items()))


def release_id_from_generated_at(generated_at: str) -> str:
    """Create a path-safe release identifier from a snapshot timestamp."""

    if not isinstance(generated_at, str) or not generated_at.strip():
        raise PackageBuildError("generated_at is required for a v2 release")
    raw = generated_at.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        safe = _RELEASE_ID_SAFE.sub("-", raw).strip("-._")
        if not safe:
            raise PackageBuildError("generated_at cannot form a safe release id")
        return safe
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def build_globalid_download_package(
    country_rows: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    generated_at: str,
    country_entries: Iterable[Mapping[str, Any]],
    disease_entries: Iterable[Mapping[str, Any]],
    source_info_by_country: Mapping[str, Mapping[str, Any]],
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    """One-time v1 migration adapter that builds a validated v2 package."""

    source_catalog = build_source_catalog(source_info_by_country)

    def normalized_facts() -> Iterator[dict[str, Any]]:
        for row in country_rows:
            fact = canonicalize_country_download_row(row)
            unknown_refs = set(fact["source_refs"]) - set(source_catalog)
            if unknown_refs:
                raise PackageBuildError(
                    "Canonical fact references unknown source(s): "
                    + ", ".join(sorted(unknown_refs))
                )
            yield fact

    return _build_globalid_canonical_release(
        normalized_facts(),
        output_dir,
        generated_at=generated_at,
        country_entries=country_entries,
        disease_entries=disease_entries,
        source_catalog=source_catalog,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def build_globalid_canonical_download_package(
    facts: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    generated_at: str,
    country_entries: Iterable[Mapping[str, Any]],
    disease_entries: Iterable[Mapping[str, Any]],
    source_info_by_country: Mapping[str, Mapping[str, Any]],
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    """Build production v2 directly from canonical facts without a v1 row shape."""

    source_catalog = build_source_catalog(source_info_by_country)

    def validated_facts() -> Iterator[dict[str, Any]]:
        for raw_fact in facts:
            if not isinstance(raw_fact, Mapping):
                raise PackageBuildError("Canonical fact must be a mapping")
            fact = dict(raw_fact)
            refs = fact.get("source_refs")
            if not isinstance(refs, list) or not all(
                isinstance(ref, str) for ref in refs
            ):
                raise PackageBuildError("Canonical source_refs must be a string list")
            unknown_refs = set(refs) - set(source_catalog)
            if unknown_refs:
                raise PackageBuildError(
                    "Canonical fact references unknown source(s): "
                    + ", ".join(sorted(unknown_refs))
                )
            yield fact

    return _build_globalid_canonical_release(
        validated_facts(),
        output_dir,
        generated_at=generated_at,
        country_entries=country_entries,
        disease_entries=disease_entries,
        source_catalog=source_catalog,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def _build_globalid_canonical_release(
    facts: Iterable[Mapping[str, Any]],
    output_dir: Path,
    *,
    generated_at: str,
    country_entries: Iterable[Mapping[str, Any]],
    disease_entries: Iterable[Mapping[str, Any]],
    source_catalog: Mapping[str, Any],
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    return build_canonical_facts_release(
        facts,
        output_dir,
        date_field="date",
        schema=GLOBALID_DOWNLOAD_SCHEMA,
        release={
            "release_id_prefix": release_id_from_generated_at(generated_at),
            "generated_at": generated_at,
            "stage": "production",
            "immutable": True,
        },
        dataset=GLOBALID_DOWNLOAD_DATASET,
        country_metadata=build_country_metadata(country_entries),
        disease_metadata=build_disease_metadata(disease_entries),
        source_catalog=source_catalog,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


__all__ = [
    "GLOBALID_DOWNLOAD_DATASET",
    "GLOBALID_DOWNLOAD_SCHEMA",
    "build_country_metadata",
    "build_disease_metadata",
    "build_frontend_download_manifest",
    "build_globalid_canonical_download_package",
    "build_globalid_download_package",
    "build_source_catalog",
    "canonicalize_country_download_row",
    "canonicalize_country_download_rows",
    "release_id_from_generated_at",
    "source_reference",
]
