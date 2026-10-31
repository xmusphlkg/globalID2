"""
Crawl Service

Encapsulates all business logic for data crawling + processing,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_database, get_logger
from src.core.country_library import get_country_bootstrap_config
from src.core.country_library import SUBDIVISION_REGISTRY
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock
from src.core.task_manager import task_manager
from src.data.crawlers.au import AU_STATE_SUBDIVISIONS
from src.data.storage import (
    SeriesObservationQualityError,
    SeriesObservationQualityPolicy,
    SeriesObservationSaveResult,
    SeriesObservationStore,
)
from src.domain import CrawlRun, Task

logger = get_logger(__name__)


@dataclass
class CrawlResult:
    new_reports: int
    processed_reports: int
    total_records: int
    crawl_run_id: Optional[int]


@dataclass(frozen=True)
class _PipelineSpec:
    handler_name: str
    updater_name: Optional[str] = None


class CrawlService:
    """Orchestrates the three-phase crawl → parse → store pipeline."""

    _PIPELINES: Dict[str, _PipelineSpec] = {
        "CN": _PipelineSpec("_execute_cn_cdc"),
        "US": _PipelineSpec("_execute_us_weekly", "USWeeklyUpdater"),
        "JP": _PipelineSpec("_execute_jp_weekly", "JPWeeklyUpdater"),
        "AU": _PipelineSpec("_execute_au_monthly", "AUMonthlyUpdater"),
        "CA": _PipelineSpec("_execute_ca_national_annual", "CanadaCNDSSAnnualUpdater"),
        "NZ": _PipelineSpec("_execute_nz_monthly", "NZMonthlyUpdater"),
        "TW": _PipelineSpec("_execute_tw_monthly", "TWMonthlyUpdater"),
        "HK": _PipelineSpec("_execute_hk_monthly", "HKMonthlyUpdater"),
        "FI": _PipelineSpec("_execute_fi_monthly", "FIMonthlyUpdater"),
        "IE": _PipelineSpec("_execute_ie_weekly", "IEWeeklyUpdater"),
        "NO": _PipelineSpec("_execute_no_monthly", "NOMonthlyUpdater"),
        "SE": _PipelineSpec("_execute_se_monthly", "SEMonthlyUpdater"),
        "AT": _PipelineSpec("_execute_at_monthly", "ATMonthlyUpdater"),
        "DE": _PipelineSpec("_execute_de_weekly", "DEWeeklyUpdater"),
        "SG": _PipelineSpec("_execute_sg_weekly", "SGWeeklyUpdater"),
        "FR": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "ES": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "IT": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "PT": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "PL": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "CZ": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "GR": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "RO": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "CA-ON": _PipelineSpec(
            "_execute_ca_on_monthly", "CAOntarioMonthlyUpdater"
        ),
        "BR": _PipelineSpec("_execute_br_monthly", "BRMonthlyUpdater"),
        "KR": _PipelineSpec("_execute_kr_monthly", "KRMonthlyUpdater"),
        "CH": _PipelineSpec("_execute_ch_monthly", "CHMonthlyUpdater"),
        "IS": _PipelineSpec("_execute_is_mixed", "ISMultiFrequencyUpdater"),
    }
    for _ecdc_country_code in ECDC_BASELINE_COUNTRY_CODES:
        _PIPELINES.setdefault(
            _ecdc_country_code,
            _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        )
    for _au_subdivision_code in AU_STATE_SUBDIVISIONS:
        _PIPELINES.setdefault(
            _au_subdivision_code,
            _PipelineSpec("_execute_au_monthly", "AUMonthlyUpdater"),
        )
    for _subdivision_code, _subdivision in SUBDIVISION_REGISTRY.items():
        _parent_code = str(_subdivision.get("parent_country_code") or "").upper()
        if _parent_code not in {"JP", "SE", "DE", "US", "CH", "NZ", "BR"}:
            continue
        _parent_pipeline = _PIPELINES.get(_parent_code)
        if _parent_pipeline is not None:
            _PIPELINES[_subdivision_code] = (
                _PipelineSpec("_execute_nz_dhb_annual", "NZDHBAnnualUpdater")
                if _parent_code == "NZ"
                else _parent_pipeline
            )
    from src.data.crawlers.cn_province_adapters import province_adapter_registry as _province_registry
    for _province_code in _province_registry():
        _PIPELINES[_province_code] = _PipelineSpec("_execute_cn_province", "CNProvinceUpdater")
    from src.data.processors.br_subdivisions import STATE_CODES as _br_states
    for _state in _br_states.values():
        _PIPELINES[f"BR-{_state}"] = _PipelineSpec("_execute_br_monthly", "BRMonthlyUpdater")
    _SERIES_SOURCE_IDS: Dict[str, str | Dict[str, str]] = {
        "US": {
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
        "JP": "SRC_JP_NIID",
        "AU": "SRC_AU_NINDSS",
        "CA": "SRC_CA_PHAC_CNDSS",
        "NZ": "SRC_NZ_PHS",
        "TW": "SRC_TW_NIDSS",
        "HK": "SRC_HK_CHP",
        "FI": "SRC_FI_THL_TTR",
        "IE": "SRC_IE_HPSC_NDH",
        "NO": "SRC_NO_FHI_MSIS",
        "SE": "SRC_SE_FOHM_SMINET",
        "AT": "SRC_AT_AGES_RADAR",
        "DE": "SRC_DE_RKI_SURVSTAT",
        "SG": {
            "Singapore data.gov.sg Weekly Infectious Diseases Bulletin (2012-2022)": "SRC_SG_DATA_GOV_WIDB",
            "Singapore CDA Weekly Infectious Diseases Bulletin": "SRC_SG_CDA_WIDB",
        },
        "FR": "SRC_FR_ECDC_ATLAS",
        "ES": "SRC_ES_ECDC_ATLAS",
        "IT": "SRC_IT_ECDC_ATLAS",
        "PT": "SRC_PT_ECDC_ATLAS",
        "PL": "SRC_PL_ECDC_ATLAS",
        "CZ": "SRC_CZ_ECDC_ATLAS",
        "GR": "SRC_GR_ECDC_ATLAS",
        "RO": "SRC_RO_ECDC_ATLAS",
        "KR": "SRC_KR_KDCA",
        "BR": "SRC_BR_SINAN",
        "CH": "SRC_CH_FOPH_IDD",
        "CA-ON": "SRC_CA_ON_PHO_IDTO",
        "IS": {
            "Iceland Directorate of Health Annual Dashboard": "SRC_IS_DOH_ANNUAL",
            "Iceland Directorate of Health STI Dashboard": "SRC_IS_DOH_STI",
            "Iceland Directorate of Health Respiratory Dashboard": "SRC_IS_DOH_RESPIRATORY",
            "Iceland Directorate of Health historical registry annual": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health disease-specific monthly workbooks": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health legacy ICD monthly reports": "SRC_IS_DOH_LEGACY_ICD",
            "Iceland Directorate of Health Historical Registry": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health Legacy ICD Monthly": "SRC_IS_DOH_LEGACY_ICD",
        },
    }
    for _ecdc_country_code in ECDC_BASELINE_COUNTRY_CODES:
        _SERIES_SOURCE_IDS.setdefault(
            _ecdc_country_code,
            f"SRC_{_ecdc_country_code}_ECDC_ATLAS",
        )

    @classmethod
    def supported_country_codes(cls) -> List[str]:
        """Return country codes with implemented crawl pipelines."""
        return list(cls._PIPELINES.keys())

    @classmethod
    def supported_country_text(cls) -> str:
        """Human-readable supported country list for validation errors."""
        return ", ".join(cls.supported_country_codes())

    @staticmethod
    def _make_updater(updater_name: str, country_code: Optional[str] = None):
        if updater_name == "CNProvinceUpdater":
            from src.data.processors.cn_provinces import CNProvinceUpdater
            return CNProvinceUpdater(country_code=str(country_code).upper())
        from src.data.processors import (
            AUMonthlyUpdater,
            ATMonthlyUpdater,
            BRMonthlyUpdater,
            CAOntarioMonthlyUpdater,
            CanadaCNDSSAnnualUpdater,
            CHMonthlyUpdater,
            FIMonthlyUpdater,
            HKMonthlyUpdater,
            IEWeeklyUpdater,
            ISMultiFrequencyUpdater,
            JPWeeklyUpdater,
            KRMonthlyUpdater,
            NOMonthlyUpdater,
            NZDHBAnnualUpdater,
            NZMonthlyUpdater,
            SEMonthlyUpdater,
            DEWeeklyUpdater,
            SGWeeklyUpdater,
            ECDCAnnualUpdater,
            TWMonthlyUpdater,
            USWeeklyUpdater,
        )

        updaters = {
            "AUMonthlyUpdater": AUMonthlyUpdater,
            "ATMonthlyUpdater": ATMonthlyUpdater,
            "BRMonthlyUpdater": BRMonthlyUpdater,
            "CAOntarioMonthlyUpdater": CAOntarioMonthlyUpdater,
            "CanadaCNDSSAnnualUpdater": CanadaCNDSSAnnualUpdater,
            "CHMonthlyUpdater": CHMonthlyUpdater,
            "FIMonthlyUpdater": FIMonthlyUpdater,
            "HKMonthlyUpdater": HKMonthlyUpdater,
            "IEWeeklyUpdater": IEWeeklyUpdater,
            "ISMultiFrequencyUpdater": ISMultiFrequencyUpdater,
            "JPWeeklyUpdater": JPWeeklyUpdater,
            "KRMonthlyUpdater": KRMonthlyUpdater,
            "NOMonthlyUpdater": NOMonthlyUpdater,
            "NZDHBAnnualUpdater": NZDHBAnnualUpdater,
            "NZMonthlyUpdater": NZMonthlyUpdater,
            "SEMonthlyUpdater": SEMonthlyUpdater,
            "DEWeeklyUpdater": DEWeeklyUpdater,
            "SGWeeklyUpdater": SGWeeklyUpdater,
            "ECDCAnnualUpdater": ECDCAnnualUpdater,
            "TWMonthlyUpdater": TWMonthlyUpdater,
            "USWeeklyUpdater": USWeeklyUpdater,
        }
        updater_type = updaters[updater_name]
        if updater_name == "ECDCAnnualUpdater":
            return updater_type(country_code or "FR")
        if updater_name == "AUMonthlyUpdater" and country_code:
            return updater_type(country_code=country_code)
        if updater_name == "BRMonthlyUpdater" and str(country_code or "").startswith("BR-"):
            from pathlib import Path
            return updater_type(country_code=country_code, output_csv=Path("data/processed/br/subdivisions") / f"{country_code}.csv")
        subdivision = SUBDIVISION_REGISTRY.get(str(country_code or "").upper())
        if subdivision and updater_name in {
            "JPWeeklyUpdater", "SEMonthlyUpdater", "DEWeeklyUpdater",
            "USWeeklyUpdater", "CHMonthlyUpdater", "NZMonthlyUpdater",
            "NZDHBAnnualUpdater", "BRMonthlyUpdater",
        }:
            return updater_type(country_code=str(country_code).upper())
        return updater_type()

    @staticmethod
    async def _save_series_rows(
        db,
        updater,
        rows,
        *,
        series_rows=None,
    ):
        """Persist lossless source-series facts without a legacy projection."""

        quality_policy = CrawlService._series_quality_policy(updater)
        country_code = str(getattr(updater, "country_code", "") or "").upper()
        series_country_code = str(
            getattr(updater, "series_country_code", "") or country_code
        ).upper()
        source_id = getattr(updater, "ontology_source_id", None)
        if source_id is None:
            source_id = CrawlService._SERIES_SOURCE_IDS.get(
                country_code,
                CrawlService._SERIES_SOURCE_IDS.get(series_country_code),
            )
        geography_key = getattr(updater, "series_geography_key", None)
        geography_from_rows = bool(
            getattr(updater, "series_geography_from_rows", False)
        )
        if (
            geography_key is None
            and not geography_from_rows
            and country_code in CrawlService._SERIES_SOURCE_IDS
        ):
            # Updaters without an explicit row-grain contract emit a national
            # aggregate.  Scope-aware updaters opt out and derive the key from
            # each source row.
            geography_key = f"country:{country_code}:national"
        series_store = SeriesObservationStore()
        source_series_rows = rows if series_rows is None else series_rows
        # Mapping Registry v3 discovers source categories for every country at
        # the same transactional boundary as the source facts.  Unknown
        # categories are retained and queued for AI review; they are never
        # silently coerced to a legacy disease ID.
        if isinstance(db, AsyncSession) and source_series_rows:
            from src.services.disease_mapping_registry_service import (
                disease_mapping_registry_service,
            )

            source_series_rows = series_store.enrich_registry_identities(
                source_series_rows,
                series_country_code,
                source_id=source_id,
            )
            discovery = await disease_mapping_registry_service.discover_rows(
                db,
                country_code=series_country_code,
                source_id=source_id,
                rows=source_series_rows,
                notify=True,
            )
            logger.info(
                "Disease Mapping Registry discovery | country={} categories={} new={}",
                series_country_code,
                discovery["source_category_count"],
                discovery["new_category_count"],
            )
        skipped_unregistered = 0
        skipped_missing = 0
        if bool(getattr(updater, "series_registered_rows_only", False)):
            selection = series_store.select_registry_rows(
                source_series_rows,
                series_country_code,
                source_id=source_id,
            )
            holding_rows = list(getattr(selection, "unregistered_rows", ()) or ())
            if source_series_rows and not selection.rows and not holding_rows:
                raise ValueError(
                    "Registry-scoped dual write selected no non-missing source rows"
                )
            source_series_rows = [*selection.rows, *holding_rows]
            # Older/test selection adapters do not expose the retained rows. In
            # that compatibility case the count still means rows were omitted;
            # the production selector forwards them into dynamic holding series.
            skipped_unregistered = (
                0 if holding_rows else selection.skipped_unregistered
            )
            skipped_missing = selection.skipped_missing
            logger.info(
                "Disease source-series Registry selection | country={} "
                "selected={} retained_unmapped={} omitted_unregistered={} "
                "omitted_missing={}",
                series_country_code,
                len(selection.rows),
                len(holding_rows),
                skipped_unregistered,
                selection.skipped_missing,
            )

            if country_code == "US" and any(
                str(row.get("Source") or "").strip().casefold()
                == "us cdc nndss"
                for row in series_rows or rows
            ):
                nndss_areas = {
                    str(row.get("ReportingArea") or "").strip().casefold()
                    for row in source_series_rows
                    if str(row.get("Source") or "").strip().casefold()
                    == "us cdc nndss"
                }
                resident_aliases = {
                    "us residents",
                    "u.s. residents",
                    "united states residents",
                }
                missing_scopes = []
                if not nndss_areas.intersection(resident_aliases):
                    missing_scopes.append("US_RESIDENTS")
                if "total" not in nndss_areas:
                    missing_scopes.append("TOTAL")
                if missing_scopes:
                    raise ValueError(
                        "NNDSS Registry dual write is missing required reporting "
                        "scope(s): " + ", ".join(missing_scopes)
                    )
        try:
            series_result = await series_store.save_rows(
                db,
                source_series_rows,
                series_country_code,
                source_id=source_id,
                geography_key=geography_key,
                quality_policy=quality_policy,
            )
        except SeriesObservationQualityError as exc:
            logger.error(
                "Disease source-series batch rejected before transaction commit | "
                "country={} mode={} report={}",
                series_country_code,
                quality_policy.mode,
                json.dumps(exc.report.to_dict(), ensure_ascii=False, sort_keys=True),
            )
            raise
        if isinstance(series_result, SeriesObservationSaveResult):
            series_result = replace(
                series_result,
                skipped_unregistered=skipped_unregistered,
                skipped_missing=skipped_missing,
            )
        else:
            # Test doubles and compatibility stores may return a namespace.
            # Preserve that contract while surfacing Registry pre-filter counts.
            setattr(series_result, "skipped_unregistered", skipped_unregistered)
            setattr(series_result, "skipped_missing", skipped_missing)
        quality_report = series_result.quality_report
        logger.info(
            "Disease source-series dual write complete | country={} upserted={} "
            "unregistered={} missing={} unmatched={} ambiguous={} invalid={} "
            "registry_not_synced={} holding={} "
            "quality_mode={} quality_issues={} quality_highest={}",
            series_country_code,
            series_result.upserted,
            series_result.skipped_unregistered,
            series_result.skipped_missing,
            series_result.skipped_unmatched,
            series_result.skipped_ambiguous,
            series_result.skipped_invalid,
            series_result.skipped_registry_not_synced,
            getattr(series_result, "holding_observations", 0),
            quality_policy.mode,
            len(quality_report.issues),
            quality_report.highest_severity or "none",
        )
        if quality_report.issues:
            logger.warning(
                "Disease source-series quality anomalies | country={} report={}",
                series_country_code,
                json.dumps(
                    quality_report.to_dict(), ensure_ascii=False, sort_keys=True
                ),
            )
        return series_result

    @staticmethod
    async def _import_rows_with_series(
        db,
        updater,
        rows,
        *,
        series_rows=None,
        db_latest_date,
        source_latest_date,
        force: bool,
    ):
        """Persist legacy projections and lossless source-series facts atomically."""

        # Take the shared mutex before staging the legacy side of the dual
        # write. SeriesObservationStore takes the same transaction lock again;
        # doing it here preserves one global lock order and avoids a migration
        # waiting on legacy rows while this transaction waits on the mutex.
        await acquire_disease_data_mutation_lock(db)
        import_result = await updater.import_rows(
            db,
            rows,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            force=force,
        )
        series_result = await CrawlService._save_series_rows(
            db,
            updater,
            rows,
            series_rows=series_rows,
        )
        # Source-native integrations can intentionally have no legacy disease
        # projection.  Surface their successfully persisted fact count to the
        # task/control-plane result instead of incorrectly reporting zero.
        if (
            int(getattr(import_result, "inserted_or_updated", 0) or 0) == 0
            and int(getattr(series_result, "upserted", 0) or 0) > 0
        ):
            try:
                return replace(
                    import_result,
                    inserted_or_updated=series_result.upserted,
                    imported_new_data=True,
                )
            except TypeError:
                # Compatibility test doubles need not be dataclasses.
                setattr(import_result, "inserted_or_updated", series_result.upserted)
                setattr(import_result, "imported_new_data", True)
        return import_result

    @staticmethod
    def _series_quality_policy(updater) -> SeriesObservationQualityPolicy:
        """Resolve per-country guard settings with an operational env override."""

        country_code = str(getattr(updater, "country_code", "") or "").upper()
        series_country_code = str(
            getattr(updater, "series_country_code", "") or country_code
        ).upper()
        config = get_country_bootstrap_config(country_code) if country_code else {}
        crawler_config = (
            config.get("crawler_config", {}) if isinstance(config, dict) else {}
        )
        raw_policy = crawler_config.get("series_quality_guard", {})
        if not isinstance(raw_policy, dict):
            raw_policy = {}
        raw_policy = dict(raw_policy)

        updater_policy = getattr(updater, "series_quality_guard", None)
        if isinstance(updater_policy, dict):
            raw_policy.update(updater_policy)

        registry_coverage = getattr(updater, "series_registry_coverage", None)
        if registry_coverage:
            raw_policy["registry_coverage"] = registry_coverage
        elif (
            country_code in CrawlService._SERIES_SOURCE_IDS
            or series_country_code in CrawlService._SERIES_SOURCE_IDS
        ):
            raw_policy.setdefault("registry_coverage", "required")

        env_country_code = country_code.replace("-", "_")
        country_env = (
            os.getenv(f"GLOBALID_{env_country_code}_SERIES_QUALITY_MODE")
            if country_code
            else None
        )
        global_env = os.getenv("GLOBALID_SERIES_QUALITY_MODE")
        if country_env or global_env:
            raw_policy["mode"] = country_env or global_env
        return SeriesObservationQualityPolicy.from_mapping(raw_policy)

    async def execute(
        self,
        task: Task,
        country_code: str,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        include_current_month: Optional[bool] = None,
        revision_window_months: Optional[int] = None,
    ) -> CrawlResult:
        """
        Run the full crawl pipeline and return a summary.

        Progress is reported via task_manager (0 → 100 %).
        Raises on unrecoverable errors (caller handles via task_lifecycle).
        """
        normalized_country = (country_code or "").strip().upper()
        normalized_source = str(source or "all").strip().casefold()
        if (
            normalized_country in ECDC_BASELINE_COUNTRY_CODES
            and normalized_source in {"ecdc", "atlas", "ecdc_atlas_annual"}
        ):
            # Countries with a higher-frequency national pipeline can also
            # refresh the independent ECDC annual fallback explicitly.
            pipeline = _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater")
        else:
            pipeline = self._PIPELINES.get(normalized_country)
        if pipeline is None:
            raise ValueError(
                f"Unsupported country: {normalized_country or country_code}. "
                f"Available: {self.supported_country_text()}"
            )

        implementation = (task.input_data or {}).get("pipeline_impl")
        if implementation not in {None, "v2"}:
            raise ValueError(f"Unsupported pipeline implementation: {implementation}")
        if pipeline.handler_name != "_execute_cn_cdc":
            updater = self._make_updater(
                pipeline.updater_name or "", normalized_country
            )
            self._configure_monthly_runtime_policy(
                updater,
                country_code=normalized_country,
                source=source,
                include_current_month=include_current_month,
                revision_window_months=revision_window_months,
            )
            if normalized_country == "BR" or normalized_country.startswith("BR-"):
                start_year = (
                    task.input_data.get("start_year")
                    if isinstance(task.input_data, dict)
                    else None
                )
                result = await self._execute_br_monthly(
                    task=task,
                    source=source,
                    force=force,
                    process=process,
                    save_raw=save_raw,
                    fill_missing=fill_missing,
                    start_year=start_year,
                    updater=updater,
                )
            else:
                handler = getattr(self, pipeline.handler_name)
                result = await handler(
                    task=task,
                    source=source,
                    force=force,
                    process=process,
                    save_raw=save_raw,
                    fill_missing=fill_missing,
                    updater=updater,
                )
        else:
            result = await self._execute_cn_cdc(
                task=task,
                country_code=normalized_country,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
            )
        return result

    async def fail_current_run(
        self,
        *,
        country_code: str,
        source: str,
        started_after: datetime,
        error: BaseException,
        status: str = "failed",
    ) -> Optional[int]:
        """Finalize the run opened by a failed task invocation.

        Pipelines create their audit row before external I/O.  This bounded
        lookup is safe because crawl tasks are serialized per country and the
        timestamp fence prevents an older orphan from being rewritten when the
        current run failed before it could create its own audit row.
        """

        if status not in {"failed", "cancelled"}:
            raise ValueError(f"Unsupported CrawlRun terminal status: {status}")
        normalized_country = str(country_code or "").strip().upper()
        normalized_source = str(source or "all").strip().lower()
        fence = started_after
        if fence.tzinfo is None:
            fence = fence.replace(tzinfo=timezone.utc)
        else:
            fence = fence.astimezone(timezone.utc)
        async with get_database() as db:
            run = (
                await db.execute(
                    select(CrawlRun)
                    .where(
                        CrawlRun.country_code == normalized_country,
                        CrawlRun.source == normalized_source,
                        CrawlRun.status == "running",
                        CrawlRun.started_at >= fence,
                    )
                    .order_by(CrawlRun.started_at.desc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run is None:
                return None
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)[-12000:]
            metadata = dict(run.metadata_ or {})
            metadata["failed_closed"] = status == "failed"
            metadata["terminal_status"] = status
            run.metadata_ = metadata
            run_id = run.id
            await db.commit()
        return run_id

    @staticmethod
    def _configure_monthly_runtime_policy(
        updater,
        *,
        country_code: str,
        include_current_month: Optional[bool],
        revision_window_months: Optional[int],
        source: str = "",
    ) -> None:
        """Apply task/control-plane month policy to a compatible updater."""

        config = get_country_bootstrap_config(country_code)
        crawler_config = (
            config.get("crawler_config", {}) if isinstance(config, dict) else {}
        )
        source_policies = crawler_config.get("source_policies", {})
        source_policy = (
            source_policies.get(str(source or "").strip().casefold(), {})
            if isinstance(source_policies, dict)
            else {}
        )
        if not isinstance(source_policy, dict):
            source_policy = {}
        effective_config = {**crawler_config, **source_policy}
        if include_current_month is None:
            raw_current = effective_config.get("default_include_current_month", False)
            if isinstance(raw_current, str):
                effective_current = raw_current.strip().casefold() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            else:
                effective_current = bool(raw_current)
        else:
            effective_current = bool(include_current_month)

        weekly_window = hasattr(updater, "refresh_recent_weeks")
        configured_window = (
            effective_config.get(
                "default_revision_window",
                effective_config.get("refresh_recent_weeks", 12),
            )
            if weekly_window
            else effective_config.get(
                "default_revision_window",
                effective_config.get("refresh_recent_months", 3),
            )
        )
        try:
            effective_window = int(
                revision_window_months
                if revision_window_months is not None
                else configured_window
            )
        except (TypeError, ValueError):
            effective_window = 12 if weekly_window else 3
        effective_window = max(1, min(52 if weekly_window else 24, effective_window))

        if hasattr(updater, "include_current_month"):
            updater.include_current_month = effective_current
        if hasattr(updater, "refresh_recent_months"):
            updater.refresh_recent_months = effective_window
        if hasattr(updater, "refresh_recent_weeks"):
            updater.refresh_recent_weeks = effective_window

    async def _execute_cn_province(self, *, task, source, force, process, save_raw, fill_missing, updater):
        from src.services.ingestion.countries import cn_province
        from src.services.ingestion.runner import run_surveillance
        options = {
            "country_code": updater.country_code,
            "force": bool(force or fill_missing),
            "start_year": (task.input_data or {}).get("start_year"),
            "recent_months": updater.refresh_recent_months,
        }
        return await run_surveillance(
            self, task=task, updater=updater, connector=cn_province,
            source=source, force=force, process=process,
            strategy=cn_province.ProvinceCommitStrategy(), connector_options=options,
        )

    async def _execute_cn_cdc(
        self,
        *,
        task: Task,
        country_code: str,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import cn
        from src.services.ingestion.runner import run_surveillance
        return await cn.run(self, task=task, source=source, force=force, process=process, fill_missing=fill_missing)


    async def _execute_us_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import us
        from src.services.ingestion.runner import run_surveillance
        async with get_database() as db:
            options = await us.plan_collection(
                db, country_code=updater.country_code, source=source,
                force=force, fill_missing=fill_missing,
            )
        return await run_surveillance(self, task=task, updater=updater, connector=us, source=source, force=force, process=process, connector_options={
            **options, "subdivision": updater.country_code != "US", "task_uuid": task.task_uuid,
        })


    async def _execute_jp_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import jp
        from src.services.ingestion.runner import run_surveillance
        return await run_surveillance(self, task=task, updater=updater, connector=jp, source=source, force=force, process=process, connector_options={"output_csv": updater.output_csv, "force": force, "fill_missing": fill_missing})


    async def _execute_configured_monthly(self, country_code: str, **kwargs) -> CrawlResult:
        """Dispatch explicit monthly source contracts to the three-stage runner."""
        from importlib import import_module
        from src.services.crawl_pipelines.monthly import CONFIGS, _months_to_fetch
        from src.services.ingestion.runner import run_surveillance

        modules = {"AU": "au", "NZ": "nz", "TW": "tw", "HK": "hk", "FI": "fi", "CA-ON": "ca_on", "SE": "se", "AT": "at"}
        if country_code not in modules:
            raise ValueError(f"No staged monthly contract for {country_code}")
        config = CONFIGS[country_code]
        if kwargs["fill_missing"] and not config.supports_fill_missing:
            raise ValueError(f"{country_code} publishes a complete snapshot; fill_missing is not supported")
        updater, task = kwargs["updater"], kwargs["task"]
        months = await _months_to_fetch(config, updater, fill_missing=kwargs["fill_missing"], force=kwargs["force"], get_database=get_database)
        options = {"months": months, "output_csv": updater.output_csv}
        if country_code == "AU":
            options.update(country_code=updater.country_code, include_current_month=updater.include_current_month, refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "AT":
            options.update(force=kwargs["force"], refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "FI":
            options.update(include_current_month=updater.include_current_month, refresh_recent_months=updater.refresh_recent_months, backfill_history=bool((task.input_data or {}).get("backfill_history")))
        elif country_code == "SE":
            options.update(country_code=updater.country_code, include_current_month=updater.include_current_month, force=kwargs["force"], refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "CA-ON":
            options.update(force=kwargs["force"], **updater.pipeline_refresh_kwargs(task))
        connector = import_module("src.services.ingestion.countries." + modules[country_code])
        return await run_surveillance(self, task=task, updater=updater, connector=connector, source=kwargs["source"], force=kwargs["force"], process=kwargs["process"], connector_options=options)

    async def _execute_au_monthly(self, **kwargs) -> CrawlResult:
        return await self._execute_configured_monthly("AU", **kwargs)

    async def _execute_nz_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the New Zealand monthly pipeline."""
        return await self._execute_configured_monthly(
            "NZ",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_nz_dhb_annual(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run independent annual DHB observations through durable stages."""
        from src.services.ingestion.countries import nz_dhb
        from src.services.ingestion.runner import run_surveillance
        return await run_surveillance(self, task=task, updater=updater, connector=nz_dhb, source=source, force=force, process=process, connector_options={"country_code": updater.country_code, "output_csv": updater.output_csv})


    async def _execute_tw_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Taiwan, China monthly pipeline."""
        return await self._execute_configured_monthly(
            "TW",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_hk_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Hong Kong monthly pipeline."""
        return await self._execute_configured_monthly(
            "HK",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_fi_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Finland THL national monthly pipeline."""
        return await self._execute_configured_monthly(
            "FI",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_ie_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Dispatch Ireland's current, annual, or archived-weekly source."""
        if str(source or "").strip().casefold() == "hpsc_weekly_archive":
            from src.data.processors import IEWeeklyArchiveUpdater
            from src.services.crawl_pipelines.ie_weekly_archive import (
                execute_ie_weekly_archive_pipeline,
            )

            return await execute_ie_weekly_archive_pipeline(
                self,
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=IEWeeklyArchiveUpdater(),
                get_database=get_database,
                task_manager=task_manager,
                crawl_run_type=CrawlRun,
                result_type=CrawlResult,
                logger=logger,
            )

        if str(source or "").strip().casefold() == "hpsc_annual":
            from src.data.processors import IEAnnualUpdater
            from src.services.crawl_pipelines.ie_annual import (
                execute_ie_annual_pipeline,
            )

            return await execute_ie_annual_pipeline(
                self,
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=IEAnnualUpdater(),
                get_database=get_database,
                task_manager=task_manager,
                crawl_run_type=CrawlRun,
                result_type=CrawlResult,
                logger=logger,
            )

        from src.services.crawl_pipelines.ie import execute_ie_pipeline

        return await execute_ie_pipeline(
            self,
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            logger=logger,
        )

    async def _execute_no_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Norway FHI MSIS national monthly pipeline."""
        from src.services.crawl_pipelines.no import execute_no_pipeline

        return await execute_no_pipeline(
            self,
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            logger=logger,
        )

    async def _execute_se_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Sweden SmiNet internal monthly pipeline."""
        return await self._execute_configured_monthly(
            "SE",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_at_monthly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        return await self._execute_configured_monthly(
            "AT", task=task, source=source, force=force, process=process,
            save_raw=save_raw, fill_missing=fill_missing, updater=updater,
        )

    async def _execute_de_weekly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import de
        today = datetime.now(timezone.utc).date()
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, min(today.year, int(raw_start or updater.full_history_start_year)))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        if force:
            weeks = updater.history_weeks(today=today, start_year=start_year)
        elif fill_missing:
            async with get_database() as db:
                existing = await updater.get_db_week_dates(db)
            weeks = sorted(set(updater.history_weeks(today=today, start_year=start_year)).difference(existing) | set(updater._recent_weeks(today)))
        else:
            weeks = updater._recent_weeks(today)
        return await run_surveillance(
            self, task=task, updater=updater, connector=de, source=source,
            force=force, process=process,
            connector_options={"country_code": updater.country_code, "weeks": weeks, "force": force, "export_url_template": updater.export_url_template, "history_csv": updater.output_csv},
        )

    async def _execute_sg_weekly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import sg
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, min(datetime.now(timezone.utc).year, int(raw_start or updater.full_history_start_year)))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=sg, source=source,
            force=force, process=process,
            connector_options={"force": force, "fill_missing": fill_missing, "start_year": start_year, "history_csv": updater.output_csv},
        )

    async def _execute_ecdc_annual(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import ecdc
        from src.services.ingestion.strategies import EcdcWindowCommitStrategy
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, int(raw_start or updater.full_history_start_year))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=ecdc, source=source,
            force=force, process=process, strategy=EcdcWindowCommitStrategy(),
            strategy_options={"start_year": start_year},
            connector_options={"country_code": updater.country_code, "start_year": start_year, "history_csv": updater.output_csv},
        )

    async def _execute_ca_on_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run Ontario through the shared country/region monthly pipeline."""
        return await self._execute_configured_monthly(
            "CA-ON",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_ca_national_annual(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the national Canadian CNDSS annual authoritative snapshot."""
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import ca_national
        from src.services.ingestion.strategies import CanadaCndssWindowCommitStrategy
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, int(raw_start or updater.full_history_start_year))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=ca_national, source=source,
            force=force, process=process, strategy=CanadaCndssWindowCommitStrategy(),
            strategy_options={"start_year": start_year}, connector_options={"start_year": start_year},
        )

    async def _execute_br_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        start_year: Optional[int] = None,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import br
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater, start_year)
        months = await plan_months(task, updater, force=force, fill_missing=fill_missing, start_year=boundary)
        months = updater._resolve_requested_months(months)
        options = {"months": months, "history_csv": updater.output_csv, "start_year": boundary, "country_code": updater.country_code}
        return await run_surveillance(self, task=task, updater=updater, connector=br, source=source, force=force, process=process, connector_options=options)




    async def _execute_kr_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import kr
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater)
        months = await plan_months(task, updater, force=force, fill_missing=fill_missing, start_year=boundary)
        months = updater._resolve_requested_months(months)
        options = {"months": months, "history_csv": updater.output_csv, "source_file": (task.input_data or {}).get("source_file"), "source_dir": (task.input_data or {}).get("source_dir")}
        return await run_surveillance(self, task=task, updater=updater, connector=kr, source=source, force=force, process=process, connector_options=options)



    async def _execute_ch_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import ch
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater)
        months = await plan_months(task, updater, force=False, fill_missing=fill_missing and not force, start_year=boundary)
        options = {"country_code": updater.country_code, "history_csv": updater.output_csv, "months": months, "force": force, "start_year": boundary if force else None}
        return await run_surveillance(self, task=task, updater=updater, connector=ch, source=source, force=force, process=process, connector_options=options)



    async def _execute_is_mixed(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run Iceland's mixed annual/monthly/weekly surveillance pipeline."""
        from src.core.source_scopes import canonicalize_task_source, validate_iceland_crawl_options
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import is_ as connector
        normalized = canonicalize_task_source(source, country_code="IS")
        start_year = (task.input_data or {}).get("start_year")
        validate_iceland_crawl_options(country_code="IS", source=normalized, fill_missing=fill_missing, start_year=start_year)
        strategy = connector.IcelandCurrentCommitStrategy()
        if normalized in connector.HISTORY_SOURCES:
            from src.data.processors.is_history import IcelandHistoryProcessor, HISTORY_SOURCE_NAME, HISTORY_SOURCE_ID, LEGACY_SOURCE_NAME, LEGACY_SOURCE_ID
            updater = IcelandHistoryProcessor()
            updater.ontology_source_id = {HISTORY_SOURCE_NAME: HISTORY_SOURCE_ID, LEGACY_SOURCE_NAME: LEGACY_SOURCE_ID}
            updater.series_geography_key = "country:IS:national"
            strategy = connector.IcelandHistoryCommitStrategy()
        return await run_surveillance(
            self, task=task, updater=updater, connector=connector,
            source=normalized, force=force, process=process, strategy=strategy,
            connector_options={"fill_missing": fill_missing, "start_year": start_year, "history_csv": getattr(updater, "output_csv", None)},
        )

    @staticmethod
    def _source_distribution(results) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in results:
            meta = item.metadata or {}
            source = str(meta.get("source") or "Unknown")
            counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[0]))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _add_raw_archive_entry(
        self,
        task_uuid: str,
        *,
        save_raw: bool,
        raw_dir: Path,
    ) -> None:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title="Raw Archive",
            content=(
                f"Save raw enabled: {'Yes' if save_raw else 'No'}\n"
                + (f"Archive path: {raw_dir}" if save_raw else "Archive path: disabled")
            ),
            content_type="text",
        )

    async def _finish_crawl_run(
        self,
        run_id: Optional[int],
        new_reports: int,
        processed: int,
        records: int,
        status: str = "completed",
        error: Optional[str] = None,
    ) -> None:
        if run_id is None:
            return
        try:
            async with get_database() as db:
                run = await db.get(CrawlRun, run_id)
                if run:
                    run.status = status
                    run.finished_at = datetime.now(timezone.utc)
                    run.new_reports = new_reports
                    run.processed_reports = processed
                    run.total_records = records
                    if error:
                        run.error_message = error
                    await db.commit()
        except Exception as e:
            logger.warning(f"Could not update CrawlRun {run_id}: {e}")
