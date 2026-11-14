"""Persist source-series observations without collapsing canonical concepts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import and_, case, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.domain import (
    DiseaseSeriesObservation,
    DiseaseSeriesObservationRevision,
    DiseaseSurveillanceSeries,
    SourceDiseaseCategory,
)
from src.ontology import DiseaseOntology, load_disease_ontology

from src.data.storage.series.identity import (
    _SUPPRESSED_VALUES,
    _SUPPRESSED_THRESHOLD,
    _as_utc,
    _advance_period,
    _previous_period,
    _periods_are_consecutive,
    _period_range,
    _days_in_month,
    _parse_report_time,
    _mmwr_week_end_date,
    _parse_value,
    _first_text,
    _normalized_identity,
    _dimension_key,
    _parse_dimensions,
    _source_id_for_row,
    _geography_key,
    _series_valid_at,
    _canonical_source_code,
    _json_safe,
    _quality_status,
    _allows_authoritative_revision,
    _allows_equal_quality_overwrite,
    _observation_allows_authoritative_revision,
)
from src.data.storage.series.quality import (
    _QUALITY_MODES,
    _REGISTRY_COVERAGE_MODES,
    _SEVERITY_RANK,
    SeriesObservationQualityIssue,
    SeriesObservationQualityReport,
    SeriesObservationQualityPolicy,
    SeriesObservationQualityError,
    SeriesObservationQuarantinedError,
    _mapping_coverage_issues,
    _zero_tail_issues,
    _single_series_revision_issues,
    _time_completeness_issues,
    _frequency_by_series,
    _cohort_period_rows,
    _period_is_cross_series_zero,
)

_WRITE_BATCH_SIZE = 500
_UNMAPPED_GROUP_CODE = "G_UNMAPPED_SOURCE_CATEGORIES"


def _holding_series_measure_profile(
    row: Mapping[str, Any], *, country_code: str, source_id: str
) -> tuple[str, str, str]:
    """Preserve Japan IDWR all-case versus sentinel reporting in holding series."""

    measure = _first_text(row, "Measure", "measure") or "case_notifications"
    basis = _first_text(row, "ReportingBasis", "reporting_basis") or "notification"
    frequency = _first_text(row, "Frequency", "frequency") or "unknown"
    if country_code.upper() == "JP" and source_id == "SRC_JP_NIID":
        surveillance_type = (_first_text(row, "SurveillanceType") or "").casefold()
        if surveillance_type == "sentinel":
            measure, basis = "sentinel_case_notifications", "sentinel_surveillance"
        elif surveillance_type == "all_case":
            measure, basis = "case_notifications", "notification"
        if _first_text(row, "MMWRYear") and _first_text(row, "MMWRWeek"):
            frequency = "weekly"
    return measure, basis, frequency


def _holding_series_code(
    country_code: str, category_key: str, surveillance_type: str | None = None
) -> str:
    """Stable holding code. Typed siblings keep the legacy code for one type."""

    material = category_key if not surveillance_type else f"{category_key}|{surveillance_type}"
    digest = hashlib.sha256(material.encode()).hexdigest()[:28]
    prefix = country_code.upper().replace("-", "_")
    return f"HOLD_{prefix}_{digest}"


def _jp_surveillance_type(row: Mapping[str, Any]) -> str:
    return (_first_text(dict(row), "SurveillanceType") or "").casefold()


def _bind_legacy_jp_surveillance_type(metadata_type: str, counts: Mapping[str, int]) -> str:
    """Keep an untyped legacy series on the stored majority, preferring all-case ties."""

    stamped = metadata_type.casefold()
    if stamped in {"all_case", "sentinel"}:
        return stamped
    if counts.get("sentinel", 0) > counts.get("all_case", 0):
        return "sentinel"
    return "all_case"


def _stamp_jp_holding_series(
    series: DiseaseSurveillanceSeries,
    row: Mapping[str, Any],
    *,
    country_code: str,
    source_id: str,
    surveillance_type: str,
) -> None:
    measure, basis, frequency = _holding_series_measure_profile(
        row, country_code=country_code, source_id=source_id
    )
    series.metric_type = measure
    series.reporting_basis = basis
    series.temporal_granularity = frequency
    series.metadata_ = {
        **(series.metadata_ or {}),
        "surveillance_type": surveillance_type,
    }


def _new_dynamic_holding_series(
    *,
    series_code: str,
    category: SourceDiseaseCategory,
    country_code: str,
    row: Mapping[str, Any],
    measure: str,
    reporting_basis: str,
    frequency: str,
    surveillance_type: str = "",
) -> DiseaseSurveillanceSeries:
    source_series_code = category.source_code
    if len(source_series_code) > 240:
        source_series_code = (
            source_series_code[:210]
            + ":"
            + hashlib.sha256(source_series_code.encode()).hexdigest()[:24]
        )
    metadata: dict[str, Any] = {
        "dynamic_holding": True,
        "source_category_id": category.id,
        "category_key": category.category_key,
    }
    if surveillance_type:
        metadata["surveillance_type"] = surveillance_type
    return DiseaseSurveillanceSeries(
        series_code=series_code,
        disease_id=None,
        target_group_code=_UNMAPPED_GROUP_CODE,
        country_code=country_code.upper(),
        source_system=category.source_id,
        source_series_code=source_series_code,
        source_label=category.canonical_source_label,
        definition_version=category.definition_version,
        case_definition=category.source_definition,
        case_definition_uri=category.source_definition_uri,
        metric_type=measure,
        reporting_basis=reporting_basis,
        temporal_granularity=frequency,
        unit=_first_text(dict(row), "Unit", "unit") or "count",
        mapping_relation="unmapped",
        comparability="not_comparable",
        aggregation_policy="non_additive",
        availability_status="active",
        missing_value_policy="missing_is_unknown",
        is_active=True,
        metadata_=metadata,
    )


def _json_surveillance_type(column):
    return func.lower(column["SurveillanceType"].as_string())


_REVISION_KEY_COLUMNS = ("time", "series_code", "geography_key", "dimension_key")
_REVISION_STATE_COLUMNS = (
    "value",
    "unit",
    "suppressed",
    "suppression_reason",
    "quality_status",
    "dimensions",
    "raw_data",
    "metadata",
)
_REVISION_COMPARISON_COLUMNS = (
    "value",
    "unit",
    "suppressed",
    "suppression_reason",
    "quality_status",
    "dimensions",
)


def _revision_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        _as_utc(row[key]) if key == "time" else row[key]
        for key in _REVISION_KEY_COLUMNS
    )


def _revision_state(row: Mapping[str, Any]) -> str:
    # Retrieval timestamps and raw artifact locations may change on a routine
    # refresh. Preserve them in snapshots, but only create a new value revision
    # when the reported observation or its interpretation actually changes.
    state = {key: row.get(key) for key in _REVISION_COMPARISON_COLUMNS}
    metadata = row.get("metadata") or {}
    state["interpretation"] = {
        key: metadata.get(key)
        for key in (
            "source_id", "local_code", "local_label", "measure", "frequency",
            "rollup_policy", "population_scope", "missing_value_status",
            "dynamic_holding", "source_category_id",
        )
    }
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


def _revision_snapshot(row: Mapping[str, Any], recorded_at: datetime) -> dict[str, Any]:
    snapshot = {
        key: (_as_utc(row[key]) if key == "time" else row[key])
        for key in _REVISION_KEY_COLUMNS
    }
    snapshot.update({key: row.get(key) for key in _REVISION_STATE_COLUMNS})
    snapshot["recorded_at"] = _as_utc(recorded_at)
    return snapshot


async def _persist_observation_revisions(
    db: AsyncSession,
    observations: list[dict[str, Any]],
    *,
    recorded_at: datetime,
    seed_current_state: bool,
    changed_observations: list[dict[str, Any]] | None = None,
) -> int:
    """Seed legacy current rows once, then append only changed accepted states."""
    # Each identity expands to four bind parameters in both lookup queries,
    # and each revision insert expands to all snapshot columns. Large source
    # refreshes must keep both statements within PostgreSQL/asyncpg limits.
    inserted = 0
    for offset in range(0, len(observations), _WRITE_BATCH_SIZE):
        inserted += await _persist_observation_revisions_batch(
            db,
            observations[offset : offset + _WRITE_BATCH_SIZE],
            recorded_at=recorded_at,
            seed_current_state=seed_current_state,
            changed_observations=changed_observations,
        )
    return inserted


async def _persist_observation_revisions_batch(
    db: AsyncSession,
    observations: list[dict[str, Any]],
    *,
    recorded_at: datetime,
    seed_current_state: bool,
    changed_observations: list[dict[str, Any]] | None = None,
) -> int:
    if not observations:
        return 0
    keys = list(dict.fromkeys(_revision_identity(row) for row in observations))
    current_result = await db.execute(
        select(
            DiseaseSeriesObservation.time,
            DiseaseSeriesObservation.series_code,
            DiseaseSeriesObservation.geography_key,
            DiseaseSeriesObservation.dimension_key,
            DiseaseSeriesObservation.value,
            DiseaseSeriesObservation.unit,
            DiseaseSeriesObservation.suppressed,
            DiseaseSeriesObservation.suppression_reason,
            DiseaseSeriesObservation.quality_status,
            DiseaseSeriesObservation.dimensions,
            DiseaseSeriesObservation.raw_data,
            DiseaseSeriesObservation.metadata_.label("metadata"),
            DiseaseSeriesObservation.created_at,
            DiseaseSeriesObservation.updated_at,
        ).where(
            tuple_(
                DiseaseSeriesObservation.time,
                DiseaseSeriesObservation.series_code,
                DiseaseSeriesObservation.geography_key,
                DiseaseSeriesObservation.dimension_key,
            ).in_(keys)
        )
    )
    current = [dict(row) for row in current_result.mappings().all()]
    if not current:
        return 0

    identity_columns = (
        DiseaseSeriesObservationRevision.time,
        DiseaseSeriesObservationRevision.series_code,
        DiseaseSeriesObservationRevision.geography_key,
        DiseaseSeriesObservationRevision.dimension_key,
    )
    history_query = (
        select(
            DiseaseSeriesObservationRevision.time,
            DiseaseSeriesObservationRevision.series_code,
            DiseaseSeriesObservationRevision.geography_key,
            DiseaseSeriesObservationRevision.dimension_key,
            DiseaseSeriesObservationRevision.recorded_at,
            DiseaseSeriesObservationRevision.value,
            DiseaseSeriesObservationRevision.unit,
            DiseaseSeriesObservationRevision.suppressed,
            DiseaseSeriesObservationRevision.suppression_reason,
            DiseaseSeriesObservationRevision.quality_status,
            DiseaseSeriesObservationRevision.dimensions,
            DiseaseSeriesObservationRevision.raw_data,
            DiseaseSeriesObservationRevision.metadata_.label("metadata"),
        )
        .where(
            tuple_(
                DiseaseSeriesObservationRevision.time,
                DiseaseSeriesObservationRevision.series_code,
                DiseaseSeriesObservationRevision.geography_key,
                DiseaseSeriesObservationRevision.dimension_key,
            ).in_(keys)
        )
        # Only the latest state per observation participates in comparison.
        # Fetching every historical revision turns an incremental crawl into a
        # growing full-history read, especially for frequently revised weeks.
        .distinct(*identity_columns)
        .order_by(*identity_columns, DiseaseSeriesObservationRevision.recorded_at.desc())
    )
    history_result = await db.execute(history_query)
    revisions = [dict(row) for row in history_result.mappings().all()]

    inserts: list[dict[str, Any]] = []
    if seed_current_state:
        known = {_revision_identity(row) for row in revisions}
        for row in current:
            if _revision_identity(row) not in known:
                timestamp = row.get("updated_at") or row.get("created_at") or recorded_at
                inserts.append(_revision_snapshot(row, timestamp))
    else:
        latest: dict[tuple[Any, ...], Mapping[str, Any]] = {}
        for row in revisions:
            latest.setdefault(_revision_identity(row), row)
        for row in current:
            previous = latest.get(_revision_identity(row))
            if previous is None or _revision_state(previous) != _revision_state(row):
                inserts.append(_revision_snapshot(row, recorded_at))

    if not inserts:
        return 0
    if not seed_current_state and changed_observations is not None:
        # Capture the accepted database state, not the incoming proposal or
        # PostgreSQL upsert count. Seeding pre-existing history is not a change.
        changed_observations.extend(
            {key: row[key] for key in _REVISION_KEY_COLUMNS} for row in inserts
        )
    statement = pg_insert(DiseaseSeriesObservationRevision.__table__)
    statement = statement.on_conflict_do_nothing(
        constraint="uq_disease_series_observation_revision"
    ).returning(DiseaseSeriesObservationRevision.time)
    result = await db.execute(statement, inserts)
    return len(result.scalars().all())


@dataclass(frozen=True)
class SeriesObservationBuildResult:
    observations: list[dict[str, Any]]
    skipped_unmatched: int
    skipped_ambiguous: int
    skipped_invalid: int


@dataclass(frozen=True)
class SeriesObservationSaveResult:
    upserted: int
    skipped_unmatched: int
    skipped_ambiguous: int
    skipped_invalid: int
    skipped_registry_not_synced: int
    quality_report: SeriesObservationQualityReport
    skipped_unregistered: int = 0
    skipped_missing: int = 0
    holding_observations: int = 0
    # None means coverage is unknown; an empty tuple proves a semantic no-op.
    # Keep None as the default for compatibility stores and legacy-only imports.
    publication_changes: tuple[dict[str, Any], ...] | None = None


def observation_publication_changes(
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Compact changed fact identities without merging distinct historical years."""
    ranges: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in observations:
        when = _as_utc(row["time"])
        key = (row["geography_key"], row["series_code"], when.year)
        timestamp = when.isoformat()
        if key not in ranges:
            ranges[key] = {
                "geography_key": key[0], "series_code": key[1],
                "time_start": timestamp, "time_end": timestamp,
                "operation": "upsert",
            }
        else:
            ranges[key]["time_start"] = min(ranges[key]["time_start"], timestamp)
            ranges[key]["time_end"] = max(ranges[key]["time_end"], timestamp)
    return tuple(ranges[key] for key in sorted(ranges))


