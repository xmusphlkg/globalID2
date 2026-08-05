#!/usr/bin/env python3
"""Backfill lossless US-resident NNDSS source-series observations safely.

The CDC NNDSS weekly table publishes several statistical scopes.  Only
``US RESIDENTS`` / ``U.S. Residents`` represents the national scope used by
GlobalID.  The broader source ``TOTAL`` also includes U.S. territories and
non-U.S. residents and is deliberately rejected by this command.

The command is database-aware but read-only by default.  ``--apply`` requires
explicit input files and an inclusive date range.  Existing facts are never
updated: an exactly identical resident fact is a no-op, while any differing
content or non-resident provenance blocks the entire batch.  Every fact that
is actually inserted receives an exact after-image in
``disease_migration_audit`` in the same transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.core.disease_mutation_lock import (  # noqa: E402
    acquire_disease_data_mutation_lock,
)
from src.data.processors.mapping_lookup import normalize_mapping_key  # noqa: E402
from src.data.storage import SeriesObservationStore  # noqa: E402
from src.domain import DiseaseSeriesObservation  # noqa: E402
from src.ontology import load_disease_ontology  # noqa: E402

SOURCE_ID = "SRC_US_NNDSS"
COUNTRY_CODE = "US"
NATIONAL_GEOGRAPHY = "country:US:national"
DIMENSION_KEY = "all"
POPULATION_SCOPE = "us_residents_excluding_territories"
DEFAULT_INPUT = ROOT / "data" / "history" / "us" / "NNDSS_Weekly_Data_20260317.csv"
MIGRATION_REASON = (
    "Backfill exact CDC NNDSS U.S.-resident source-series observations; "
    "exclude the broader NNDSS TOTAL reporting scope"
)
MIGRATION_OPERATION = "series_resident_backfill_insert"
_AUDIT_BATCH_SIZE = 500
_WRITE_BATCH_SIZE = 500
_RESIDENT_ALIASES = frozenset(
    {
        "us residents",
        "u.s. residents",
        "united states residents",
    }
)
_MISSING_VALUES = frozenset(
    {
        "",
        "-",
        "--",
        "—",
        "u",
        "n",
        "nn",
        "np",
        "nc",
        "null",
        "na",
        "n/a",
    }
)
_CONTENT_FIELDS = (
    "time",
    "series_code",
    "geography_key",
    "dimension_key",
    "dimensions",
    "value",
    "unit",
    "suppressed",
    "suppression_reason",
    "quality_status",
    "raw_data",
    "metadata",
)


@dataclass(frozen=True)
class RegistryRoute:
    series_code: str
    status: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class SourceBuildResult:
    summary: dict[str, Any]
    observations: list[dict[str, Any]]
    registry_series_codes: tuple[str, ...]
    requested_start_date: date | None
    requested_end_date: date | None


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split())


def _first_text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        if key in row:
            value = _normalize_text(row.get(key))
            if value:
                return value
    return ""


def _is_resident_area(value: object) -> bool:
    return _normalize_text(value).casefold() in _RESIDENT_ALIASES


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def _registry_date(value: object) -> date | None:
    text_value = _normalize_text(value)
    return date.fromisoformat(text_value) if text_value else None


def _mmwr_week_end(year: int, week: int) -> date:
    """Return the Saturday ending a CDC MMWR week."""

    if not 1 <= week <= 53:
        raise ValueError("MMWR week must be between 1 and 53")
    january_fourth = date(year, 1, 4)
    week_one_start = january_fourth - timedelta(days=(january_fourth.weekday() + 1) % 7)
    following_january_fourth = date(year + 1, 1, 4)
    following_week_one_start = following_january_fourth - timedelta(
        days=(following_january_fourth.weekday() + 1) % 7
    )
    week_start = week_one_start + timedelta(weeks=week - 1)
    if week_start >= following_week_one_start:
        raise ValueError(f"MMWR year {year} does not contain week {week}")
    return week_start + timedelta(days=6)


def _parse_case_value(value: object) -> tuple[str, int | None]:
    normalized = _normalize_text(value).replace(",", "")
    if normalized.casefold() in _MISSING_VALUES:
        return "missing", None
    try:
        numeric = float(normalized)
    except ValueError:
        return "invalid", None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        return "invalid", None
    integer = int(numeric)
    return ("zero" if integer == 0 else "nonzero"), integer


def _parse_integer(value: object, *, field: str) -> int:
    normalized = _normalize_text(value).replace(",", "")
    try:
        numeric = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} is not numeric: {normalized!r}") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{field} must be a finite integer: {normalized!r}")
    return int(numeric)


def _identity(observation: Mapping[str, Any]) -> tuple[datetime, str, str, str]:
    return (
        _utc_datetime(observation["time"]),
        str(observation["series_code"]),
        str(observation["geography_key"]),
        str(observation.get("dimension_key") or DIMENSION_KEY),
    )


def _identity_json(observation: Mapping[str, Any]) -> dict[str, str]:
    report_time, series_code, geography_key, dimension_key = _identity(observation)
    return {
        "time": report_time.isoformat(),
        "series_code": series_code,
        "geography_key": geography_key,
        "dimension_key": dimension_key,
    }


def _identity_key(observation: Mapping[str, Any]) -> str:
    identity = _identity_json(observation)
    canonical = "|".join(
        identity[key]
        for key in ("time", "series_code", "geography_key", "dimension_key")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{identity['series_code']}|sha256:{digest}"


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _json_safe(value: object) -> Any:
    if isinstance(value, datetime):
        return _utc_datetime(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _content_image(observation: Mapping[str, Any]) -> dict[str, Any]:
    image: dict[str, Any] = {}
    for field in _CONTENT_FIELDS:
        value = observation.get(field)
        if field == "time":
            value = _utc_datetime(value).isoformat()
        elif field in {"dimensions", "raw_data", "metadata"}:
            value = _json_object(value)
        elif field == "value" and value is not None:
            value = float(value)
        image[field] = _json_safe(value)
    return image


def _raw_reporting_area(raw_data: object) -> str:
    row = _json_object(raw_data)
    return _first_text(row, "ReportingArea", "Reporting Area", "states")


def _registry_routes(
    ontology: Any,
) -> tuple[dict[str, list[RegistryRoute]], tuple[str, ...]]:
    document = ontology.to_dict()
    alias_routes: dict[str, list[RegistryRoute]] = defaultdict(list)
    series_codes: list[str] = []
    series_without_aliases: list[str] = []
    for series in document.get("source_series", []):
        if series.get("source_id") != SOURCE_ID:
            continue
        status = _normalize_text(series.get("status")).casefold()
        if status not in {"active", "historical"}:
            continue
        series_code = _normalize_text(series.get("id"))
        if not series_code:
            continue
        route = RegistryRoute(
            series_code=series_code,
            status=status,
            valid_from=_registry_date(series.get("valid_from")),
            valid_to=_registry_date(series.get("valid_to")),
        )
        aliases = {
            normalize_mapping_key(alias)
            for alias in [
                *series.get("local_labels", []),
                *series.get("local_codes", []),
            ]
            if _normalize_text(alias)
        }
        if not aliases:
            series_without_aliases.append(series_code)
        for alias in sorted(aliases):
            alias_routes[alias].append(route)
        series_codes.append(series_code)
    if series_without_aliases:
        raise ValueError(
            "Registry NNDSS series lack local labels/codes: "
            + ", ".join(sorted(series_without_aliases))
        )
    if not series_codes:
        raise ValueError("Registry has no active/historical SRC_US_NNDSS series")
    return dict(alias_routes), tuple(sorted(series_codes))


def _source_row(
    raw: Mapping[str, object],
    *,
    source_path: Path,
    source_line: int,
    label: str,
    reporting_area: str,
    year: int,
    week: int,
    report_date: date,
    cases: int,
) -> dict[str, Any]:
    row = {str(key): _normalize_text(value) for key, value in raw.items()}
    row.update(
        {
            "Date": report_date.isoformat(),
            "Diseases": label,
            "DiseasesCN": label,
            "Cases": str(cases),
            "Deaths": "",
            "Source": "US CDC NNDSS",
            "CountryCode": COUNTRY_CODE,
            "ReportingArea": reporting_area,
            "MMWRYear": str(year),
            "MMWRWeek": str(week),
            "CurrentWeekFlag": _first_text(raw, "Current week, flag", "m1_flag"),
            "RawDiseaseLabel": label,
            "IsProvisional": "true",
            "UpdateMode": "resident_series_backfill",
            "Frequency": "weekly",
            "Measure": "case_notifications",
            "PopulationScope": POPULATION_SCOPE,
            "__source_file": str(source_path.resolve()),
            "__source_line": source_line,
        }
    )
    return row


def build_source_plan(
    input_paths: Sequence[Path],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    ontology: Any | None = None,
) -> SourceBuildResult:
    """Build exact national observations from resident rows without touching DB."""

    if start_date and end_date and start_date > end_date:
        raise ValueError("--from-date must be on or before --to-date")
    registry = ontology or load_disease_ontology()
    routes, registry_series_codes = _registry_routes(registry)
    store = SeriesObservationStore(registry)

    counts: Counter[str] = Counter()
    resident_areas: Counter[str] = Counter()
    skipped_areas: Counter[str] = Counter()
    missing_flags: Counter[str] = Counter()
    unmatched_labels: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    candidates: dict[tuple[datetime, str, str, str], tuple[int, dict[str, Any]]] = {}
    duplicate_count = 0
    duplicate_examples: list[dict[str, Any]] = []
    conflict_count = 0

    for path_value in input_paths:
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            errors.append({"code": "input_missing", "path": str(path)})
            continue
        counts["input_files"] += 1
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                errors.append({"code": "header_missing", "path": str(path)})
                continue
            for line_number, raw in enumerate(reader, start=2):
                counts["source_rows"] += 1
                reporting_area = _first_text(raw, "Reporting Area", "states")
                if not _is_resident_area(reporting_area):
                    skipped_areas[reporting_area or "<missing>"] += 1
                    continue
                counts["resident_rows"] += 1
                resident_areas[reporting_area] += 1

                label = _first_text(raw, "Label", "label")
                route_candidates = routes.get(normalize_mapping_key(label), [])
                if not route_candidates:
                    unmatched_labels[label or "<missing>"] += 1
                    continue
                if len(route_candidates) != 1:
                    errors.append(
                        {
                            "code": "ambiguous_registry_route",
                            "path": str(path),
                            "line": line_number,
                            "label": label,
                            "series_codes": sorted(
                                {route.series_code for route in route_candidates}
                            ),
                        }
                    )
                    continue
                route = route_candidates[0]

                year_text = _first_text(raw, "Current MMWR Year", "year")
                week_text = _first_text(raw, "MMWR WEEK", "week")
                try:
                    year = _parse_integer(year_text, field="MMWR year")
                    week = _parse_integer(week_text, field="MMWR week")
                    report_date = _mmwr_week_end(year, week)
                except (TypeError, ValueError, OverflowError) as exc:
                    errors.append(
                        {
                            "code": "invalid_mmwr_period",
                            "path": str(path),
                            "line": line_number,
                            "label": label,
                            "year": year_text,
                            "week": week_text,
                            "error": str(exc),
                        }
                    )
                    continue
                if start_date and report_date < start_date:
                    counts["outside_range"] += 1
                    continue
                if end_date and report_date > end_date:
                    counts["outside_range"] += 1
                    continue
                if route.valid_from and report_date < route.valid_from:
                    errors.append(
                        {
                            "code": "series_outside_validity",
                            "series_code": route.series_code,
                            "date": report_date.isoformat(),
                            "valid_from": route.valid_from.isoformat(),
                        }
                    )
                    continue
                if route.valid_to and report_date > route.valid_to:
                    errors.append(
                        {
                            "code": "series_outside_validity",
                            "series_code": route.series_code,
                            "date": report_date.isoformat(),
                            "valid_to": route.valid_to.isoformat(),
                        }
                    )
                    continue

                raw_value = next(
                    (raw.get(key) for key in ("Current week", "m1") if key in raw),
                    None,
                )
                value_kind, cases = _parse_case_value(raw_value)
                if value_kind == "missing":
                    counts["missing_values"] += 1
                    missing_flags[
                        _first_text(raw, "Current week, flag", "m1_flag") or "<blank>"
                    ] += 1
                    continue
                if value_kind == "invalid" or cases is None:
                    errors.append(
                        {
                            "code": "invalid_case_value",
                            "path": str(path),
                            "line": line_number,
                            "label": label,
                            "value": _normalize_text(raw_value),
                        }
                    )
                    continue
                counts[value_kind] += 1
                prepared = _source_row(
                    raw,
                    source_path=path,
                    source_line=line_number,
                    label=label,
                    reporting_area=reporting_area,
                    year=year,
                    week=week,
                    report_date=report_date,
                    cases=cases,
                )
                built = store.build_observations(
                    [prepared],
                    COUNTRY_CODE,
                    source_id=SOURCE_ID,
                    value_field="Cases",
                )
                if (
                    len(built.observations) != 1
                    or built.skipped_unmatched
                    or built.skipped_ambiguous
                    or built.skipped_invalid
                ):
                    errors.append(
                        {
                            "code": "registry_observation_build_failed",
                            "path": str(path),
                            "line": line_number,
                            "label": label,
                            "series_code": route.series_code,
                            "built": len(built.observations),
                            "skipped_unmatched": built.skipped_unmatched,
                            "skipped_ambiguous": built.skipped_ambiguous,
                            "skipped_invalid": built.skipped_invalid,
                        }
                    )
                    continue
                observation = built.observations[0]
                if observation["series_code"] != route.series_code:
                    errors.append(
                        {
                            "code": "registry_route_changed",
                            "label": label,
                            "expected": route.series_code,
                            "actual": observation["series_code"],
                        }
                    )
                    continue
                if observation["geography_key"] != NATIONAL_GEOGRAPHY:
                    errors.append(
                        {
                            "code": "non_national_resident_route",
                            "label": label,
                            "geography_key": observation["geography_key"],
                        }
                    )
                    continue
                identity = _identity(observation)
                previous = candidates.get(identity)
                if previous is not None:
                    previous_raw = _json_object(previous[1].get("raw_data"))
                    if previous[0] != cases:
                        conflict_count += 1
                        errors.append(
                            {
                                "code": "overlapping_extract_value_conflict",
                                "identity": _identity_json(observation),
                                "first_value": previous[0],
                                "second_value": cases,
                                "first_path": previous_raw.get("__source_file"),
                                "first_line": previous_raw.get("__source_line"),
                                "first_reporting_area": _raw_reporting_area(
                                    previous_raw
                                ),
                                "second_path": str(path),
                                "second_line": line_number,
                                "second_reporting_area": reporting_area,
                            }
                        )
                    else:
                        duplicate_count += 1
                        if len(duplicate_examples) < 20:
                            duplicate_examples.append(
                                {
                                    "identity": _identity_json(observation),
                                    "value": cases,
                                    "first_path": previous_raw.get("__source_file"),
                                    "first_line": previous_raw.get("__source_line"),
                                    "first_reporting_area": _raw_reporting_area(
                                        previous_raw
                                    ),
                                    "second_path": str(path),
                                    "second_line": line_number,
                                    "second_reporting_area": reporting_area,
                                }
                            )
                    continue
                candidates[identity] = (cases, observation)

    observations = [item[1] for _, item in sorted(candidates.items())]
    coverage: dict[str, dict[str, Any]] = {}
    by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        by_series[str(observation["series_code"])].append(observation)
    for series_code in registry_series_codes:
        rows = by_series.get(series_code, [])
        coverage[series_code] = {
            "observations": len(rows),
            "start": min((_identity(row)[0].date() for row in rows), default=None),
            "end": max((_identity(row)[0].date() for row in rows), default=None),
        }
        for key in ("start", "end"):
            if coverage[series_code][key] is not None:
                coverage[series_code][key] = coverage[series_code][key].isoformat()

    summary = {
        "source_id": SOURCE_ID,
        "country_code": COUNTRY_CODE,
        "geography_key": NATIONAL_GEOGRAPHY,
        "population_scope": POPULATION_SCOPE,
        "inputs": [str(Path(path).expanduser().resolve()) for path in input_paths],
        "from_date": start_date.isoformat() if start_date else None,
        "to_date": end_date.isoformat() if end_date else None,
        "registry_series": len(registry_series_codes),
        "counts": dict(sorted(counts.items())),
        "resident_reporting_areas": dict(resident_areas.most_common()),
        "skipped_reporting_areas": dict(skipped_areas.most_common()),
        "missing_value_flags": dict(missing_flags.most_common()),
        "unmatched_labels": len(unmatched_labels),
        "unmatched_label_examples": dict(unmatched_labels.most_common(20)),
        "deduplicated_same_value": duplicate_count,
        "deduplicated_same_value_examples": duplicate_examples,
        "overlap_conflicts": conflict_count,
        "observations": len(observations),
        "series_coverage": coverage,
        "errors": errors[:100],
        "error_count": len(errors),
        "status": "blocked" if errors or not observations else "ready",
    }
    if not observations and not errors:
        summary["errors"] = [{"code": "no_resident_observations"}]
        summary["error_count"] = 1
    return SourceBuildResult(
        summary=summary,
        observations=observations,
        registry_series_codes=registry_series_codes,
        requested_start_date=start_date,
        requested_end_date=end_date,
    )


def classify_existing(
    observations: Sequence[Mapping[str, Any]],
    existing_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify candidates as absent/exact/conflicting using full content."""

    incoming = {_identity(row): row for row in observations}
    existing = {_identity(row): row for row in existing_rows}
    nonresident: list[dict[str, Any]] = []
    for row in existing_rows:
        area = _raw_reporting_area(row.get("raw_data"))
        if not _is_resident_area(area):
            nonresident.append(
                {
                    "identity": _identity_json(row),
                    "reporting_area": area or "<missing>",
                }
            )

    absent: list[Mapping[str, Any]] = []
    exact: list[Mapping[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for identity, row in incoming.items():
        current = existing.get(identity)
        if current is None:
            absent.append(row)
            continue
        area = _raw_reporting_area(current.get("raw_data"))
        if not _is_resident_area(area):
            conflicts.append(
                {
                    "identity": _identity_json(row),
                    "reason": "existing_nonresident_provenance",
                    "reporting_area": area or "<missing>",
                }
            )
            continue
        if _content_image(current) == _content_image(row):
            exact.append(row)
        else:
            conflicts.append(
                {
                    "identity": _identity_json(row),
                    "reason": "existing_content_differs",
                    "incoming": _content_image(row),
                    "existing": _content_image(current),
                }
            )
    return {
        "absent": absent,
        "exact": exact,
        "conflicts": conflicts,
        "nonresident": nonresident,
        "status": "blocked" if conflicts or nonresident else "ready",
    }


async def _database_tables_ready(db: Any) -> bool:
    result = await db.execute(text("""
            SELECT
                to_regclass('public.disease_surveillance_series') IS NOT NULL
                AND to_regclass('public.disease_series_observations') IS NOT NULL
            """))
    return bool(result.scalar())


async def _load_database_series(db: Any) -> dict[str, dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text("""
                    SELECT series_code, source_system,
                           availability_status AS status
                    FROM disease_surveillance_series
                    WHERE source_system = :source_id
                    """),
                {"source_id": SOURCE_ID},
            )
        )
        .mappings()
        .all()
    )
    return {str(row["series_code"]): dict(row) for row in rows}


