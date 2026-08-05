"""Persist source-series observations without collapsing canonical concepts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock
from src.domain import DiseaseSeriesObservation, DiseaseSurveillanceSeries
from src.ontology import DiseaseOntology, load_disease_ontology

_SUPPRESSED_VALUES = {"*", "suppressed", "suppression", "<5", "-"}
_WRITE_BATCH_SIZE = 500
_QUALITY_MODES = {"off", "report", "quarantine", "fail_closed"}
_REGISTRY_COVERAGE_MODES = {"auto", "required", "legacy_only"}
_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


@dataclass(frozen=True)
class SeriesObservationQualityIssue:
    """One machine-readable anomaly found before an observation batch is committed."""

    code: str
    severity: str
    stage: str
    message: str
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "details": _json_safe(self.details),
        }


@dataclass(frozen=True)
class SeriesObservationQualityReport:
    """Structured pre-save and projected-post-save quality assessment."""

    country_code: str
    source_row_count: int
    analyzed_observations: int
    history_observations: int
    issues: tuple[SeriesObservationQualityIssue, ...] = ()

    @property
    def highest_severity(self) -> str | None:
        if not self.issues:
            return None
        return max(
            (issue.severity for issue in self.issues),
            key=lambda severity: _SEVERITY_RANK.get(severity, -1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "source_row_count": self.source_row_count,
            "analyzed_observations": self.analyzed_observations,
            "history_observations": self.history_observations,
            "highest_severity": self.highest_severity,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class SeriesObservationQualityPolicy:
    """Configurable guardrails for source-series imports.

    ``report`` never blocks a write. ``quarantine`` blocks only critical,
    high-confidence batch anomalies. ``fail_closed`` additionally blocks
    error-level anomalies. ``registry_coverage=required`` promotes any mapping
    or Registry synchronization loss to critical even for a one-row batch;
    ``legacy_only`` is the explicit exemption for sources outside Registry
    scope. Warnings such as an isolated time gap remain report-only in every
    mode so legitimate sparse and zero-valued series are not rejected.
    """

    mode: str = "quarantine"
    minimum_cross_series: int = 5
    critical_zero_tail_periods: int = 3
    coverage_drop_ratio: float = 0.5
    low_mapping_coverage_ratio: float = 0.5
    history_lookback_days: int = 740
    max_examples: int = 20
    registry_coverage: str = "auto"
    material_revision_drop_ratio: float = 0.8

    def __post_init__(self) -> None:
        normalized_mode = str(self.mode).strip().casefold().replace("-", "_")
        if normalized_mode not in _QUALITY_MODES:
            raise ValueError(
                "Series observation quality mode must be one of: "
                + ", ".join(sorted(_QUALITY_MODES))
            )
        object.__setattr__(self, "mode", normalized_mode)
        normalized_coverage = (
            str(self.registry_coverage).strip().casefold().replace("-", "_")
        )
        if normalized_coverage not in _REGISTRY_COVERAGE_MODES:
            raise ValueError(
                "Registry coverage must be one of: "
                + ", ".join(sorted(_REGISTRY_COVERAGE_MODES))
            )
        object.__setattr__(self, "registry_coverage", normalized_coverage)
        if self.minimum_cross_series < 2:
            raise ValueError("minimum_cross_series must be at least 2")
        if self.critical_zero_tail_periods < 2:
            raise ValueError("critical_zero_tail_periods must be at least 2")
        if not 0 < self.coverage_drop_ratio < 1:
            raise ValueError("coverage_drop_ratio must be between 0 and 1")
        if not 0 < self.low_mapping_coverage_ratio < 1:
            raise ValueError("low_mapping_coverage_ratio must be between 0 and 1")
        if self.history_lookback_days < 0:
            raise ValueError("history_lookback_days cannot be negative")
        if self.max_examples < 1:
            raise ValueError("max_examples must be positive")
        if not 0 < self.material_revision_drop_ratio < 1:
            raise ValueError("material_revision_drop_ratio must be between 0 and 1")

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | None
    ) -> "SeriesObservationQualityPolicy":
        raw = dict(value or {})
        allowed = {
            "mode",
            "minimum_cross_series",
            "critical_zero_tail_periods",
            "coverage_drop_ratio",
            "low_mapping_coverage_ratio",
            "history_lookback_days",
            "max_examples",
            "registry_coverage",
            "material_revision_drop_ratio",
        }
        selected = {key: raw[key] for key in allowed if key in raw}
        for key in {
            "minimum_cross_series",
            "critical_zero_tail_periods",
            "history_lookback_days",
            "max_examples",
        }:
            if key in selected:
                selected[key] = int(selected[key])
        for key in {
            "coverage_drop_ratio",
            "low_mapping_coverage_ratio",
            "material_revision_drop_ratio",
        }:
            if key in selected:
                selected[key] = float(selected[key])
        return cls(**selected)

    def blocks(self, report: SeriesObservationQualityReport) -> bool:
        if self.mode in {"off", "report"}:
            return False
        threshold = 3 if self.mode == "quarantine" else 2
        return any(
            _SEVERITY_RANK.get(issue.severity, -1) >= threshold
            for issue in report.issues
        )


class SeriesObservationQualityError(RuntimeError):
    """Raised when a fail-closed observation quality policy rejects a batch."""

    def __init__(self, report: SeriesObservationQualityReport) -> None:
        self.report = report
        payload = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
        super().__init__(f"Disease series observation quality gate failed: {payload}")


class SeriesObservationQuarantinedError(SeriesObservationQualityError):
    """Raised when a critical batch is quarantined before transaction commit."""


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


@dataclass(frozen=True)
class RegistryRowSelection:
    """Rows eligible for an explicitly partial Registry dual write."""

    rows: list[dict[str, Any]]
    skipped_unregistered: int
    skipped_missing: int


class SeriesObservationStore:
    """Resolve raw rows to ontology series and upsert their natural fact grain."""

    def __init__(self, ontology: DiseaseOntology | None = None) -> None:
        self.ontology = ontology or load_disease_ontology()

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
        skipped_unregistered = 0
        skipped_missing = 0
        for row in rows:
            label = _first_text(
                row,
                "RawDiseaseLabel",
                "Disease",
                "DiseasesCN",
                "Diseases",
                "local_label",
            )
            local_code = _first_text(row, "DiseaseCode", "local_code", "Diseases")
            row_source_id = _source_id_for_row(row, source_id)
            matches = self._resolve_series(
                country_code=country_code,
                source_id=row_source_id,
                local_code=local_code,
                local_label=label,
            )
            if not matches:
                skipped_unregistered += 1
                continue
            raw_value = row.get(value_field)
            if raw_value is None or not str(raw_value).strip():
                skipped_missing += 1
                continue
            selected.append(row)
        return RegistryRowSelection(
            rows=selected,
            skipped_unregistered=skipped_unregistered,
            skipped_missing=skipped_missing,
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
            if report_time is None or (value is None and not suppressed):
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
            local_code = _first_text(row, "DiseaseCode", "local_code", "Diseases")
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
                if previous != observation:
                    raise ValueError(
                        "Conflicting source rows share a disease series observation "
                        f"identity: {identity}"
                    )
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
        built = self.build_observations(
            rows,
            country_code,
            source_id=source_id,
            value_field=value_field,
            geography_key=geography_key,
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
            )

        await acquire_disease_data_mutation_lock(db)
        requested_codes = {row["series_code"] for row in built.observations}
        existing = await db.execute(
            select(DiseaseSurveillanceSeries.series_code).where(
                DiseaseSurveillanceSeries.series_code.in_(requested_codes)
            )
        )
        available_codes = set(existing.scalars())
        observations = [
            row for row in built.observations if row["series_code"] in available_codes
        ]
        skipped_registry = len(built.observations) - len(observations)
        history = (
            await self._load_recent_history(db, observations, policy)
            if observations and policy.mode != "off"
            else []
        )
        quality_report = self.assess_quality(
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

        affected_rows = 0
        for offset in range(0, len(observations), _WRITE_BATCH_SIZE):
            batch = observations[offset : offset + _WRITE_BATCH_SIZE]
            # Build the insert against the underlying table.  Passing the ORM
            # class here makes SQLAlchemy interpret the physical ``metadata``
            # column as DeclarativeBase.metadata, which breaks bulk inserts.
            statement = pg_insert(DiseaseSeriesObservation.__table__).values(batch)
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
                    incoming_quality_rank >= existing_quality_rank,
                    statement.excluded["metadata"].op("->>")(
                        "authoritative_revision"
                    )
                    == "true",
                ),
            ).returning(DiseaseSeriesObservation.series_code)
            write_result = await db.execute(statement)
            affected_rows += len(write_result.scalars().all())

        return SeriesObservationSaveResult(
            upserted=affected_rows,
            skipped_unmatched=built.skipped_unmatched,
            skipped_ambiguous=built.skipped_ambiguous,
            skipped_invalid=built.skipped_invalid,
            skipped_registry_not_synced=skipped_registry,
            quality_report=quality_report,
        )

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
        result = await db.execute(
            select(
                DiseaseSeriesObservation.time,
                DiseaseSeriesObservation.series_code,
                DiseaseSeriesObservation.geography_key,
                DiseaseSeriesObservation.dimension_key,
                DiseaseSeriesObservation.value,
                DiseaseSeriesObservation.suppressed,
                DiseaseSeriesObservation.quality_status,
                DiseaseSeriesObservation.raw_data,
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
        return [candidates[key] for key in sorted(candidates)]


def _mapping_coverage_issues(
    *,
    observation_count: int,
    source_row_count: int,
    skipped_unmatched: int,
    skipped_ambiguous: int,
    skipped_invalid: int,
    skipped_registry_not_synced: int,
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    if policy.registry_coverage == "legacy_only":
        return []
    if policy.registry_coverage == "required":
        required_failures = {
            "skipped_unmatched": skipped_unmatched,
            "skipped_ambiguous": skipped_ambiguous,
            "skipped_invalid": skipped_invalid,
            "skipped_registry_not_synced": skipped_registry_not_synced,
        }
        if source_row_count > 0 and (
            observation_count == 0 or any(required_failures.values())
        ):
            return [
                SeriesObservationQualityIssue(
                    code="required_registry_coverage_incomplete",
                    severity="critical",
                    stage="pre_save",
                    message=(
                        "A source declared as Registry-covered did not resolve and "
                        "stage every source row; the dual write is fail-closed."
                    ),
                    details={
                        "source_rows": source_row_count,
                        "resolved_observations": observation_count,
                        **required_failures,
                    },
                )
            ]
    if source_row_count < policy.minimum_cross_series:
        return []
    usable_ratio = observation_count / source_row_count if source_row_count else 1.0
    if usable_ratio >= policy.low_mapping_coverage_ratio:
        return []
    return [
        SeriesObservationQualityIssue(
            code="batch_mapping_coverage_low",
            severity="warning",
            stage="pre_save",
            message=(
                "Fewer than the configured share of source rows resolved to a "
                "synced surveillance series."
            ),
            details={
                "source_rows": source_row_count,
                "resolved_observations": observation_count,
                "resolved_ratio": round(usable_ratio, 6),
                "minimum_ratio": policy.low_mapping_coverage_ratio,
                "skipped_unmatched": skipped_unmatched,
                "skipped_ambiguous": skipped_ambiguous,
                "skipped_invalid": skipped_invalid,
                "skipped_registry_not_synced": skipped_registry_not_synced,
            },
        )
    ]


def _zero_tail_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Find implausible all-zero tails without flagging isolated series zeros."""

    issues: list[SeriesObservationQualityIssue] = []
    incoming_cohorts = _cohort_period_rows(incoming)
    history_cohorts = _cohort_period_rows(
        history,
        frequency_by_series=_frequency_by_series(incoming),
    )

    for cohort_key, rows_by_time in incoming_cohorts.items():
        frequency = cohort_key[0]
        all_zero_times = {
            report_time
            for report_time, period_rows in rows_by_time.items()
            if _period_is_cross_series_zero(period_rows, policy.minimum_cross_series)
        }
        if not all_zero_times:
            continue

        ordered_times = sorted(rows_by_time)
        zero_tail: list[datetime] = []
        for report_time in reversed(ordered_times):
            if report_time not in all_zero_times:
                break
            if zero_tail and not _periods_are_consecutive(
                report_time, zero_tail[-1], frequency
            ):
                break
            zero_tail.append(report_time)
        zero_tail.reverse()
        if not zero_tail:
            continue

        latest = zero_tail[-1]
        latest_rows = rows_by_time[latest]
        series_codes = sorted({str(row["series_code"]) for row in latest_rows})
        details = {
            "frequency": frequency,
            "geography_key": cohort_key[1],
            "dimension_key": cohort_key[2],
            "unit": cohort_key[3],
            "tail_period_count": len(zero_tail),
            "tail_start": zero_tail[0].isoformat(),
            "tail_end": latest.isoformat(),
            "series_count": len(series_codes),
            "series_examples": series_codes[: policy.max_examples],
        }

        history_by_time = history_cohorts.get(cohort_key, {})
        overwritten_rows = history_by_time.get(latest, [])
        overwritten = {
            str(row["series_code"]): row
            for row in overwritten_rows
            if row.get("value") is not None and not row.get("suppressed")
        }
        overwritten_positive = sorted(
            code
            for code in series_codes
            if code in overwritten and float(overwritten[code]["value"]) > 0
        )
        if overwritten_positive:
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_zero_overwrites_positive",
                    severity="critical",
                    stage="pre_save",
                    message=(
                        "An all-zero multi-disease period would overwrite existing "
                        "positive observations."
                    ),
                    details={
                        **details,
                        "positive_overwrite_count": len(overwritten_positive),
                        "positive_overwrite_examples": overwritten_positive[
                            : policy.max_examples
                        ],
                    },
                )
            )
            continue

        # "Sudden" requires a baseline. Prefer the period immediately before
        # the zero tail from the same source batch, then fall back to stored
        # history. This keeps a long run of perfectly legitimate rare-disease
        # zeros reportable without quarantining it merely for being zero.
        prior_time = _previous_period(zero_tail[0], frequency)
        prior_rows = rows_by_time.get(prior_time, []) if prior_time else []
        prior_stage = "incoming_batch"
        if not prior_rows and prior_time:
            prior_rows = history_by_time.get(prior_time, [])
            prior_stage = "stored_history"
        prior_by_series = {
            str(row["series_code"]): row
            for row in prior_rows
            if row.get("value") is not None and not row.get("suppressed")
        }
        overlapping = [code for code in series_codes if code in prior_by_series]
        prior_positive = [
            code for code in overlapping if float(prior_by_series[code]["value"]) > 0
        ]
        has_positive_baseline = (
            len(overlapping) >= policy.minimum_cross_series and bool(prior_positive)
        )
        baseline_details = (
            {
                "prior_period": prior_time.isoformat(),
                "prior_stage": prior_stage,
                "prior_overlap_count": len(overlapping),
                "prior_positive_count": len(prior_positive),
                "prior_positive_examples": prior_positive[: policy.max_examples],
            }
            if prior_time and overlapping
            else {}
        )

        if len(zero_tail) >= policy.critical_zero_tail_periods:
            issues.append(
                SeriesObservationQualityIssue(
                    code=(
                        "cross_series_all_zero_tail"
                        if has_positive_baseline
                        else "cross_series_all_zero_tail_unverified"
                    ),
                    severity="critical" if has_positive_baseline else "error",
                    stage="pre_save",
                    message=(
                        "A multi-period, multi-disease zero tail follows a positive "
                        "baseline and is likely an upstream missing-to-zero failure."
                        if has_positive_baseline
                        else "A multi-period, multi-disease batch is all zero, but "
                        "no adjacent positive baseline is available; source "
                        "verification is required before fail-closed imports."
                    ),
                    details={**details, **baseline_details},
                )
            )
        elif has_positive_baseline:
            issues.append(
                SeriesObservationQualityIssue(
                    code="sudden_cross_series_all_zero_period",
                    severity="error",
                    stage="pre_save",
                    message=(
                        "Many disease series simultaneously changed from a prior "
                        "period containing positives to all zero."
                    ),
                    details={**details, **baseline_details},
                )
            )
        elif len(zero_tail) >= 2:
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_all_zero_tail_short",
                    severity="error",
                    stage="pre_save",
                    message=(
                        "Several consecutive periods are zero across many disease "
                        "series and require source verification."
                    ),
                    details=details,
                )
            )
        else:
            # A single period is report-only. In particular, a legitimate zero
            # for one disease can never reach the cross-series threshold here.
            issues.append(
                SeriesObservationQualityIssue(
                    code="cross_series_all_zero_period",
                    severity="warning",
                    stage="pre_save",
                    message=(
                        "One period is zero across many disease series; retained as "
                        "a warning because a single all-zero period can be valid."
                    ),
                    details=details,
                )
            )

    return issues