@dataclass(frozen=True)
class RegistryRowSelection:
    """Rows eligible for an explicitly partial Registry dual write."""

    rows: list[dict[str, Any]]
    skipped_unregistered: int
    skipped_missing: int
    unregistered_rows: tuple[dict[str, Any], ...] = ()


class SeriesObservationStore:
    """Resolve raw rows to ontology series and upsert their natural fact grain."""

    def __init__(self, ontology: DiseaseOntology | None = None) -> None:
        self.ontology = ontology or load_disease_ontology()
        # The ontology is immutable for this store's lifetime. Many locations
        # and months share a source identity; resolve it once per batch/store.
        self._resolved_series_cache: dict[tuple, list[dict[str, Any]]] = {}

    def enrich_registry_identities(
        self,
        rows: Iterable[Mapping[str, Any]],
        country_code: str,
        *,
        source_id: str | Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Attach the reviewed source identity before Registry discovery.

        Crawlers commonly publish a label and/or local code but omit the
        ontology ``definition_version``.  Discovering those rows first creates
        a second ``source-current`` category beside the reviewed category.  A
        row is enriched only when its code/label and observation date resolve
        to exactly one ontology series.  Ambiguous and genuinely new rows are
        deliberately left unchanged for the holding-series review workflow.
        """

        enriched: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            # ``Diseases`` is used as a label by several wide-table crawlers;
            # treating it as an explicit source code is what previously made
            # label-only registered series look unregistered.
            explicit_local_code = _first_text(
                row,
                "SourceDiseaseCode",
                "source_disease_code",
                "DiseaseCode",
                "local_code",
            )
            resolved_source = _source_id_for_row(row, source_id)
            matches = self._resolve_series(
                country_code=country_code,
                source_id=resolved_source,
                local_code=explicit_local_code,
                local_label=label,
            )
            report_time = _parse_report_time(row)
            if report_time is not None:
                matches = [
                    series for series in matches if _series_valid_at(series, report_time)
                ]
            if len(matches) != 1:
                enriched.append(row)
                continue

            series = matches[0]
            resolved_code = _canonical_source_code(
                series,
                explicit_local_code=explicit_local_code,
                local_label=label,
            )
            definition_version = str(series.get("definition_version") or "").strip()
            if not resolved_code or not definition_version:
                enriched.append(row)
                continue

            # Canonical casing is part of the source identity in PostgreSQL.
            # Use the ontology spelling after a case-insensitive match.
            row["SourceDiseaseCode"] = resolved_code
            if not _first_text(row, "DefinitionVersion", "definition_version"):
                row["DefinitionVersion"] = definition_version
            enriched.append(row)
        return enriched

    def select_registry_rows(
        self,
        rows: list[dict[str, Any]],
        country_code: str,
        *,
        source_id: str | Mapping[str, str] | None = None,
        value_field: str = "Cases",
    ) -> RegistryRowSelection:
        """Select declared-series rows without disguising missing cells as errors.

        Some upstream tables publish many conditions while the Registry models
        only an explicitly reviewed subset. Unregistered labels are outside
        that dual-write scope, and blank cells mean missing observations. Rows
        that target a declared series and contain any non-blank value remain in
        the batch so normal parsing, ambiguity, and quality checks can fail
        closed on malformed data.
        """

        selected: list[dict[str, Any]] = []
        unregistered_rows: list[dict[str, Any]] = []
        skipped_unregistered = 0
        skipped_missing = 0
        for row in rows:
            source_missing = (
                str(row.get("MissingValueStatus") or "").strip().lower()
                == "source_missing"
            )
            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            local_code = _first_text(
                row,
                "SourceDiseaseCode",
                "source_disease_code",
                "DiseaseCode",
                "local_code",
                "Diseases",
            )
            row_source_id = _source_id_for_row(row, source_id)
            matches = self._resolve_series(
                country_code=country_code,
                source_id=row_source_id,
                local_code=local_code,
                local_label=label,
            )
            if not matches:
                skipped_unregistered += 1
                if row.get(value_field) is not None and str(row.get(value_field)).strip():
                    unregistered_rows.append(dict(row))
                continue
            raw_value = row.get(value_field)
            if raw_value is None or not str(raw_value).strip():
                if not source_missing:
                    skipped_missing += 1
                    continue
            selected.append(row)
        return RegistryRowSelection(
            rows=selected,
            skipped_unregistered=skipped_unregistered,
            skipped_missing=skipped_missing,
            unregistered_rows=tuple(unregistered_rows),
        )

    def build_observations(
        self,
        rows: list[dict[str, Any]],
        country_code: str,
        *,
        source_id: str | Mapping[str, str] | None = None,
        value_field: str = "Cases",
        geography_key: str | None = None,
    ) -> SeriesObservationBuildResult:
        observations: list[dict[str, Any]] = []
        skipped_unmatched = 0
        skipped_ambiguous = 0
        skipped_invalid = 0
        observation_by_key: dict[tuple[object, ...], dict[str, Any]] = {}

        for row in rows:
            report_time = _parse_report_time(row)
            value, suppressed = _parse_value(row.get(value_field))
            source_missing = (
                str(row.get("MissingValueStatus") or "").strip().lower()
                == "source_missing"
            )
            if report_time is None or (
                value is None and not suppressed and not source_missing
            ):
                skipped_invalid += 1
                continue

            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            local_code = _first_text(
                row,
                "SourceDiseaseCode",
                "source_disease_code",
                "DiseaseCode",
                "local_code",
                "Diseases",
            )
            row_source_id = _source_id_for_row(row, source_id)
            matches = self._resolve_series(
                country_code=country_code,
                source_id=row_source_id,
                local_code=local_code,
                local_label=label,
            )
            matches = [
                series for series in matches if _series_valid_at(series, report_time)
            ]
            if not matches:
                skipped_unmatched += 1
                continue
            if len(matches) != 1:
                skipped_ambiguous += 1
                continue

            series = matches[0]
            dimensions = _parse_dimensions(
                row.get("Dimensions") or row.get("dimensions")
            )
            if dimensions is None:
                skipped_invalid += 1
                continue
            dimension_key = _dimension_key(dimensions)
            resolved_geography = _geography_key(
                row, country_code, source_id=row_source_id
            )
            if (
                geography_key
                and row_source_id == "SRC_US_NNDSS"
                and geography_key != resolved_geography
            ):
                raise ValueError(
                    "Batch geography_key conflicts with SRC_US_NNDSS "
                    "ReportingArea: expected "
                    f"{resolved_geography!r}, received {geography_key!r}"
                )
            observation = {
                "time": report_time,
                "series_code": series["id"],
                "geography_key": geography_key or resolved_geography,
                "dimension_key": dimension_key,
                "dimensions": dimensions,
                "value": value,
                "unit": str(series.get("unit") or "count"),
                "suppressed": suppressed,
                "suppression_reason": (
                    "source_value_suppressed" if suppressed else None
                ),
                "quality_status": _quality_status(row),
                "raw_data": _json_safe(dict(row)),
                "metadata": {
                    "source_id": series["source_id"],
                    "local_code": local_code,
                    "local_label": label,
                    "measure": series["measure"],
                    "frequency": series["frequency"],
                    "rollup_policy": series["rollup_policy"],
                    "population_scope": _first_text(
                        row, "PopulationScope", "population_scope"
                    ),
                    "authoritative_revision": _allows_authoritative_revision(row),
                    "missing_value_status": (
                        "source_missing" if source_missing else None
                    ),
                    "allow_equal_quality_overwrite": (
                        _allows_equal_quality_overwrite(row)
                    ),
                },
            }
            identity = (
                observation["time"],
                observation["series_code"],
                observation["geography_key"],
                observation["dimension_key"],
            )
            previous = observation_by_key.get(identity)
            if previous is not None:
                comparable_fields = (
                    "value",
                    "unit",
                    "suppressed",
                    "suppression_reason",
                    "quality_status",
                    "dimensions",
                )
                if any(
                    previous.get(field) != observation.get(field)
                    for field in comparable_fields
                ):
                    raise ValueError(
                        "Conflicting source rows share a disease series observation "
                        f"identity: {identity}"
                    )
                # Upstream extracts can retain both a historic label and its
                # replacement for the same weekly observation. Preserve the
                # first deterministic provenance row when the actual fact is
                # identical; differing values still fail closed above.
                continue
            observation_by_key[identity] = observation
            observations.append(observation)

        return SeriesObservationBuildResult(
            observations=observations,
            skipped_unmatched=skipped_unmatched,
            skipped_ambiguous=skipped_ambiguous,
            skipped_invalid=skipped_invalid,
        )

    async def save_rows(
        self,
        db: AsyncSession,
        rows: list[dict[str, Any]],
        country_code: str,
        *,
        source_id: str | Mapping[str, str] | None = None,
        value_field: str = "Cases",
        geography_key: str | None = None,
        quality_policy: SeriesObservationQualityPolicy | None = None,
        jp_holding_profiles: dict | None = None,
    ) -> SeriesObservationSaveResult:
        policy = quality_policy or SeriesObservationQualityPolicy()
        if policy.registry_coverage == "legacy_only":
            quality_report = self.assess_quality(
                [],
                country_code=country_code,
                source_row_count=len(rows),
                policy=policy,
            )
            return SeriesObservationSaveResult(
                upserted=0,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=quality_report,
            )
        built = await asyncio.to_thread(
            self.build_observations,
            rows,
            country_code,
            source_id=source_id,
            value_field=value_field,
            geography_key=geography_key,
        )
        # Holding-series construction can insert or relocate facts. Acquire
        # the partition before it, including direct storage/backfill callers.
        await acquire_country_data_mutation_lock(db, country_code)
        holding_observations, holding_handled = await self._build_holding_observations(
            db,
            rows,
            country_code,
            source_id=source_id,
            value_field=value_field,
            geography_key=geography_key,
            jp_holding_profiles=jp_holding_profiles,
        )
        if holding_observations:
            built = SeriesObservationBuildResult(
                observations=[*built.observations, *holding_observations],
                skipped_unmatched=max(0, built.skipped_unmatched - holding_handled),
                skipped_ambiguous=built.skipped_ambiguous,
                skipped_invalid=built.skipped_invalid,
            )
        if not built.observations:
            quality_report = self.assess_quality(
                [],
                country_code=country_code,
                source_row_count=len(rows),
                skipped_unmatched=built.skipped_unmatched,
                skipped_ambiguous=built.skipped_ambiguous,
                skipped_invalid=built.skipped_invalid,
                policy=policy,
            )
            self.enforce_quality(quality_report, policy)
            return SeriesObservationSaveResult(
                upserted=0,
                skipped_unmatched=built.skipped_unmatched,
                skipped_ambiguous=built.skipped_ambiguous,
                skipped_invalid=built.skipped_invalid,
                skipped_registry_not_synced=0,
                quality_report=quality_report,
                holding_observations=0,
                publication_changes=() if not rows else None,
            )

        requested_codes = {row["series_code"] for row in built.observations}
        existing = await db.execute(
            select(DiseaseSurveillanceSeries.series_code).where(
                DiseaseSurveillanceSeries.series_code.in_(requested_codes)
            )
        )
        available_codes = set(existing.scalars())
        missing_codes = requested_codes - available_codes
        if missing_codes:
            from src.services.disease_ontology_sync_service import restore_missing_registry_series
            from src.core.logging import get_logger

            restored = await restore_missing_registry_series(
                db, self.ontology, missing_codes, country_code=country_code,
            )
            available_codes.update(restored)
            if restored:
                get_logger(__name__).warning(
                    "Disease source-series registry restored in ingestion transaction | country={} series={}",
                    country_code, sorted(restored),
                )
        observations = [
            row for row in built.observations if row["series_code"] in available_codes
        ]
        skipped_registry = len(built.observations) - len(observations)
        history = (
            await self._load_recent_history(db, observations, policy)
            if observations and policy.mode != "off"
            else []
        )
        quality_report = await asyncio.to_thread(
            self.assess_quality,
            observations,
            country_code=country_code,
            source_row_count=len(rows),
            existing_observations=history,
            skipped_unmatched=built.skipped_unmatched,
            skipped_ambiguous=built.skipped_ambiguous,
            skipped_invalid=built.skipped_invalid,
            skipped_registry_not_synced=skipped_registry,
            policy=policy,
        )
        # This executes after the legacy projection has been staged but before
        # either side of the dual write is committed. Raising here therefore
        # rolls back both stores instead of leaving a partially imported batch.
        self.enforce_quality(quality_report, policy)

        revision_time = datetime.now(timezone.utc)
        await _persist_observation_revisions(
            db,
            observations,
            recorded_at=revision_time,
            seed_current_state=True,
        )
        affected_rows = 0
        for offset in range(0, len(observations), _WRITE_BATCH_SIZE):
            batch = observations[offset : offset + _WRITE_BATCH_SIZE]
            # Build the insert against the underlying table.  Passing the ORM
            # class here makes SQLAlchemy interpret the physical ``metadata``
            # column as DeclarativeBase.metadata, which breaks bulk inserts.
            statement = pg_insert(DiseaseSeriesObservation.__table__)
            existing_quality_rank = case(
                (DiseaseSeriesObservation.quality_status == "final", 4),
                (DiseaseSeriesObservation.quality_status == "revised", 3),
                (DiseaseSeriesObservation.quality_status == "validated", 2),
                (DiseaseSeriesObservation.quality_status == "provisional", 1),
                else_=0,
            )
            incoming_quality_rank = case(
                (statement.excluded.quality_status == "final", 4),
                (statement.excluded.quality_status == "revised", 3),
                (statement.excluded.quality_status == "validated", 2),
                (statement.excluded.quality_status == "provisional", 1),
                else_=0,
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_disease_series_observation_identity",
                set_={
                    "dimensions": statement.excluded.dimensions,
                    "value": statement.excluded.value,
                    "unit": statement.excluded.unit,
                    "suppressed": statement.excluded.suppressed,
                    "suppression_reason": statement.excluded.suppression_reason,
                    "quality_status": statement.excluded.quality_status,
                    "raw_data": statement.excluded.raw_data,
                    "metadata": statement.excluded["metadata"],
                    # BaseModel audit columns are TIMESTAMP WITHOUT TIME ZONE;
                    # keep their value UTC-normalized but offset-naive.
                    "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
                },
                # A routine provisional/raw refresh must never destroy a final
                # or revised observation. Sources can opt into an intentional
                # correction by marking the row as an authoritative revision;
                # that same marker is consumed by the anomaly detector below.
                where=or_(
                    incoming_quality_rank > existing_quality_rank,
                    and_(
                        incoming_quality_rank == existing_quality_rank,
                        statement.excluded["metadata"].op("->>")(
                            "allow_equal_quality_overwrite"
                        )
                        == "true",
                    ),
                    statement.excluded["metadata"].op("->>")(
                        "authoritative_revision"
                    )
                    == "true",
                ),
            ).returning(DiseaseSeriesObservation.series_code)
            write_result = await db.execute(statement, batch)
            affected_rows += len(write_result.scalars().all())

        changed_observations: list[dict[str, Any]] = []
        await _persist_observation_revisions(
            db,
            observations,
            recorded_at=revision_time,
            seed_current_state=False,
            changed_observations=changed_observations,
        )

        return SeriesObservationSaveResult(
            upserted=affected_rows,
            skipped_unmatched=built.skipped_unmatched,
            skipped_ambiguous=built.skipped_ambiguous,
            skipped_invalid=built.skipped_invalid,
            skipped_registry_not_synced=skipped_registry,
            quality_report=quality_report,
            holding_observations=len(holding_observations),
            publication_changes=(
                observation_publication_changes(changed_observations)
                if not (missing_codes or skipped_registry or built.skipped_unmatched
                        or built.skipped_ambiguous or built.skipped_invalid)
                else None
            ),
        )

    def _jp_mixed_holding_profiles(
        self,
        rows: list[dict[str, Any]],
        country_code: str,
        source_id: str | Mapping[str, str] | None,
        value_field: str,
    ) -> dict[tuple, dict[str, dict[str, Any]]]:
        """Find JP categories whose current batch contains both surveillance types.

        The scan is in memory. A single-type batch must not issue an extra query.
        """

        if country_code.upper() != "JP":
            return {}
        profiles: dict[tuple, dict[str, dict[str, Any]]] = {}
        for row in rows:
            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            local_code = _first_text(
                row,
                "SourceDiseaseCode",
                "source_disease_code",
                "DiseaseCode",
                "local_code",
                "Diseases",
            )
            resolved_source = _source_id_for_row(row, source_id)
            if resolved_source != "SRC_JP_NIID":
                continue
            surveillance_type = _jp_surveillance_type(row)
            if surveillance_type not in {"all_case", "sentinel"}:
                continue
            if self._resolve_series(
                country_code=country_code,
                source_id=resolved_source,
                local_code=local_code,
                local_label=label,
            ):
                continue
            report_time = _parse_report_time(row)
            value, suppressed = _parse_value(row.get(value_field))
            source_missing = (
                str(row.get("MissingValueStatus") or "").strip().lower()
                == "source_missing"
            )
            if (
                report_time is None
                or (value is None and not suppressed and not source_missing)
                or not resolved_source
            ):
                continue
            source_code = local_code or (
                "label:"
                + hashlib.sha256(_normalized_identity(label).encode()).hexdigest()[:20]
            )
            definition_version = (
                _first_text(row, "DefinitionVersion", "definition_version")
                or "source-current"
            )
            identity = (resolved_source, source_code, definition_version)
            profiles.setdefault(identity, {}).setdefault(surveillance_type, row)
        return {
            identity: by_type
            for identity, by_type in profiles.items()
            if {"all_case", "sentinel"} <= set(by_type)
        }

    async def _load_holding_series(
        self, db: AsyncSession, series_code: str
    ) -> DiseaseSurveillanceSeries | None:
        return (
            await db.execute(
                select(DiseaseSurveillanceSeries).where(
                    DiseaseSurveillanceSeries.series_code == series_code
                )
            )
        ).scalar_one_or_none()

    async def _stored_jp_surveillance_counts(
        self, db: AsyncSession, series_code: str
    ) -> dict[str, int]:
        type_expr = _json_surveillance_type(DiseaseSeriesObservation.raw_data)
        rows = (
            await db.execute(
                select(type_expr, func.count())
                .where(DiseaseSeriesObservation.series_code == series_code)
                .group_by(type_expr)
            )
        ).all()
        counts: dict[str, int] = {}
        for label, count in rows:
            key = str(label or "").casefold()
            if key in {"all_case", "sentinel"}:
                counts[key] = counts.get(key, 0) + int(count)
        return counts

    async def _relocate_jp_surveillance_rows(
        self,
        db: AsyncSession,
        *,
        legacy_code: str,
        sibling_code: str,
        surveillance_type: str,
    ) -> None:
        """Move stored rows of one surveillance type onto the sibling series.

        The sibling series is flushed first so the series_code foreign key holds.
        Incoming rows are written later in this same transaction.
        """

        observation_type = _json_surveillance_type(DiseaseSeriesObservation.raw_data)
        await db.execute(
            update(DiseaseSeriesObservation)
            .where(
                DiseaseSeriesObservation.series_code == legacy_code,
                observation_type == surveillance_type,
            )
            .values(series_code=sibling_code)
        )
        revision_type = _json_surveillance_type(
            DiseaseSeriesObservationRevision.raw_data
        )
        await db.execute(
            update(DiseaseSeriesObservationRevision)
            .where(
                DiseaseSeriesObservationRevision.series_code == legacy_code,
                revision_type == surveillance_type,
            )
            .values(series_code=sibling_code)
        )

    async def _assign_jp_split_holding_series(
        self,
        db: AsyncSession,
        *,
        category: SourceDiseaseCategory,
        country_code: str,
        source_id: str,
        surveillance_type: str,
        profile_rows: Mapping[str, Mapping[str, Any]],
        splits: dict[int, dict[str, Any]],
    ) -> DiseaseSurveillanceSeries:
        """Route one mixed JP category without letting row order claim the legacy code."""

        split = splits.get(category.id)
        if split is None:
            legacy_code = _holding_series_code(country_code, category.category_key)
            legacy = await self._load_holding_series(db, legacy_code)
            metadata_type = ""
            counts: dict[str, int] = {}
            if legacy is not None:
                metadata_type = str(
                    (legacy.metadata_ or {}).get("surveillance_type") or ""
                )
                counts = await self._stored_jp_surveillance_counts(
                    db, legacy.series_code
                )
            bound_type = _bind_legacy_jp_surveillance_type(metadata_type, counts)
            bound_row = profile_rows[bound_type]
            if legacy is None:
                measure, basis, frequency = _holding_series_measure_profile(
                    bound_row, country_code=country_code, source_id=source_id
                )
                legacy = _new_dynamic_holding_series(
                    series_code=legacy_code,
                    category=category,
                    country_code=country_code,
                    row=bound_row,
                    measure=measure,
                    reporting_basis=basis,
                    frequency=frequency,
                    surveillance_type=bound_type,
                )
                db.add(legacy)
                await db.flush()
            else:
                _stamp_jp_holding_series(
                    legacy,
                    bound_row,
                    country_code=country_code,
                    source_id=source_id,
                    surveillance_type=bound_type,
                )
            split = {
                "bound_type": bound_type,
                "legacy": legacy,
                "by_type": {bound_type: legacy},
                "counts": counts,
                "relocated": set(),
            }
            splits[category.id] = split

        cached = split["by_type"].get(surveillance_type)
        if cached is not None:
            return cached
        sibling_code = _holding_series_code(
            country_code, category.category_key, surveillance_type
        )
        series = await self._load_holding_series(db, sibling_code)
        profile = profile_rows[surveillance_type]
        if series is None:
            measure, basis, frequency = _holding_series_measure_profile(
                profile, country_code=country_code, source_id=source_id
            )
            series = _new_dynamic_holding_series(
                series_code=sibling_code,
                category=category,
                country_code=country_code,
                row=profile,
                measure=measure,
                reporting_basis=basis,
                frequency=frequency,
                surveillance_type=surveillance_type,
            )
            db.add(series)
            await db.flush()
        else:
            _stamp_jp_holding_series(
                series,
                profile,
                country_code=country_code,
                source_id=source_id,
                surveillance_type=surveillance_type,
            )
        split["by_type"][surveillance_type] = series
        if (
            surveillance_type not in split["relocated"]
            and split["counts"].get(surveillance_type, 0) > 0
        ):
            await self._relocate_jp_surveillance_rows(
                db,
                legacy_code=split["legacy"].series_code,
                sibling_code=series.series_code,
                surveillance_type=surveillance_type,
            )
            split["relocated"].add(surveillance_type)
        return series

    async def _build_holding_observations(
        self,
        db: AsyncSession,
        rows: list[dict[str, Any]],
        country_code: str,
        *,
        source_id: str | Mapping[str, str] | None,
        value_field: str,
        geography_key: str | None,
        jp_holding_profiles: dict | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Retain valid source facts whose disease category is awaiting review.

        Mapping Registry discovery runs immediately before this method.  A
        dynamically-created holding series therefore uses the same stable
        source/category identity and points only at the non-projectable unmapped
        group.  Review releases can later interpret the already-stored facts;
        no source re-fetch or destructive observation rewrite is required.
        """

        observations: list[dict[str, Any]] = []
        observation_by_key: dict[tuple[object, ...], dict[str, Any]] = {}
        series_by_category: dict[int, DiseaseSurveillanceSeries] = {}
        categories_by_identity: dict[tuple, SourceDiseaseCategory | None] = {}
        jp_profiles = jp_holding_profiles if jp_holding_profiles is not None else self._jp_mixed_holding_profiles(
            rows, country_code, source_id, value_field
        )
        jp_splits: dict[int, dict[str, Any]] = {}
        handled = 0

        for row in rows:
            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            local_code = _first_text(
                row,
                "SourceDiseaseCode",
                "source_disease_code",
                "DiseaseCode",
                "local_code",
                "Diseases",
            )
            resolved_source = _source_id_for_row(row, source_id)
            matches = self._resolve_series(
                country_code=country_code,
                source_id=resolved_source,
                local_code=local_code,
                local_label=label,
            )
            if matches:
                continue

            report_time = _parse_report_time(row)
            value, suppressed = _parse_value(row.get(value_field))
            source_missing = (
                str(row.get("MissingValueStatus") or "").strip().lower()
                == "source_missing"
            )
            if (
                report_time is None
                or (value is None and not suppressed and not source_missing)
                or not resolved_source
            ):
                continue

            source_code = local_code or (
                "label:"
                + hashlib.sha256(_normalized_identity(label).encode()).hexdigest()[:20]
            )
            definition_version = (
                _first_text(row, "DefinitionVersion", "definition_version")
                or "source-current"
            )
            category_identity = (resolved_source, source_code, definition_version)
            if category_identity not in categories_by_identity:
                categories_by_identity[category_identity] = (
                    await db.execute(
                        select(SourceDiseaseCategory).where(
                            SourceDiseaseCategory.source_id == resolved_source,
                            SourceDiseaseCategory.source_code == source_code,
                            SourceDiseaseCategory.definition_version == definition_version,
                            SourceDiseaseCategory.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
            category = categories_by_identity[category_identity]
            if category is None:
                continue

            measure, reporting_basis, frequency = _holding_series_measure_profile(
                row, country_code=country_code, source_id=resolved_source
            )
            surveillance_type = (_first_text(row, "SurveillanceType") or "").casefold()
            if (
                category_identity in jp_profiles
                and surveillance_type in {"all_case", "sentinel"}
            ):
                series = await self._assign_jp_split_holding_series(
                    db,
                    category=category,
                    country_code=country_code,
                    source_id=resolved_source,
                    surveillance_type=surveillance_type,
                    profile_rows=jp_profiles[category_identity],
                    splits=jp_splits,
                )
            else:
                series = series_by_category.get(category.id)
                if series is None:
                    series_code = _holding_series_code(
                        country_code, category.category_key
                    )
                    series = await self._load_holding_series(db, series_code)
                    if series is None:
                        series = _new_dynamic_holding_series(
                            series_code=series_code,
                            category=category,
                            country_code=country_code,
                            row=row,
                            measure=measure,
                            reporting_basis=reporting_basis,
                            frequency=frequency,
                            surveillance_type=surveillance_type,
                        )
                        db.add(series)
                        await db.flush()
                    series_by_category[category.id] = series

                if (
                    country_code.upper() == "JP"
                    and resolved_source == "SRC_JP_NIID"
                    and surveillance_type in {"all_case", "sentinel"}
                    and (series.metadata_ or {}).get("dynamic_holding") is True
                ):
                    prior_type = str(
                        (series.metadata_ or {}).get("surveillance_type") or ""
                    ).casefold()
                    if prior_type and prior_type != surveillance_type:
                        raise ValueError(
                            "Japan IDWR surveillance type changed within one holding "
                            f"series: {series.series_code} ({prior_type} -> {surveillance_type})"
                        )
                    # Existing HOLD rows were created before the source's
                    # SurveillanceType field was reflected in series metadata.
                    _stamp_jp_holding_series(
                        series,
                        row,
                        country_code=country_code,
                        source_id=resolved_source,
                        surveillance_type=surveillance_type,
                    )

            dimensions = _parse_dimensions(row.get("Dimensions") or row.get("dimensions"))
            if dimensions is None:
                continue
            resolved_geography = geography_key or _geography_key(
                row, country_code, source_id=resolved_source
            )
            observation = {
                "time": report_time,
                "series_code": series.series_code,
                "geography_key": resolved_geography,
                "dimension_key": _dimension_key(dimensions),
                "dimensions": dimensions,
                "value": value,
                "unit": series.unit,
                "suppressed": suppressed,
                "suppression_reason": "source_value_suppressed" if suppressed else None,
                "quality_status": _quality_status(row),
                "raw_data": _json_safe(dict(row)),
                "metadata": {
                    "source_id": resolved_source,
                    "local_code": source_code,
                    "local_label": label,
                    "measure": series.metric_type,
                    "frequency": series.temporal_granularity,
                    "rollup_policy": "no_auto_rollup",
                    "dynamic_holding": True,
                    "source_category_id": category.id,
                    "population_scope": _first_text(
                        row, "PopulationScope", "population_scope"
                    ),
                    "authoritative_revision": _allows_authoritative_revision(row),
                    "allow_equal_quality_overwrite": _allows_equal_quality_overwrite(row),
                    "missing_value_status": (
                        "source_missing" if source_missing else None
                    ),
                },
            }
            identity = (
                observation["time"],
                observation["series_code"],
                observation["geography_key"],
                observation["dimension_key"],
            )
            previous = observation_by_key.get(identity)
            if previous is not None:
                comparable = (
                    "value",
                    "unit",
                    "suppressed",
                    "suppression_reason",
                    "quality_status",
                    "dimensions",
                )
                if any(previous.get(field) != observation.get(field) for field in comparable):
                    raise ValueError(
                        "Conflicting unmapped source rows share a holding-series "
                        f"observation identity: {identity}"
                    )
            else:
                observation_by_key[identity] = observation
                observations.append(observation)
            handled += 1

        return observations, handled

    @staticmethod
    def assess_quality(
        observations: Iterable[Mapping[str, Any]],
        *,
        country_code: str,
        source_row_count: int | None = None,
        existing_observations: Iterable[Mapping[str, Any]] = (),
        skipped_unmatched: int = 0,
        skipped_ambiguous: int = 0,
        skipped_invalid: int = 0,
        skipped_registry_not_synced: int = 0,
        policy: SeriesObservationQualityPolicy | None = None,
    ) -> SeriesObservationQualityReport:
        """Assess an incoming batch and its projected post-save timeline.

        The method is intentionally pure so crawlers, backfills, and tests can
        run exactly the same checks without writing to the database.
        """

        active_policy = policy or SeriesObservationQualityPolicy()
        incoming = [dict(row) for row in observations]
        history = [dict(row) for row in existing_observations]
        issues: list[SeriesObservationQualityIssue] = []

        if active_policy.mode != "off":
            issues.extend(
                _mapping_coverage_issues(
                    observation_count=len(incoming),
                    source_row_count=(
                        len(incoming)
                        if source_row_count is None
                        else int(source_row_count)
                    ),
                    skipped_unmatched=skipped_unmatched,
                    skipped_ambiguous=skipped_ambiguous,
                    skipped_invalid=skipped_invalid,
                    skipped_registry_not_synced=skipped_registry_not_synced,
                    policy=active_policy,
                )
            )
            issues.extend(
                _zero_tail_issues(incoming, history=history, policy=active_policy)
            )
            issues.extend(
                _single_series_revision_issues(
                    incoming, history=history, policy=active_policy
                )
            )
            issues.extend(
                _time_completeness_issues(
                    incoming, history=history, policy=active_policy
                )
            )

        issues.sort(
            key=lambda issue: (
                -_SEVERITY_RANK.get(issue.severity, -1),
                issue.code,
                issue.stage,
            )
        )
        return SeriesObservationQualityReport(
            country_code=str(country_code).strip().upper(),
            source_row_count=(
                len(incoming) if source_row_count is None else int(source_row_count)
            ),
            analyzed_observations=len(incoming),
            history_observations=len(history),
            issues=tuple(issues),
        )

    @staticmethod
    def enforce_quality(
        report: SeriesObservationQualityReport,
        policy: SeriesObservationQualityPolicy,
    ) -> None:
        if not policy.blocks(report):
            return
        if policy.mode == "quarantine":
            raise SeriesObservationQuarantinedError(report)
        raise SeriesObservationQualityError(report)

    @staticmethod
    async def _load_recent_history(
        db: AsyncSession,
        observations: list[dict[str, Any]],
        policy: SeriesObservationQualityPolicy,
    ) -> list[dict[str, Any]]:
        requested_codes = {row["series_code"] for row in observations}
        earliest = min(_as_utc(row["time"]) for row in observations)
        latest = max(_as_utc(row["time"]) for row in observations)
        lower_bound = earliest - timedelta(days=policy.history_lookback_days)
        # Quality checks compare identity, values and frequency. Historical raw
        # provenance is never consulted (revision overrides apply to incoming
        # rows), so do not transfer/decode millions of large JSON payloads here.
        result = await db.execute(
            select(
                DiseaseSeriesObservation.time,
                DiseaseSeriesObservation.series_code,
                DiseaseSeriesObservation.geography_key,
                DiseaseSeriesObservation.dimension_key,
                DiseaseSeriesObservation.value,
                DiseaseSeriesObservation.suppressed,
                DiseaseSeriesObservation.quality_status,
                DiseaseSeriesObservation.metadata_.label("metadata"),
            ).where(
                DiseaseSeriesObservation.series_code.in_(requested_codes),
                DiseaseSeriesObservation.time >= lower_bound,
                DiseaseSeriesObservation.time <= latest,
            )
        )
        return [dict(row) for row in result.mappings().all()]

    def _resolve_series(
        self,
        *,
        country_code: str,
        source_id: str | None,
        local_code: str | None,
        local_label: str | None,
    ) -> list[dict[str, Any]]:
        cache_key = (country_code, source_id, local_code, local_label)
        if cache_key in self._resolved_series_cache:
            return self._resolved_series_cache[cache_key]
        filters = {
            "country_code": country_code,
            "source_id": source_id,
            "local_code": local_code,
            "local_label": local_label,
        }
        matches = self.ontology.series_lookup(
            **{key: value for key, value in filters.items() if value}
        )
        if isinstance(matches, list) and matches:
            self._resolved_series_cache[cache_key] = matches
            return matches

        # Some source extracts omit one side of their code/label pair.  Retry
        # each independently while still refusing ambiguous matches.
        candidates: dict[str, dict[str, Any]] = {}
        for field, value in (("local_code", local_code), ("local_label", local_label)):
            if not value:
                continue
            fallback = self.ontology.series_lookup(
                source_id=source_id,
                country_code=country_code,
                **{field: value},
            )
            if isinstance(fallback, list):
                for item in fallback:
                    candidates[item["id"]] = item
        resolved = [candidates[key] for key in sorted(candidates)]
        self._resolved_series_cache[cache_key] = resolved
        return resolved


__all__ = [
    "RegistryRowSelection",
    "SeriesObservationBuildResult",
    "SeriesObservationQualityError",
    "SeriesObservationQualityIssue",
    "SeriesObservationQualityPolicy",
    "SeriesObservationQualityReport",
    "SeriesObservationQuarantinedError",
    "SeriesObservationSaveResult",
    "SeriesObservationStore",
]
