"""Monthly updater for the Ontario, Canada PHO IDTO jurisdiction dataset."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Set

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.data.crawlers.ca import (
    DEFINITION_VERSION_BY_YEAR,
    DEFAULT_SOURCE_NAME,
    ONTARIO_GEOGRAPHY_KEY,
    ONTARIO_GEOCODE,
    SPECIAL_TIME_BASES,
    CAOntarioFetchSummary,
    CanadaOntarioPHOCrawler,
    _parse_case_value,
)
from src.data.processors.mapping_lookup import (
    load_country_mapping_dict,
    normalize_mapping_key,
)

logger = get_logger(__name__)

MAPPING_SOURCE_ID = "SRC_CA_ON_PHO_IDTO"

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/ca/ontario_idto_monthly.csv"
DEFAULT_MAPPING_CSV = ROOT / "configs/mapping/ca-on.csv"
DEFAULT_EXCLUSIONS_JSON = ROOT / "configs/mapping/ca-on_exclusions.json"


@dataclass(frozen=True)
class CAOntarioUpdateFetchResult:
    rows: list[dict[str, str]]
    source_latest_date: Optional[date]
    source_csv: Path
    script_logs: list[str]
    disease_count: int
    content_sha256: str
    populated_month_count: int
    unpublished_month_slots: int
    acquisition_mode: str
    model_refresh_time: str
    source_artifact_sha256: str
    source_file_mtime: str


@dataclass(frozen=True)
class CAOntarioUpdateImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: Optional[date]
    source_latest_date: Optional[date]
    imported_new_data: bool


class CAOntarioMonthlyUpdater:
    """Refresh Ontario rows using the standard country/region monthly contract."""

    country_code = "CA-ON"
    ontology_source_id = MAPPING_SOURCE_ID
    series_geography_key = ONTARIO_GEOGRAPHY_KEY
    series_registered_rows_only = True
    series_registry_coverage = "required"

    def __init__(
        self,
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
        output_csv: Path = DEFAULT_OUTPUT_CSV,
    ) -> None:
        self.source_name = source_name
        self.output_csv = Path(output_csv)

    @staticmethod
    def pipeline_refresh_kwargs(task: object) -> dict[str, object]:
        """Translate optional task controls without creating a custom pipeline."""

        input_data = (
            getattr(task, "input_data", None)
            if isinstance(getattr(task, "input_data", None), dict)
            else {}
        )
        requested_mode = str(
            input_data.get("acquisition_mode") or "live"
        ).strip().casefold()
        if requested_mode in {"live", "powerbi_read_only"}:
            use_configured_file = False
        elif requested_mode in {"configured_file", "official_export_file"}:
            use_configured_file = True
        else:
            raise ValueError(
                "Ontario acquisition_mode must be 'live' or 'configured_file'"
            )
        reporting_year = input_data.get("reporting_year")
        return {
            "input_file": None,
            "reporting_year": int(reporting_year) if reporting_year else None,
            "use_configured_file": use_configured_file,
        }

    def refresh_source(
        self,
        *,
        source: str = "pho_idto_monthly",
        run_external: bool = False,
        force: bool = False,
        months: list[tuple[int, int]] | None = None,
        save_raw: bool = False,
        raw_dir: Path | None = None,
        input_file: Path | None = None,
        reporting_year: int | None = None,
        use_configured_file: bool = False,
    ) -> CAOntarioUpdateFetchResult:
        del run_external
        if source not in {
            "all",
            "pho_idto_monthly",
            "pho_idto",
            "idto",
            "ontario",
            "ca-on",
        }:
            raise ValueError(f"Unsupported Ontario source: {source}")

        crawler = CanadaOntarioPHOCrawler(
            save_raw=save_raw,
            raw_dir=raw_dir or ROOT / "data/raw/ca/on_idto",
        )
        summary: CAOntarioFetchSummary = crawler.crawl_monthly_ontario(
            self.output_csv,
            months=months,
            input_file=Path(input_file) if input_file else None,
            reporting_year=reporting_year,
            allow_file_revisions=force,
            use_configured_file=use_configured_file,
        )
        rows = self._load_rows(self.output_csv)
        self._validate_rows(rows)
        logs = [
            f"[crawler] acquisition mode: {summary.acquisition_mode}",
            (
                f"[crawler] prepared {summary.row_count} Ontario monthly rows "
                f"for {summary.disease_count} diseases"
            ),
            f"[crawler] latest month: {summary.latest_date}",
            f"[crawler] normalized content sha256: {summary.content_sha256}",
            (
                "[crawler] current-year month slots without observations "
                "(blank, future, or absent in export): "
                f"{summary.unpublished_month_slots}"
            ),
        ]
        if summary.model_refresh_time:
            logs.append(f"[crawler] Power BI model refresh: {summary.model_refresh_time}")
        if summary.source_artifact_sha256:
            logs.append(
                "[crawler] source artifact sha256: "
                f"{summary.source_artifact_sha256}"
            )
        if summary.source_file_mtime:
            logs.append(
                f"[crawler] source file mtime (UTC): {summary.source_file_mtime}"
            )
        if save_raw:
            logs.append(f"[crawler] raw artifacts archived under {crawler.raw_dir}")
        return CAOntarioUpdateFetchResult(
            rows=rows,
            source_latest_date=summary.latest_date,
            source_csv=self.output_csv,
            script_logs=logs,
            disease_count=summary.disease_count,
            content_sha256=summary.content_sha256,
            populated_month_count=summary.populated_month_count,
            unpublished_month_slots=summary.unpublished_month_slots,
            acquisition_mode=summary.acquisition_mode,
            model_refresh_time=summary.model_refresh_time,
            source_artifact_sha256=summary.source_artifact_sha256,
            source_file_mtime=summary.source_file_mtime,
        )

    @staticmethod
    def _parse_model_refresh_time(value: object, *, label: str) -> datetime:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(f"Ontario IDTO {label} model refresh time is missing")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Ontario IDTO {label} model refresh time is invalid: {raw!r}"
            ) from exc
        if parsed.tzinfo is None:
            # PHO's Power BI metadata currently emits LastRefreshTime without
            # an offset. Assign UTC deterministically for release ordering; do
            # not interpret this as a publisher-stated wall-clock timezone.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def authorize_release_for_persistence(
        cls,
        rows: list[dict[str, str]],
        *,
        stored_model_refresh_time: object | None,
    ) -> str:
        """Authorize a live revision only when its source release is newer."""

        modes = {str(row.get("AcquisitionMode") or "").strip() for row in rows}
        if modes == {"official_export_file"}:
            forced = any(
                str(row.get("AuthoritativeRevision") or "").casefold() == "true"
                for row in rows
            )
            return "official_file_forced_revision" if forced else "official_file_replay_safe"
        if modes != {"powerbi_read_only"}:
            raise ValueError("Ontario IDTO batch has mixed or unknown acquisition modes")

        refresh_values = {
            str(row.get("ModelRefreshTime") or "").strip() for row in rows
        }
        if len(refresh_values) != 1:
            raise ValueError("Ontario IDTO live batch has inconsistent model refresh times")
        incoming_text = next(iter(refresh_values))
        incoming = cls._parse_model_refresh_time(incoming_text, label="incoming")
        stored = (
            cls._parse_model_refresh_time(
                stored_model_refresh_time, label="stored"
            )
            if stored_model_refresh_time
            else None
        )
        if stored is not None and incoming < stored:
            raise ValueError(
                "Ontario IDTO live release is older than the newest stored release: "
                f"incoming={incoming_text}, stored={stored_model_refresh_time}"
            )
        is_new_release = stored is None or incoming > stored
        marker = str(is_new_release).lower()
        for row in rows:
            row["AuthoritativeRevision"] = marker
            row["AllowEqualQualityOverwrite"] = marker
        if stored is None:
            return "initial_live_release"
        return "newer_live_release" if is_new_release else "unchanged_live_release"

    @staticmethod
    def _load_rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _validate_rows(rows: list[dict[str, str]]) -> None:
        if not rows:
            raise ValueError("Ontario IDTO normalized CSV is empty")
        identities: dict[tuple[str, str], str] = {}
        for row_number, row in enumerate(rows, start=2):
            if row.get("GeographyKey") != ONTARIO_GEOGRAPHY_KEY:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has unexpected geography"
                )
            disease = " ".join(str(row.get("RawDiseaseLabel") or "").split())
            report_date = str(row.get("Date") or "")
            cases = str(row.get("Cases") or "").strip()
            if not disease or not report_date or not cases:
                raise ValueError(f"Ontario IDTO row {row_number} is incomplete")
            try:
                parsed = date.fromisoformat(report_date)
            except ValueError as exc:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has invalid date"
                ) from exc
            if parsed.day != 1:
                raise ValueError(
                    f"Ontario IDTO row {row_number} is not month-start normalized"
                )
            try:
                row_year = int(row.get("Year") or "")
                row_month = int(row.get("Month") or "")
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has invalid year/month"
                ) from exc
            if (row_year, row_month) != (parsed.year, parsed.month):
                raise ValueError(
                    f"Ontario IDTO row {row_number} date and year/month disagree"
                )
            try:
                expected_definition = DEFINITION_VERSION_BY_YEAR[row_year]
            except KeyError as exc:
                raise ValueError(
                    f"Ontario IDTO row {row_number} uses an unreviewed year"
                ) from exc
            if row.get("DefinitionVersion") != expected_definition:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has an unreviewed definition version"
                )
            if row.get("ReportingArea") != "Ontario" or row.get("Geocode") != ONTARIO_GEOCODE:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has unexpected reporting scope"
                )
            if (
                row.get("JurisdictionCode") != ONTARIO_GEOCODE
                or row.get("ParentCountryCode") != "CA"
                or row.get("LocationType") != "subdivision"
            ):
                raise ValueError(
                    f"Ontario IDTO row {row_number} has unexpected jurisdiction metadata"
                )
            if row.get("Source") != DEFAULT_SOURCE_NAME:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has unexpected source provenance"
                )
            if row.get("DatasetStatus") != "preliminary" or str(
                row.get("IsProvisional") or ""
            ).casefold() != "true":
                raise ValueError(
                    f"Ontario IDTO row {row_number} has unexpected quality status"
                )
            acquisition_mode = str(row.get("AcquisitionMode") or "").strip()
            if acquisition_mode not in {
                "powerbi_read_only",
                "official_export_file",
            }:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has invalid acquisition mode"
                )
            if acquisition_mode == "powerbi_read_only":
                CAOntarioMonthlyUpdater._parse_model_refresh_time(
                    row.get("ModelRefreshTime"), label="incoming"
                )
            for policy_field in (
                "AuthoritativeRevision",
                "AllowEqualQualityOverwrite",
            ):
                if str(row.get(policy_field) or "").strip().casefold() not in {
                    "true",
                    "false",
                }:
                    raise ValueError(
                        f"Ontario IDTO row {row_number} has invalid {policy_field}"
                    )
            expected_time_basis = SPECIAL_TIME_BASES.get(
                disease.casefold(), "PHO episode-date hierarchy"
            )
            if row.get("TimeBasis") != expected_time_basis:
                raise ValueError(
                    f"Ontario IDTO row {row_number} has incorrect time basis"
                )
            _parse_case_value(cases)
            identity = (report_date, disease.casefold())
            previous = identities.get(identity)
            if previous is not None and previous != cases:
                raise ValueError(
                    f"Ontario IDTO row {row_number} conflicts with a duplicate"
                )
            if previous is not None:
                raise ValueError(
                    f"Ontario IDTO row {row_number} duplicates an observation"
                )
            identities[identity] = cases
        CAOntarioMonthlyUpdater._validate_source_label_contract(rows)

    @staticmethod
    def _load_source_label_contract() -> tuple[set[str], set[str], set[str]]:
        with DEFAULT_MAPPING_CSV.open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            registered = {
                str(row.get("local_name") or "").strip()
                for row in csv.DictReader(handle)
                if str(row.get("source_id") or "").strip() == MAPPING_SOURCE_ID
            }
        registered.discard("")
        manifest = json.loads(DEFAULT_EXCLUSIONS_JSON.read_text(encoding="utf-8"))
        excluded = {
            str(item.get("local_label") or "").strip()
            for item in manifest.get("exclusions", [])
            if isinstance(item, dict)
        }
        excluded.discard("")
        reviewed = {
            str(label).strip()
            for label in manifest.get("reviewed_source_labels", [])
            if str(label).strip()
        }
        expected_count = int(manifest.get("expected_source_label_count") or 0)
        expected_registered = int(manifest.get("registered_label_count") or 0)
        if registered.intersection(excluded):
            raise ValueError("Ontario IDTO registered and excluded labels overlap")
        if len(registered) != expected_registered:
            raise ValueError("Ontario IDTO registered-label contract count is stale")
        if reviewed != registered.union(excluded) or len(reviewed) != expected_count:
            raise ValueError("Ontario IDTO reviewed source-label contract is stale")
        return registered, excluded, reviewed

    @staticmethod
    def _registered_series_by_label() -> dict[str, str]:
        with DEFAULT_MAPPING_CSV.open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            pairs = [
                (
                    str(row.get("local_name") or "").strip().casefold(),
                    str(row.get("series_id") or "").strip(),
                )
                for row in csv.DictReader(handle)
                if str(row.get("source_id") or "").strip() == MAPPING_SOURCE_ID
            ]
        if any(not label or not series_id for label, series_id in pairs):
            raise ValueError("Ontario IDTO mapping has an incomplete series identity")
        mapping = dict(pairs)
        if len(mapping) != len(pairs):
            raise ValueError("Ontario IDTO mapping repeats a source label")
        return mapping

    @staticmethod
    def _validate_source_label_contract(rows: list[dict[str, str]]) -> None:
        _registered, _excluded, reviewed = (
            CAOntarioMonthlyUpdater._load_source_label_contract()
        )
        source_labels = {
            str(row.get("RawDiseaseLabel") or "").strip() for row in rows
        }
        source_labels.discard("")
        unknown = source_labels - reviewed
        if unknown:
            logger.warning(
                "Ontario IDTO source contains newly discovered disease labels; "
                "retaining them in unmapped holding series | labels={}",
                sorted(unknown),
            )
        acquisition_modes = {
            str(row.get("AcquisitionMode") or "").strip() for row in rows
        }
        if acquisition_modes == {"powerbi_read_only"} and not reviewed.issubset(source_labels):
            missing = reviewed - source_labels
            raise ValueError(
                "Ontario IDTO live disease-label manifest changed; missing reviewed "
                "labels: "
                + ", ".join(sorted(missing))
            )

    async def get_db_latest_date(self, db: AsyncSession) -> Optional[date]:
        result = await db.execute(
            text(
                """
                SELECT MAX(dso.time)
                FROM disease_series_observations dso
                JOIN disease_surveillance_series dss
                  ON dss.series_code = dso.series_code
                WHERE dss.country_code = :country_code
                  AND dss.source_system = :source_id
                  AND dso.geography_key = :geography_key
                """
            ),
            {
                "country_code": self.country_code,
                "source_id": self.ontology_source_id,
                "geography_key": self.series_geography_key,
            },
        )
        value = result.scalar()
        if value is None:
            return None
        return value.date() if isinstance(value, datetime) else value

    async def get_db_model_refresh_time(self, db: AsyncSession) -> Optional[str]:
        result = await db.execute(
            text(
                """
                SELECT MAX(NULLIF(dso.raw_data ->> 'ModelRefreshTime', ''))
                FROM disease_series_observations dso
                JOIN disease_surveillance_series dss
                  ON dss.series_code = dso.series_code
                WHERE dss.country_code = :country_code
                  AND dss.source_system = :source_id
                  AND dso.geography_key = :geography_key
                """
            ),
            {
                "country_code": self.country_code,
                "source_id": self.ontology_source_id,
                "geography_key": self.series_geography_key,
            },
        )
        value = result.scalar()
        return str(value) if value else None

    async def validate_live_snapshot_continuity(
        self,
        db: AsyncSession,
        rows: list[dict[str, str]],
    ) -> int:
        """Reject live snapshots that silently retract a stored observation.

        Blank source cells are intentionally absent from normalized rows. Until
        the model supports explicit coverage tombstones, accepting a newer
        snapshot with a missing natural identity would leave the old value in
        place and fabricate a hybrid snapshot.
        """

        modes = {str(row.get("AcquisitionMode") or "").strip() for row in rows}
        if modes != {"powerbi_read_only"}:
            return 0
        years = {int(row.get("Year") or 0) for row in rows}
        if len(years) != 1:
            raise ValueError("Ontario IDTO live snapshot must contain exactly one year")
        reporting_year = next(iter(years))
        result = await db.execute(
            text(
                """
                SELECT dso.time, dso.series_code
                FROM disease_series_observations dso
                JOIN disease_surveillance_series dss
                  ON dss.series_code = dso.series_code
                WHERE dss.country_code = :country_code
                  AND dss.source_system = :source_id
                  AND dso.geography_key = :geography_key
                  AND dso.time >= :year_start
                  AND dso.time < :year_end
                """
            ),
            {
                "country_code": self.country_code,
                "source_id": self.ontology_source_id,
                "geography_key": self.series_geography_key,
                "year_start": datetime(
                    reporting_year, 1, 1, tzinfo=timezone.utc
                ),
                "year_end": datetime(
                    reporting_year + 1, 1, 1, tzinfo=timezone.utc
                ),
            },
        )
        existing: set[tuple[date, str]] = set()
        for raw_time, raw_series_code in result.fetchall():
            observation_date = (
                raw_time.date() if isinstance(raw_time, datetime) else raw_time
            )
            if not isinstance(observation_date, date) or not raw_series_code:
                raise ValueError("Ontario IDTO stored identity is malformed")
            existing.add((observation_date, str(raw_series_code)))

        series_by_label = self._registered_series_by_label()
        incoming = {
            (
                date.fromisoformat(str(row.get("Date") or "")),
                series_by_label[str(row.get("RawDiseaseLabel") or "").casefold()],
            )
            for row in rows
            if str(row.get("RawDiseaseLabel") or "").casefold()
            in series_by_label
        }
        retracted = sorted(existing - incoming)
        if retracted:
            sample = ", ".join(
                f"{series_code}@{observation_date.isoformat()}"
                for observation_date, series_code in retracted[:8]
            )
            raise ValueError(
                "Ontario IDTO newer live snapshot retracts stored observations; "
                "explicit missing/tombstone support is required before import: "
                f"count={len(retracted)}, sample={sample}"
            )
        return len(existing)

    async def get_db_months(self, db: AsyncSession) -> Set[tuple[int, int]]:
        result = await db.execute(
            text(
                """
                SELECT DISTINCT
                    EXTRACT(YEAR FROM dso.time)::int AS yr,
                    EXTRACT(MONTH FROM dso.time)::int AS mo
                FROM disease_series_observations dso
                JOIN disease_surveillance_series dss
                  ON dss.series_code = dso.series_code
                WHERE dss.country_code = :country_code
                  AND dss.source_system = :source_id
                  AND dso.geography_key = :geography_key
                """
            ),
            {
                "country_code": self.country_code,
                "source_id": self.ontology_source_id,
                "geography_key": self.series_geography_key,
            },
        )
        return {(int(row[0]), int(row[1])) for row in result.fetchall()}

    async def _get_country_id(self, db: AsyncSession) -> int:
        result = await db.execute(
            text("SELECT id FROM countries WHERE code = :code"),
            {"code": self.country_code},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Country/region not found in database: {self.country_code}")
        return int(row[0])

    async def _load_mapping_dict(self, db: AsyncSession) -> dict[str, int]:
        return await load_country_mapping_dict(
            db,
            self.country_code,
            source_id=self.ontology_source_id,
        )

    async def import_rows(
        self,
        db: AsyncSession,
        rows: list[dict[str, str]],
        *,
        db_latest_date: Optional[date],
        source_latest_date: Optional[date],
        force: bool = False,
    ) -> CAOntarioUpdateImportResult:
        """Upsert mapped Ontario facts under the independent CA-ON jurisdiction."""

        del force  # The source release metadata, not a task flag, controls revision order.
        if not rows:
            return CAOntarioUpdateImportResult(
                0, 0, db_latest_date, source_latest_date, False
            )

        self._validate_rows(rows)
        await self.validate_live_snapshot_continuity(db, rows)
        stored_model_refresh = await self.get_db_model_refresh_time(db)
        self.authorize_release_for_persistence(
            rows,
            stored_model_refresh_time=stored_model_refresh,
        )

        country_id = await self._get_country_id(db)
        mapping_dict = await self._load_mapping_dict(db)
        upsert_by_identity: dict[tuple[datetime, int, int], dict[str, object]] = {}
        skipped_unmapped = 0

        for row in rows:
            label = " ".join(str(row.get("RawDiseaseLabel") or "").split())
            disease_id = mapping_dict.get(normalize_mapping_key(label))
            if disease_id is None:
                skipped_unmapped += 1
                continue

            canonical_cases, suppressed = _parse_case_value(row.get("Cases"))
            # Legacy facts cannot represent suppression without turning it into a
            # number. The lossless series write that follows preserves it.
            if canonical_cases is None or suppressed:
                continue
            report_date = date.fromisoformat(str(row.get("Date") or ""))
            report_time = datetime(
                report_date.year,
                report_date.month,
                1,
                tzinfo=timezone.utc,
            )
            identity = (report_time, disease_id, country_id)
            if identity in upsert_by_identity:
                raise ValueError(
                    "Ontario source labels collide in the legacy projection: "
                    f"{label!r} at {report_date.isoformat()}"
                )

            metadata = {
                "source_scope": "pho_idto_monthly",
                "frequency": "monthly",
                "temporal_granularity": "monthly",
                "measure": "case_notifications",
                "reporting_basis": "notification",
                "jurisdiction_code": "CA-ON",
                "parent_country_code": "CA",
                "location_type": "subdivision",
                "reporting_area": "Ontario",
                "population_scope": row.get("PopulationScope"),
                "dataset_status": row.get("DatasetStatus"),
                "is_provisional": True,
                "definition_version": row.get("DefinitionVersion"),
                "time_basis": row.get("TimeBasis"),
                "model_refresh_time": row.get("ModelRefreshTime"),
                "source_url": row.get("SourceURL"),
                "death_reporting": "not_provided_by_source",
            }
            upsert_by_identity[identity] = {
                "time": report_time,
                "disease_id": disease_id,
                "country_id": country_id,
                "cases": int(canonical_cases),
                "deaths": None,
                "region": "Ontario",
                "data_source": row.get("Source") or self.source_name,
                "data_quality": "preliminary",
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "raw_data": json.dumps(row, ensure_ascii=False),
            }

        upsert_rows = list(upsert_by_identity.values())
        if upsert_rows:
            await db.execute(
                text(
                    """
                    INSERT INTO disease_records (
                        time, disease_id, country_id, cases, deaths, region,
                        data_source, data_quality, metadata, raw_data,
                        new_cases, new_deaths, recoveries, active_cases,
                        new_recoveries
                    ) VALUES (
                        :time, :disease_id, :country_id, :cases, :deaths, :region,
                        :data_source, :data_quality, CAST(:metadata AS json),
                        CAST(:raw_data AS json), 0, 0, 0, 0, 0
                    )
                    ON CONFLICT (time, disease_id, country_id) DO UPDATE SET
                        cases = EXCLUDED.cases,
                        deaths = EXCLUDED.deaths,
                        region = EXCLUDED.region,
                        data_source = EXCLUDED.data_source,
                        data_quality = EXCLUDED.data_quality,
                        metadata = EXCLUDED.metadata,
                        raw_data = EXCLUDED.raw_data
                    """
                ),
                upsert_rows,
            )

        imported = len(upsert_rows)
        logger.info(
            "Ontario monthly import complete | jurisdiction={} upserted={} "
            "skipped_unmapped={}",
            self.country_code,
            imported,
            skipped_unmapped,
        )
        return CAOntarioUpdateImportResult(
            inserted_or_updated=imported,
            skipped_unmapped=skipped_unmapped,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            imported_new_data=imported > 0,
        )


__all__ = [
    "CAOntarioMonthlyUpdater",
    "CAOntarioUpdateFetchResult",
    "CAOntarioUpdateImportResult",
    "DEFAULT_OUTPUT_CSV",
    "MAPPING_SOURCE_ID",
]
