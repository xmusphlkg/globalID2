"""
Crawl Service

Encapsulates all business logic for data crawling + processing,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core import get_database, get_logger
from src.core.country_library import get_country_bootstrap_config
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock
from src.core.task_manager import task_manager
from src.data.storage import (
    SeriesObservationQualityError,
    SeriesObservationQualityPolicy,
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
        "NZ": _PipelineSpec("_execute_nz_monthly", "NZMonthlyUpdater"),
        "TW": _PipelineSpec("_execute_tw_monthly", "TWMonthlyUpdater"),
        "HK": _PipelineSpec("_execute_hk_monthly", "HKMonthlyUpdater"),
        "BR": _PipelineSpec("_execute_br_monthly", "BRMonthlyUpdater"),
        "KR": _PipelineSpec("_execute_kr_monthly", "KRMonthlyUpdater"),
        "CH": _PipelineSpec("_execute_ch_monthly", "CHMonthlyUpdater"),
    }
    _SERIES_SOURCE_IDS: Dict[str, str | Dict[str, str]] = {
        "US": {
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
        "JP": "SRC_JP_NIID",
        "AU": "SRC_AU_NINDSS",
        "NZ": "SRC_NZ_PHS",
        "TW": "SRC_TW_NIDSS",
        "HK": "SRC_HK_CHP",
        "KR": "SRC_KR_KDCA",
        "BR": "SRC_BR_SINAN",
        "CH": "SRC_CH_FOPH_IDD",
    }

    @classmethod
    def supported_country_codes(cls) -> List[str]:
        """Return country codes with implemented crawl pipelines."""
        return list(cls._PIPELINES.keys())

    @classmethod
    def supported_country_text(cls) -> str:
        """Human-readable supported country list for validation errors."""
        return ", ".join(cls.supported_country_codes())

    @staticmethod
    def _make_updater(updater_name: str):
        from src.data.processors import (
            AUMonthlyUpdater,
            BRMonthlyUpdater,
            CHMonthlyUpdater,
            HKMonthlyUpdater,
            JPWeeklyUpdater,
            KRMonthlyUpdater,
            NZMonthlyUpdater,
            TWMonthlyUpdater,
            USWeeklyUpdater,
        )

        updaters = {
            "AUMonthlyUpdater": AUMonthlyUpdater,
            "BRMonthlyUpdater": BRMonthlyUpdater,
            "CHMonthlyUpdater": CHMonthlyUpdater,
            "HKMonthlyUpdater": HKMonthlyUpdater,
            "JPWeeklyUpdater": JPWeeklyUpdater,
            "KRMonthlyUpdater": KRMonthlyUpdater,
            "NZMonthlyUpdater": NZMonthlyUpdater,
            "TWMonthlyUpdater": TWMonthlyUpdater,
            "USWeeklyUpdater": USWeeklyUpdater,
        }
        return updaters[updater_name]()

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

        quality_policy = CrawlService._series_quality_policy(updater)
        country_code = str(getattr(updater, "country_code", "") or "").upper()
        source_id = getattr(updater, "ontology_source_id", None)
        if source_id is None:
            source_id = CrawlService._SERIES_SOURCE_IDS.get(country_code)
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
        series_store = SeriesObservationStore()
        source_series_rows = rows if series_rows is None else series_rows
        if bool(getattr(updater, "series_registered_rows_only", False)):
            selection = series_store.select_registry_rows(
                source_series_rows,
                updater.country_code,
                source_id=source_id,
            )
            if source_series_rows and not selection.rows:
                raise ValueError(
                    "Registry-scoped dual write selected no non-missing source rows"
                )
            source_series_rows = selection.rows
            logger.info(
                "Disease source-series Registry selection | country={} "
                "selected={} omitted_unregistered={} omitted_missing={}",
                updater.country_code,
                len(selection.rows),
                selection.skipped_unregistered,
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
                updater.country_code,
                source_id=source_id,
                geography_key=geography_key,
                quality_policy=quality_policy,
            )
        except SeriesObservationQualityError as exc:
            logger.error(
                "Disease source-series batch rejected before transaction commit | "
                "country={} mode={} report={}",
                updater.country_code,
                quality_policy.mode,
                json.dumps(exc.report.to_dict(), ensure_ascii=False, sort_keys=True),
            )
            raise
        quality_report = series_result.quality_report
        logger.info(
            "Disease source-series dual write complete | country={} upserted={} "
            "unmatched={} ambiguous={} invalid={} registry_not_synced={} "
            "quality_mode={} quality_issues={} quality_highest={}",
            updater.country_code,
            series_result.upserted,
            series_result.skipped_unmatched,
            series_result.skipped_ambiguous,
            series_result.skipped_invalid,
            series_result.skipped_registry_not_synced,
            quality_policy.mode,
            len(quality_report.issues),
            quality_report.highest_severity or "none",
        )
        if quality_report.issues:
            logger.warning(
                "Disease source-series quality anomalies | country={} report={}",
                updater.country_code,
                json.dumps(
                    quality_report.to_dict(), ensure_ascii=False, sort_keys=True
                ),
            )
        return import_result

    @staticmethod
    def _series_quality_policy(updater) -> SeriesObservationQualityPolicy:
        """Resolve per-country guard settings with an operational env override."""

        country_code = str(getattr(updater, "country_code", "") or "").upper()
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
        elif country_code in CrawlService._SERIES_SOURCE_IDS:
            raw_policy.setdefault("registry_coverage", "required")

        country_env = (
            os.getenv(f"GLOBALID_{country_code}_SERIES_QUALITY_MODE")
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
    ) -> CrawlResult:
        """
        Run the full crawl pipeline and return a summary.

        Progress is reported via task_manager (0 → 100 %).
        Raises on unrecoverable errors (caller handles via task_lifecycle).
        """
        normalized_country = (country_code or "").strip().upper()
        pipeline = self._PIPELINES.get(normalized_country)
        if pipeline is None:
            raise ValueError(
                f"Unsupported country: {normalized_country or country_code}. "
                f"Available: {self.supported_country_text()}"
            )

        if pipeline.handler_name != "_execute_cn_cdc":
            updater = self._make_updater(pipeline.updater_name or "")
            if normalized_country == "BR":
                start_year = (
                    task.input_data.get("start_year")
                    if isinstance(task.input_data, dict)
                    else None
                )
                return await self._execute_br_monthly(
                    task=task,
                    source=source,
                    force=force,
                    process=process,
                    save_raw=save_raw,
                    fill_missing=fill_missing,
                    start_year=start_year,
                    updater=updater,
                )

            handler = getattr(self, pipeline.handler_name)
            return await handler(
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=updater,
            )

        return await self._execute_cn_cdc(
            task=task,
            country_code=normalized_country,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
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
        """Compatibility entry point for the China CDC pipeline."""
        from src.data.crawlers import ChinaCDCCrawler
        from src.data.processors import DataProcessor
        from src.services.crawl_pipelines.cn import execute_cn_pipeline

        return await execute_cn_pipeline(
            self,
            task=task,
            country_code=country_code,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            crawler_type=ChinaCDCCrawler,
            processor_type=DataProcessor,
            logger=logger,
        )

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
        """Compatibility entry point for the US CDC pipeline."""
        from src.services.crawl_pipelines.us import execute_us_pipeline

        return await execute_us_pipeline(
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
        """Compatibility entry point for the Japan NIID pipeline."""
        from src.services.crawl_pipelines.jp import execute_jp_pipeline

        return await execute_jp_pipeline(
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

    async def _execute_configured_monthly(self, country_code: str, **kwargs) -> CrawlResult:
        """Delegate a monthly country pipeline while preserving service hooks."""
        from src.services.crawl_pipelines.monthly import CONFIGS, execute_monthly_pipeline

        return await execute_monthly_pipeline(
            self,
            config=CONFIGS[country_code],
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            logger=logger,
            **kwargs,
        )

    async def _execute_au_monthly(
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
        """Compatibility entry point for the Australia monthly pipeline."""
        return await self._execute_configured_monthly(
            "AU",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

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
        """Compatibility entry point for the New Zealand monthly pipeline."""
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
        """Compatibility entry point for the Taiwan, China monthly pipeline."""
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
        """Compatibility entry point for the Hong Kong monthly pipeline."""
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
        """Compatibility entry point for the Brazil SINAN pipeline."""
        from src.data.crawlers import BrazilSINANCrawler
        from src.services.crawl_pipelines.br import execute_br_pipeline

        return await execute_br_pipeline(
            self,
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            start_year=start_year,
            updater=updater,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            crawler_type=BrazilSINANCrawler,
            logger=logger,
        )

    @staticmethod
    def _chunk_months(
        values: List[Tuple[int, int]],
        chunk_size: int,
    ) -> List[List[Tuple[int, int]]]:
        """Compatibility entry point for Brazil's batch planner."""
        from src.services.crawl_pipelines.br import chunk_months

        return chunk_months(values, chunk_size)

    @staticmethod
    def _br_history_start_year(
        task: Task,
        updater,
        default_start_year: Optional[int] = None,
    ) -> int:
        """Compatibility entry point for Brazil's history boundary."""
        from src.services.crawl_pipelines.br import history_start_year

        return history_start_year(task, updater, default_start_year)

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
        """Compatibility entry point for the Korea KDCA pipeline."""
        from src.services.crawl_pipelines.kr import execute_kr_pipeline

        return await execute_kr_pipeline(
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

    @staticmethod
    def _kr_history_start_year(task: Task, updater) -> int:
        """Compatibility entry point for Korea's history boundary."""
        from src.services.crawl_pipelines.kr import history_start_year

        return history_start_year(task, updater)


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
        """Compatibility entry point for the Switzerland FOPH pipeline."""
        from src.services.crawl_pipelines.ch import execute_ch_pipeline

        return await execute_ch_pipeline(
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

    @staticmethod
    def _ch_history_start_year(task: Task, updater) -> int:
        """Compatibility entry point for Switzerland's history boundary."""
        from src.services.crawl_pipelines.ch import history_start_year

        return history_start_year(task, updater)

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