def _single_series_revision_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Reject destructive same-identity revisions unless explicitly authorized.

    Period-to-period disease counts can legitimately fall to zero. The high
    confidence failure mode is narrower: a refresh overwriting an already
    stored positive value at the same natural fact identity with zero or a
    material retreat. That catches parser/source regressions without inventing
    epidemiological constraints.
    """

    history_by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in history:
        try:
            identity = (
                _as_utc(row["time"]),
                str(row["series_code"]),
                str(row.get("geography_key") or ""),
                str(row.get("dimension_key") or "all"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        history_by_identity[identity] = row

    issues: list[SeriesObservationQualityIssue] = []
    for row in incoming:
        identity = (
            _as_utc(row["time"]),
            str(row["series_code"]),
            str(row.get("geography_key") or ""),
            str(row.get("dimension_key") or "all"),
        )
        previous = history_by_identity.get(identity)
        if previous is None or row.get("suppressed") or previous.get("suppressed"):
            continue
        if _observation_allows_authoritative_revision(row):
            continue
        try:
            old_value = float(previous["value"])
            new_value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(old_value) or not math.isfinite(new_value) or old_value <= 0:
            continue

        drop_ratio = (old_value - new_value) / old_value
        if new_value == 0:
            code = "single_series_positive_overwritten_by_zero"
            message = (
                "A source refresh would overwrite a stored positive observation "
                "with zero without explicit correction semantics."
            )
        elif drop_ratio >= policy.material_revision_drop_ratio:
            code = "single_series_material_revision_retreat"
            message = (
                "A source refresh would materially reduce a stored observation "
                "without explicit correction semantics."
            )
        else:
            continue
        issues.append(
            SeriesObservationQualityIssue(
                code=code,
                severity="critical",
                stage="pre_save",
                message=message,
                details={
                    "series_code": identity[1],
                    "time": identity[0].isoformat(),
                    "geography_key": identity[2],
                    "dimension_key": identity[3],
                    "stored_value": old_value,
                    "incoming_value": new_value,
                    "drop_ratio": round(drop_ratio, 6),
                    "required_override": "authoritative_revision",
                },
            )
        )
    return issues


def _time_completeness_issues(
    incoming: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]],
    policy: SeriesObservationQualityPolicy,
) -> list[SeriesObservationQualityIssue]:
    """Report inferred batch gaps and gaps in the projected stored timeline."""

    if not incoming:
        return []
    issues: list[SeriesObservationQualityIssue] = []
    frequency_by_series = _frequency_by_series(incoming)
    cohorts = _cohort_period_rows(incoming)
    missing_batch_period_examples: list[dict[str, Any]] = []
    coverage_drop_examples: list[dict[str, Any]] = []
    missing_batch_period_count = 0
    coverage_drop_period_count = 0

    for cohort_key, rows_by_time in cohorts.items():
        frequency = cohort_key[0]
        if frequency not in {"daily", "weekly", "monthly", "quarterly", "annual"}:
            continue
        ordered = sorted(rows_by_time)
        if len(ordered) < 2:
            continue
        expected = _period_range(ordered[0], ordered[-1], frequency)
        missing = [item for item in expected if item not in rows_by_time]
        missing_batch_period_count += len(missing)
        remaining_examples = policy.max_examples - len(missing_batch_period_examples)
        for report_time in missing[: max(0, remaining_examples)]:
            missing_batch_period_examples.append(
                {
                    "frequency": frequency,
                    "geography_key": cohort_key[1],
                    "dimension_key": cohort_key[2],
                    "time": report_time.isoformat(),
                }
            )

        series_counts = {
            report_time: len({str(row["series_code"]) for row in period_rows})
            for report_time, period_rows in rows_by_time.items()
        }
        maximum = max(series_counts.values(), default=0)
        if maximum >= policy.minimum_cross_series and len(series_counts) >= 2:
            cutoff = maximum * (1 - policy.coverage_drop_ratio)
            for report_time, count in sorted(series_counts.items()):
                if count <= cutoff:
                    coverage_drop_period_count += 1
                    if len(coverage_drop_examples) < policy.max_examples:
                        coverage_drop_examples.append(
                            {
                                "frequency": frequency,
                                "geography_key": cohort_key[1],
                                "dimension_key": cohort_key[2],
                                "time": report_time.isoformat(),
                                "series_count": count,
                                "maximum_series_count": maximum,
                            }
                        )

    if missing_batch_period_examples:
        issues.append(
            SeriesObservationQualityIssue(
                code="incoming_batch_period_gap",
                severity="warning",
                stage="pre_save",
                message=(
                    "The incoming batch has inferred calendar periods with no "
                    "observations between its first and last period."
                ),
                details={
                    "missing_period_count": missing_batch_period_count,
                    "examples": missing_batch_period_examples,
                    "inference_note": (
                        "Expected periods are inferred from series frequency; pass "
                        "a source-specific review for intentionally sparse extracts."
                    ),
                },
            )
        )
    if coverage_drop_examples:
        issues.append(
            SeriesObservationQualityIssue(
                code="incoming_batch_series_coverage_drop",
                severity="warning",
                stage="pre_save",
                message=(
                    "One or more periods contain substantially fewer disease series "
                    "than another period in the same batch."
                ),
                details={
                    "period_count": coverage_drop_period_count,
                    "configured_drop_ratio": policy.coverage_drop_ratio,
                    "examples": coverage_drop_examples,
                },
            )
        )

    projected_by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in [*history, *incoming]:
        try:
            identity = (
                _as_utc(row["time"]),
                str(row["series_code"]),
                str(row.get("geography_key") or ""),
                str(row.get("dimension_key") or "all"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        projected_by_identity[identity] = row

    incoming_times = [_as_utc(row["time"]) for row in incoming]
    window_start, window_end = min(incoming_times), max(incoming_times)
    projected_series: dict[tuple[str, str, str], set[datetime]] = {}
    for identity in projected_by_identity:
        report_time, series_code, geography_key, dimension_key = identity
        if window_start <= report_time <= window_end:
            projected_series.setdefault(
                (series_code, geography_key, dimension_key), set()
            ).add(report_time)

    gap_examples: list[dict[str, Any]] = []
    total_gap_count = 0
    affected_series: set[str] = set()
    for (series_code, geography_key, dimension_key), times in projected_series.items():
        frequency = frequency_by_series.get(series_code, "unknown")
        if frequency not in {"daily", "weekly", "monthly", "quarterly", "annual"}:
            continue
        ordered = sorted(times)
        if len(ordered) < 2:
            continue
        missing = [
            item
            for item in _period_range(ordered[0], ordered[-1], frequency)
            if item not in times
        ]
        if not missing:
            continue
        affected_series.add(series_code)
        total_gap_count += len(missing)
        for report_time in missing:
            if len(gap_examples) >= policy.max_examples:
                break
            gap_examples.append(
                {
                    "series_code": series_code,
                    "frequency": frequency,
                    "geography_key": geography_key,
                    "dimension_key": dimension_key,
                    "time": report_time.isoformat(),
                }
            )

    if total_gap_count:
        issues.append(
            SeriesObservationQualityIssue(
                code="projected_series_time_gap",
                severity="warning",
                stage="projected_post_save",
                message=(
                    "The timeline that would exist after this upsert still contains "
                    "one or more inferred series gaps."
                ),
                details={
                    "affected_series_count": len(affected_series),
                    "missing_period_count": total_gap_count,
                    "series_examples": sorted(affected_series)[: policy.max_examples],
                    "gap_examples": gap_examples,
                },
            )
        )
    return issues


def _frequency_by_series(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in observations:
        series_code = str(row.get("series_code") or "")
        metadata = row.get("metadata") or row.get("metadata_") or {}
        frequency = (
            str(metadata.get("frequency") or "unknown").strip().casefold()
            if isinstance(metadata, Mapping)
            else "unknown"
        )
        if series_code:
            result[series_code] = frequency
    return result


def _cohort_period_rows(
    observations: Iterable[Mapping[str, Any]],
    *,
    frequency_by_series: Mapping[str, str] | None = None,
) -> dict[tuple[str, str, str, str], dict[datetime, list[dict[str, Any]]]]:
    rows = [dict(row) for row in observations]
    frequencies = dict(frequency_by_series or _frequency_by_series(rows))
    cohorts: dict[tuple[str, str, str, str], dict[datetime, list[dict[str, Any]]]] = {}
    for row in rows:
        series_code = str(row.get("series_code") or "")
        if not series_code or row.get("time") is None:
            continue
        metadata = row.get("metadata") or row.get("metadata_") or {}
        frequency = frequencies.get(series_code)
        if not frequency and isinstance(metadata, Mapping):
            frequency = str(metadata.get("frequency") or "unknown").casefold()
        cohort_key = (
            frequency or "unknown",
            str(row.get("geography_key") or ""),
            str(row.get("dimension_key") or "all"),
            str(row.get("unit") or "count"),
        )
        cohorts.setdefault(cohort_key, {}).setdefault(_as_utc(row["time"]), []).append(
            row
        )
    return cohorts


def _period_is_cross_series_zero(
    rows: list[dict[str, Any]], minimum_cross_series: int
) -> bool:
    series_codes: set[str] = set()
    for row in rows:
        if row.get("suppressed") or row.get("value") is None:
            return False
        try:
            if float(row["value"]) != 0:
                return False
        except (TypeError, ValueError):
            return False
        series_codes.add(str(row.get("series_code") or ""))
    series_codes.discard("")
    return len(series_codes) >= minimum_cross_series


def _as_utc(value: object) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if not isinstance(value, datetime):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _advance_period(value: datetime, frequency: str) -> datetime | None:
    if frequency == "daily":
        return value + timedelta(days=1)
    if frequency == "weekly":
        return value + timedelta(days=7)
    if frequency in {"monthly", "quarterly"}:
        step = 1 if frequency == "monthly" else 3
        zero_based = value.month - 1 + step
        year = value.year + zero_based // 12
        month = zero_based % 12 + 1
        day = min(value.day, _days_in_month(year, month))
        return value.replace(year=year, month=month, day=day)
    if frequency == "annual":
        try:
            return value.replace(year=value.year + 1)
        except ValueError:
            return value.replace(year=value.year + 1, month=2, day=28)
    return None


def _previous_period(value: datetime, frequency: str) -> datetime | None:
    if frequency == "daily":
        return value - timedelta(days=1)
    if frequency == "weekly":
        return value - timedelta(days=7)
    if frequency in {"monthly", "quarterly"}:
        step = 1 if frequency == "monthly" else 3
        zero_based = value.month - 1 - step
        year = value.year + zero_based // 12
        month = zero_based % 12 + 1
        day = min(value.day, _days_in_month(year, month))
        return value.replace(year=year, month=month, day=day)
    if frequency == "annual":
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            return value.replace(year=value.year - 1, month=2, day=28)
    return None


def _periods_are_consecutive(
    earlier: datetime, later: datetime, frequency: str
) -> bool:
    expected = _advance_period(earlier, frequency)
    return expected == later if expected is not None else False


def _period_range(start: datetime, end: datetime, frequency: str) -> list[datetime]:
    result = [start]
    current = start
    # Hard cap protects the quality reporter from malformed century-spanning
    # input while still covering every supported surveillance history.
    for _ in range(5000):
        if current >= end:
            break
        following = _advance_period(current, frequency)
        if following is None or following <= current or following > end:
            break
        result.append(following)
        current = following
    return result


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    return (following - timedelta(days=1)).day


def _parse_report_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("Date") or row.get("date") or row.get("time")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value:
        text = str(value).strip()
        for parser in (datetime.fromisoformat,):
            try:
                parsed = parser(text.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

    mmwr_year = _first_text(row, "MMWRYear", "Current MMWR Year")
    mmwr_week = _first_text(row, "MMWRWeek", "MMWR WEEK")
    if mmwr_year and mmwr_week:
        try:
            year = int(float(mmwr_year))
            week = int(float(mmwr_week))
            if not 1 <= week <= 53:
                return None
            return datetime.combine(
                _mmwr_week_end_date(year, week), time.min, tzinfo=timezone.utc
            )
        except (OverflowError, TypeError, ValueError):
            return None

    try:
        year = int(row.get("Year"))
        month = int(row.get("Month"))
        return datetime.combine(
            datetime(year, month, 1).date(), time.min, tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _mmwr_week_end_date(year: int, week: int) -> date:
    """Return the Sunday ending a Japanese IDWR epidemiological week.

    IDWR weeks run Monday through Sunday and follow ISO week-year numbering.
    In particular, 2015 week 53 ends on 2016-01-03 and must not collide with
    2016 week 1, which ends on 2016-01-10.
    """

    return date.fromisocalendar(year, week, 7)


def _parse_value(value: object) -> tuple[float | None, bool]:
    text = "" if value is None else str(value).strip()
    if text.casefold() in _SUPPRESSED_VALUES:
        return None, True
    if not text:
        return None, False
    try:
        numeric = float(text.replace(",", ""))
        return (numeric, False) if math.isfinite(numeric) else (None, False)
    except ValueError:
        return None, False


def _first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _dimension_key(dimensions: dict[str, Any]) -> str:
    if not dimensions:
        return "all"
    canonical = json.dumps(
        dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_dimensions(value: object) -> dict[str, Any] | None:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return _json_safe(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _json_safe(parsed) if isinstance(parsed, dict) else None
    return None


def _source_id_for_row(
    row: Mapping[str, Any], source_id: str | Mapping[str, str] | None
) -> str | None:
    if not isinstance(source_id, Mapping):
        return source_id
    source_name = _first_text(dict(row), "Source", "source", "DataSource")
    if not source_name:
        return None
    normalized = source_name.casefold().strip()
    return next(
        (
            registry_id
            for configured_name, registry_id in source_id.items()
            if str(configured_name).casefold().strip() == normalized
        ),
        None,
    )


def _geography_key(
    row: dict[str, Any], country_code: str, *, source_id: str | None = None
) -> str:
    explicit = _first_text(row, "GeographyKey", "geography_key")
    reporting_area = _first_text(
        row, "ReportingArea", "Reporting Area", "states", "Province"
    )
    normalized_area = (reporting_area or "").casefold()
    if source_id == "SRC_US_NNDSS":
        if normalized_area == "total":
            expected = "source:SRC_US_NNDSS:reporting-area:total"
        elif normalized_area in {
            "us residents",
            "u.s. residents",
            "united states residents",
        }:
            expected = "country:US:national"
        else:
            raise ValueError(
                "Unsupported SRC_US_NNDSS ReportingArea; expected TOTAL or "
                f"US RESIDENTS, received {reporting_area!r}"
            )
        if explicit and explicit != expected:
            raise ValueError(
                "Explicit geography_key conflicts with SRC_US_NNDSS "
                f"ReportingArea: expected {expected!r}, received {explicit!r}"
            )
        return explicit or expected
    if explicit:
        return explicit
    national_aliases = {
        "total",
        "national",
        "総数",
        "全国",
    }
    if not reporting_area or normalized_area in national_aliases:
        return f"country:{country_code.upper()}:national"
    geocode = _first_text(row, "Geocode", "geocode")
    identifier = geocode or reporting_area.casefold().replace(" ", "-")
    return f"country:{country_code.upper()}:source-area:{identifier}"


def _series_valid_at(series: dict[str, Any], report_time: datetime) -> bool:
    if series["status"] not in {"active", "historical"}:
        return False
    report_date = report_time.date()
    valid_from = series.get("valid_from")
    valid_to = series.get("valid_to")
    return not (
        (valid_from and report_date < datetime.fromisoformat(valid_from).date())
        or (valid_to and report_date > datetime.fromisoformat(valid_to).date())
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _quality_status(row: dict[str, Any]) -> str:
    status = " ".join(
        str(row.get(key) or "")
        for key in ("DatasetStatus", "IsProvisional", "UpdateMode")
    ).casefold()
    if "final" in status:
        return "final"
    if any(marker in status for marker in ("revised", "revision", "corrected")):
        return "revised"
    if any(marker in status for marker in ("provisional", "preliminary", "true")):
        return "provisional"
    return "raw"


def _allows_authoritative_revision(row: Mapping[str, Any]) -> bool:
    explicit = row.get("AuthoritativeRevision", row.get("authoritative_revision"))
    if isinstance(explicit, bool):
        return explicit
    if str(explicit or "").strip().casefold() in {"1", "true", "yes"}:
        return True
    status = " ".join(
        str(row.get(key) or "")
        for key in ("DatasetStatus", "UpdateMode")
    ).casefold()
    if any(marker in status for marker in ("revised", "revision", "corrected")):
        return True
    semantics = " ".join(
        str(row.get(key) or "")
        for key in ("RevisionSemantics", "revision_semantics", "CorrectionType")
    ).strip().casefold()
    return semantics in {
        "authoritative_revision",
        "source_correction",
        "restatement",
    }


def _observation_allows_authoritative_revision(row: Mapping[str, Any]) -> bool:
    metadata = row.get("metadata") or row.get("metadata_") or {}
    if isinstance(metadata, Mapping) and metadata.get("authoritative_revision") is True:
        return True
    raw_data = row.get("raw_data") or {}
    return isinstance(raw_data, Mapping) and _allows_authoritative_revision(raw_data)


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