async def _load_existing_observations(
    db: Any,
    *,
    series_codes: Sequence[str],
    start_time: datetime,
    end_time: datetime,
    for_update: bool,
) -> list[dict[str, Any]]:
    lock = "FOR UPDATE" if for_update else ""
    rows = (
        (
            await db.execute(
                text(f"""
                    SELECT observation.*
                    FROM disease_series_observations observation
                    JOIN disease_surveillance_series series
                      ON series.series_code = observation.series_code
                    WHERE series.source_system = :source_id
                      AND observation.series_code = ANY(:series_codes)
                      AND observation.geography_key = :geography_key
                      AND observation.dimension_key = :dimension_key
                      AND observation.time >= :start_time
                      AND observation.time <= :end_time
                    {lock}
                    """),
                {
                    "source_id": SOURCE_ID,
                    "series_codes": list(series_codes),
                    "geography_key": NATIONAL_GEOGRAPHY,
                    "dimension_key": DIMENSION_KEY,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _database_scope_bounds(build: SourceBuildResult) -> tuple[datetime, datetime]:
    """Return the complete inclusive provenance-audit window for a plan."""

    if not build.observations:
        raise ValueError("Cannot derive database scope from an empty source plan")
    times = [_identity(row)[0] for row in build.observations]
    scope_start_date = build.requested_start_date or min(times).date()
    scope_end_date = build.requested_end_date or max(times).date()
    return (
        datetime.combine(scope_start_date, time.min, tzinfo=timezone.utc),
        datetime.combine(scope_end_date, time.max, tzinfo=timezone.utc),
    )


async def database_preflight(
    db: Any,
    build: SourceBuildResult,
    *,
    for_update: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not await _database_tables_ready(db):
        report = {
            "status": "blocked",
            "errors": ["ontology observation tables are not installed"],
        }
        return report, {
            "absent": [],
            "exact": [],
            "conflicts": [],
            "nonresident": [],
            "status": "blocked",
        }
    database_series = await _load_database_series(db)
    missing_series = sorted(set(build.registry_series_codes) - set(database_series))
    invalid_series = sorted(
        code
        for code in build.registry_series_codes
        if code in database_series
        and (
            database_series[code].get("source_system") != SOURCE_ID
            or str(database_series[code].get("status") or "").casefold()
            not in {"active", "historical"}
        )
    )
    if not build.observations:
        report = {
            "status": "blocked",
            "errors": ["source plan contains no observations"],
            "missing_registry_series": missing_series,
            "invalid_registry_series": invalid_series,
        }
        return report, {
            "absent": [],
            "exact": [],
            "conflicts": [],
            "nonresident": [],
            "status": "blocked",
        }
    scope_start_time, scope_end_time = _database_scope_bounds(build)
    existing = await _load_existing_observations(
        db,
        series_codes=build.registry_series_codes,
        start_time=scope_start_time,
        end_time=scope_end_time,
        for_update=for_update,
    )
    classification = classify_existing(build.observations, existing)
    content_conflicts = [
        item
        for item in classification["conflicts"]
        if item.get("reason") == "existing_content_differs"
    ]
    candidate_provenance_conflicts = [
        item
        for item in classification["conflicts"]
        if item.get("reason") == "existing_nonresident_provenance"
    ]
    errors: list[str] = []
    if missing_series:
        errors.append(f"database is missing {len(missing_series)} Registry series")
    if invalid_series:
        errors.append(f"database has {len(invalid_series)} invalid Registry series")
    if classification["nonresident"]:
        errors.append(
            f"{len(classification['nonresident'])} national facts have "
            "non-resident provenance"
        )
    if content_conflicts:
        errors.append(
            f"{len(content_conflicts)} resident target facts differ from source"
        )
    report = {
        "status": "blocked" if errors else "ready",
        "database_series": len(database_series),
        "scope_start_time": scope_start_time.isoformat(),
        "scope_end_time": scope_end_time.isoformat(),
        "missing_registry_series": missing_series,
        "invalid_registry_series": invalid_series,
        "existing_rows_in_scope": len(existing),
        "planned_inserts": len(classification["absent"]),
        "exact_noops": len(classification["exact"]),
        "target_key_conflicts": len(classification["conflicts"]),
        "content_conflicts": len(content_conflicts),
        "content_conflict_examples": content_conflicts[:20],
        "candidate_nonresident_provenance_conflicts": len(
            candidate_provenance_conflicts
        ),
        "nonresident_provenance_conflicts": len(classification["nonresident"]),
        "nonresident_provenance_examples": classification["nonresident"][:20],
        "errors": errors,
    }
    return report, classification


async def _ensure_audit_schema(db: Any) -> None:
    await db.execute(text("""
            CREATE TABLE IF NOT EXISTS disease_migration_audit (
                id BIGSERIAL PRIMARY KEY,
                migration_run_id VARCHAR(160) NOT NULL,
                migration_key VARCHAR(500) NOT NULL,
                entity_table VARCHAR(160) NOT NULL,
                operation VARCHAR(80) NOT NULL,
                identity_key VARCHAR(500) NOT NULL,
                identity JSONB NOT NULL,
                before_data JSONB NOT NULL,
                after_data JSONB,
                reason TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                restored_at TIMESTAMPTZ,
                CONSTRAINT uq_disease_migration_audit_run_identity
                    UNIQUE (migration_run_id, migration_key, identity_key)
            )
            """))
    await db.execute(text("""
            ALTER TABLE disease_migration_audit
            DROP CONSTRAINT IF EXISTS
                disease_migration_audit_migration_key_identity_key_key
            """))
    await db.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_disease_migration_audit_run_identity'
                      AND conrelid =
                          'public.disease_migration_audit'::regclass
                ) THEN
                    ALTER TABLE disease_migration_audit
                    ADD CONSTRAINT uq_disease_migration_audit_run_identity
                    UNIQUE (migration_run_id, migration_key, identity_key);
                END IF;
            END $$
            """))
    await db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_disease_migration_audit_run
            ON disease_migration_audit (migration_run_id, migration_key)
            """))


def _migration_key(start_date: date, end_date: date) -> str:
    return (
        "series_resident_backfill:SRC_US_NNDSS:country:US:national:"
        f"{start_date.isoformat()}..{end_date.isoformat()}"
    )


async def _insert_fact_batch(
    db: Any, observations: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if not observations:
        return []
    statement = (
        pg_insert(DiseaseSeriesObservation.__table__)
        .values([dict(row) for row in observations])
        .on_conflict_do_nothing(constraint="uq_disease_series_observation_identity")
        .returning(
            DiseaseSeriesObservation.time,
            DiseaseSeriesObservation.series_code,
            DiseaseSeriesObservation.geography_key,
            DiseaseSeriesObservation.dimension_key,
        )
    )
    result = await db.execute(statement)
    return [dict(row) for row in result.mappings().all()]


async def _audit_inserted_batch(
    db: Any,
    inserted: Sequence[Mapping[str, Any]],
    *,
    migration_run_id: str,
    migration_key: str,
) -> int:
    if not inserted:
        return 0
    identities = []
    for row in inserted:
        identity = _identity_json(row)
        identities.append(
            {
                **identity,
                "identity_key": _identity_key(row),
            }
        )
    result = await db.execute(
        text("""
            WITH identities AS (
                SELECT *
                FROM jsonb_to_recordset(CAST(:identities AS jsonb)) AS item(
                    time timestamptz,
                    series_code text,
                    geography_key text,
                    dimension_key text,
                    identity_key text
                )
            )
            INSERT INTO disease_migration_audit (
                migration_run_id, migration_key, entity_table, operation,
                identity_key, identity, before_data, after_data, reason
            )
            SELECT
                :migration_run_id,
                :migration_key,
                'disease_series_observations',
                :operation,
                identity.identity_key,
                jsonb_build_object(
                    'time', observation.time,
                    'series_code', observation.series_code,
                    'geography_key', observation.geography_key,
                    'dimension_key', observation.dimension_key
                ),
                '{}'::jsonb,
                to_jsonb(observation),
                :reason
            FROM identities identity
            JOIN disease_series_observations observation
              ON observation.time = identity.time
             AND observation.series_code = identity.series_code
             AND observation.geography_key = identity.geography_key
             AND observation.dimension_key = identity.dimension_key
            RETURNING identity_key
            """),
        {
            "identities": json.dumps(identities, ensure_ascii=False),
            "migration_run_id": migration_run_id,
            "migration_key": migration_key,
            "operation": MIGRATION_OPERATION,
            "reason": MIGRATION_REASON,
        },
    )
    return len(result.mappings().all())


async def _verify_audit_images(
    db: Any, *, migration_run_id: str, migration_key: str
) -> int:
    result = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM disease_migration_audit audit
            JOIN disease_series_observations observation
              ON observation.time =
                    CAST(audit.identity ->> 'time' AS timestamptz)
             AND observation.series_code = audit.identity ->> 'series_code'
             AND observation.geography_key =
                    audit.identity ->> 'geography_key'
             AND observation.dimension_key =
                    audit.identity ->> 'dimension_key'
            WHERE audit.migration_run_id = :migration_run_id
              AND audit.migration_key = :migration_key
              AND audit.operation = :operation
              AND audit.before_data = '{}'::jsonb
              AND audit.after_data = to_jsonb(observation)
            """),
        {
            "migration_run_id": migration_run_id,
            "migration_key": migration_key,
            "operation": MIGRATION_OPERATION,
        },
    )
    return int(result.scalar() or 0)


async def _apply_insertions(
    db: Any,
    observations: Sequence[Mapping[str, Any]],
    *,
    migration_run_id: str,
    migration_key: str,
) -> dict[str, int]:
    inserted: list[dict[str, Any]] = []
    for offset in range(0, len(observations), _WRITE_BATCH_SIZE):
        inserted.extend(
            await _insert_fact_batch(
                db, observations[offset : offset + _WRITE_BATCH_SIZE]
            )
        )
    audit_count = 0
    for offset in range(0, len(inserted), _AUDIT_BATCH_SIZE):
        audit_count += await _audit_inserted_batch(
            db,
            inserted[offset : offset + _AUDIT_BATCH_SIZE],
            migration_run_id=migration_run_id,
            migration_key=migration_key,
        )
    if audit_count != len(inserted):
        raise RuntimeError(
            "Incomplete resident backfill audit: "
            f"inserted={len(inserted)}, audited={audit_count}"
        )
    verified = await _verify_audit_images(
        db, migration_run_id=migration_run_id, migration_key=migration_key
    )
    if verified != len(inserted):
        raise RuntimeError(
            "Resident backfill audit after-images are incomplete: "
            f"inserted={len(inserted)}, verified={verified}"
        )
    return {
        "inserted": len(inserted),
        "audit_rows": audit_count,
        "verified_after_images": verified,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        help=(
            "Raw CDC NNDSS CSV; repeat for overlapping extracts. Dry-run "
            f"defaults to {DEFAULT_INPUT}."
        ),
    )
    parser.add_argument(
        "--from-date",
        type=_parse_date,
        help="Inclusive MMWR week-end date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--to-date",
        type=_parse_date,
        help="Inclusive MMWR week-end date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert only absent exact resident facts and write reversible audit rows.",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.input:
        parser.error("--apply requires at least one explicit --input")
    if args.apply and (args.from_date is None or args.to_date is None):
        parser.error("--apply requires explicit --from-date and --to-date")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    return args


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply and not args.input:
        raise ValueError("--apply requires at least one explicit --input")
    if args.apply and (args.from_date is None or args.to_date is None):
        raise ValueError("--apply requires explicit --from-date and --to-date")
    if args.from_date and args.to_date and args.from_date > args.to_date:
        raise ValueError("--from-date must be on or before --to-date")
    inputs = list(args.input or [DEFAULT_INPUT])
    build = build_source_plan(
        inputs,
        start_date=args.from_date,
        end_date=args.to_date,
    )
    result: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "source_plan": build.summary,
    }
    async with get_db() as db:
        if args.apply:
            await acquire_disease_data_mutation_lock(db)
        database_report, classification = await database_preflight(
            db, build, for_update=bool(args.apply)
        )
        result["database_preflight"] = database_report
        if build.summary["status"] != "ready" or database_report["status"] != "ready":
            result["status"] = "blocked"
            if args.apply:
                raise RuntimeError(
                    "Refusing US NNDSS resident backfill: source or database "
                    "preflight is blocked"
                )
            return result
        result["status"] = "ready"
        if not args.apply:
            return result

        assert args.from_date is not None and args.to_date is not None
        await _ensure_audit_schema(db)
        migration_run_id = f"us-nndss-resident:{uuid.uuid4()}"
        migration_key = _migration_key(args.from_date, args.to_date)
        applied = await _apply_insertions(
            db,
            classification["absent"],
            migration_run_id=migration_run_id,
            migration_key=migration_key,
        )

        # A concurrent writer can win a natural key after preflight. Re-read
        # the complete target scope and require every planned row to be exact.
        final_report, final_classification = await database_preflight(
            db, build, for_update=True
        )
        if (
            final_report["status"] != "ready"
            or final_classification["absent"]
            or final_classification["conflicts"]
            or final_classification["nonresident"]
        ):
            raise RuntimeError(
                "Resident backfill post-verification failed; transaction will roll back"
            )
        result.update(
            {
                "status": "applied",
                "migration_run_id": migration_run_id,
                "migration_key": migration_key,
                "planned_inserts": len(classification["absent"]),
                "exact_noops": len(classification["exact"]),
                **applied,
                "restore_command": (
                    "PYTHONPATH=. venv/bin/python "
                    "scripts/restore_disease_migration.py "
                    f"--migration-key {migration_key} --run-id {migration_run_id}"
                ),
            }
        )
        return result


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
