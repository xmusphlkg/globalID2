"""
Crawl Service

Encapsulates all business logic for data crawling + processing,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
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
        from src.data.crawlers import ChinaCDCCrawler
        from src.data.processors import DataProcessor

        crawler = ChinaCDCCrawler()
        raw_dir = Path("data/raw") / country_code.lower()
        run_started = perf_counter()

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                f"Country: {country_code}\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        # ── Create CrawlRun record ────────────────────────────────────────────
        run_id: Optional[int] = None
        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code=country_code,
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        # ── Phase 1: Fetch data list (0 → 30 %) ──────────────────────────────
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching Data List",
            content="Fetching available data list...",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        phase1_started = perf_counter()
        results = await crawler.crawl(source=source, force=force, fill_missing=fill_missing)
        phase1_elapsed = perf_counter() - phase1_started
        await task_manager.update_task_progress(task.task_uuid, 30)

        crawl_stats = getattr(crawler, "last_crawl_stats", {}) or {}
        max_date = crawl_stats.get("max_date") or "none"
        missing_months = crawl_stats.get("missing_months") or []
        missing_count = int(crawl_stats.get("missing_months_count") or 0)
        missing_preview = ", ".join(missing_months[:8]) if missing_months else "none"
        if missing_count > 8:
            missing_preview = f"{missing_preview} ... (+{missing_count - 8})"

        source_counts = self._source_distribution(results)
        source_summary = ", ".join(f"{k}: {v}" for k, v in source_counts.items()) or "none"
        total_candidates = int(crawl_stats.get("total_candidates") or 0)

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Source filter: {source}\n"
            f"Source candidates found: {total_candidates}\n"
            f"Reports selected for processing: {len(results)}\n"
            f"Selected distribution: {source_summary}\n"
                f"DB latest date: {max_date}\n"
                f"Missing months in DB: {missing_count}\n"
                f"Missing month sample: {missing_preview}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if not results:
            await self._finish_crawl_run(run_id, new_reports=0, processed=0, records=0)
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Crawl Completed",
                content=(
                    "No new data found after incremental check.\n"
                    f"Source filter: {source}\n"
                    f"DB latest date: {max_date}\n"
                    f"Missing months in DB: {missing_count}\n"
                    f"Force mode: {'Yes' if force else 'No'}\n"
                    f"Fill missing months: {'Yes' if fill_missing else 'No'}"
                ),
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 100)
            return CrawlResult(0, 0, 0, run_id)

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Data List Retrieved",
            content=f"Found {len(results)} {'new ' if not force else ''}report(s).",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 40)

        # ── Phase 2: Process data (40 → 80 %) ────────────────────────────────
        total_records = 0
        processed_dfs = []

        if process and results:
            phase2_started = perf_counter()
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 2/3: Processing Data",
                content=f"Processing {len(results)} report(s)...",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 50)

            processor = DataProcessor(
                output_dir=Path("data/processed") / country_code.lower(),
                country_code=country_code.lower(),
            )

            async def _progress(current, total, message):
                pct = 50 + int((current / total) * 30) if total else 50
                await task_manager.update_task_progress(task.task_uuid, pct)
                if current % 10 == 0 or current == total:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title="Processing Progress",
                        content=(
                            f"{current}/{total} reports processed"
                            + (f"\nLast step: {message}" if message else "")
                        ),
                        content_type="text",
                    )

            processed_dfs = await processor.process_crawler_results(
                results,
                save_to_file=True,
                save_raw=save_raw,
                crawl_run_id=run_id,
                raw_dir=raw_dir,
                progress_callback=_progress,
            )
            total_records = sum(len(df) for df in processed_dfs)
            phase2_elapsed = perf_counter() - phase2_started

            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Phase 2/3 Complete",
                content=(
                    f"Processed datasets: {len(processed_dfs)}/{len(results)}\n"
                    f"Records stored: {total_records}\n"
                    f"Duration: {phase2_elapsed:.1f}s"
                ),
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 80)

        elif save_raw and results:
            # Save raw pages only
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 3/3: Saving Raw Data",
                content="Saving raw pages...",
                content_type="text",
            )
            processor = DataProcessor(
                output_dir=Path("data/processed") / country_code.lower(),
                country_code=country_code.lower(),
            )
            await processor.save_raw_pages(results, crawl_run_id=run_id, raw_dir=raw_dir)
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Raw Data Saved",
                content=f"Saved raw pages for {len(results)} report(s) to {raw_dir}",
                content_type="text",
            )

        # ── Finalise CrawlRun ─────────────────────────────────────────────────
        processed_count = len(processed_dfs) if process else 0
        await self._finish_crawl_run(run_id, len(results), processed_count, total_records)

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"New reports: {len(results)}\n"
                f"Processed: {processed_count}\n"
                f"Records: {total_records}\n"
                f"Source distribution: {source_summary}\n"
                f"Total duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(len(results), processed_count, total_records, run_id)

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
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "us"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: US\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="US",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching Data List",
            content=(
                f"Fetching US CDC source '{source}' "
                "(NNDSS weekly and/or NHSS annual HIV data)..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        phase1_started = perf_counter()
        fetched = updater.fetch_latest(source=source)
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.latest_date.isoformat() if fetched.latest_date else "none"
        source_latest_detail = ", ".join(
            f"{name}: {latest.isoformat() if latest else 'none'}"
            for name, latest in fetched.latest_by_source.items()
        ) or "none"
        source_rows = len(fetched.rows)

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Source rows fetched: {source_rows}\n"
                f"Source latest date: {source_latest}\n"
                f"Latest by source: {source_latest_detail}\n"
                f"Source endpoint: {fetched.source_ref}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Checking Incremental Updates",
            content=(
                "Checking each source independently; NNDSS recent weeks and all "
                "NHSS annual revisions remain eligible for upsert..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    series_rows=fetched.series_rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Imported rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing US surveillance update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, fetched source rows only."
        elif imported_new_data:
            summary_message = "US surveillance data imported or revised successfully."
        elif force:
            summary_message = "Force mode completed; no rows were upserted after mapping."
        else:
            summary_message = "No eligible US source rows were detected, import skipped."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Fetched rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "jp"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: JP\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="JP",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Refreshing JP Source",
            content="Refreshing JP current weekly CSV output and preparing incremental update rows...",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(source=source, run_external=False, force=force)
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Source rows prepared (TOTAL): {source_rows}\n"
                f"Source latest week date: {source_latest}\n"
                f"Current standardized CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="JP Update Logs",
                content="\n\n".join(fetched.script_logs[-8:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Checking Incremental Updates",
            content="Comparing JP source latest week with JP latest date in database...",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Imported rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing JP weekly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed JP source only."
        elif imported_new_data:
            summary_message = "New JP weekly data imported successfully."
        elif force:
            summary_message = "Force mode completed; no rows were upserted after filtering/mapping."
        else:
            summary_message = "No newer JP weekly data detected, import skipped."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "au"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: AU\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="AU",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching AU Source",
            content=(
                "Launching Playwright to capture Power BI token, then fetching NINDSS data...\n"
                f"Mode: {'fill missing months' if fill_missing else 'recent 3 months'}"
                + (" + force re-fetch" if force else "")
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        # Determine which months to request from the crawler.
        # AU data is dynamically revised, so we always re-fetch the most recent
        # 3 months.  When fill_missing=True we also add any (year, month) pairs
        # that are completely absent from the database.
        months_to_fetch = None  # None → crawler will default to last 3 months
        if fill_missing or force:
            now_dt = datetime.now()
            # Always include last 3 months
            recent: list = []
            for delta in range(3):
                m = now_dt.month - delta
                y = now_dt.year
                if m <= 0:
                    m += 12
                    y -= 1
                recent.append((y, m))
            months_set = set(recent)

            if fill_missing:
                # Also pick up historical months missing from the DB
                async with get_database() as db:
                    existing_months = await updater.get_db_months(db)
                # Build the full expected range: 2000 → current month
                start_year = 2000
                for fy in range(start_year, now_dt.year + 1):
                    for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                        if (fy, fm) not in existing_months:
                            months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        months_fetched_count = len(months_to_fetch) if months_to_fetch is not None else 3

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_count}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="AU Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting AU Data",
            content=(
                "AU data is dynamically revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing AU monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed AU source only."
        elif imported_new_data:
            summary_message = f"AU data upserted: {imported} rows across {months_fetched_count} month(s) updated in database."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "AU source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        """Execute the NZ monthly crawl pipeline (mirrors AU pattern)."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "nz"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: NZ\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="NZ",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching NZ Source",
            content=(
                "Scraping PHF Science Digital Library for monthly notifiable disease reports...\n"
                f"Mode: {'all history' if force else ('fill missing months' if fill_missing else 'recent 3 months')}"
                + (" + force re-fetch" if force else "")
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        # Determine which months to request
        months_to_fetch = None
        if fill_missing or force:
            now_dt = datetime.now()
            recent: list = []
            for delta in range(3):
                m = now_dt.month - delta
                y = now_dt.year
                if m <= 0:
                    m += 12
                    y -= 1
                recent.append((y, m))
            months_set = set(recent)

            if force:
                # NZ archive backfills need a full re-parse when parser logic changes.
                start_year = 2016
                for fy in range(start_year, now_dt.year + 1):
                    for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                        months_set.add((fy, fm))
            elif fill_missing:
                async with get_database() as db:
                    existing_months = await updater.get_db_months(db)
                # NZ data available from ~2016 onwards on PHF Science
                start_year = 2016
                for fy in range(start_year, now_dt.year + 1):
                    for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                        if (fy, fm) not in existing_months:
                            months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        months_fetched_count = len(months_to_fetch) if months_to_fetch is not None else 3

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_count}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="NZ Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting NZ Data",
            content=(
                "NZ data is provisional — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing NZ monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed NZ source only."
        elif imported_new_data:
            summary_message = f"NZ data upserted: {imported} rows across {months_fetched_count} month(s) updated in database."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "NZ source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        """Execute the Taiwan, China CDC NIDSS monthly open-data pipeline."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "tw"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: TW\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="TW",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching TW Source",
            content=(
                "Fetching Taiwan, China CDC NIDSS open-data monthly CSVs...\n"
                f"Mode: {'fill missing months' if fill_missing else 'recent 3 months'}"
                + (" + force re-fetch" if force else "")
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        months_to_fetch = None
        if fill_missing or force:
            now_dt = datetime.now()
            recent: list = []
            for delta in range(3):
                m = now_dt.month - delta
                y = now_dt.year
                if m <= 0:
                    m += 12
                    y -= 1
                recent.append((y, m))
            months_set = set(recent)

            if fill_missing:
                async with get_database() as db:
                    existing_months = await updater.get_db_months(db)
                start_year = 1998
                for fy in range(start_year, now_dt.year + 1):
                    for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                        if (fy, fm) not in existing_months:
                            months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        months_fetched_count = len(months_to_fetch) if months_to_fetch is not None else 3

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_count}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="TW Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting TW Data",
            content=(
                "Taiwan, China NIDSS data may be revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing Taiwan, China monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed Taiwan, China source only."
        elif imported_new_data:
            summary_message = f"Taiwan, China NIDSS data upserted: {imported} rows across {months_fetched_count} month(s)."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "Taiwan, China source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        """Execute the Hong Kong CHP monthly open-data pipeline."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "hk"
        run_id: Optional[int] = None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: HK\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="HK",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching HK Source",
            content=(
                "Fetching Hong Kong CHP annual notifiable disease CSVs...\n"
                f"Mode: {'fill missing months' if fill_missing else 'recent available months'}"
                + (" + force re-fetch" if force else "")
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        months_to_fetch = None
        if fill_missing or force:
            now_dt = datetime.now()
            recent: list = []
            for delta in range(6):
                month = now_dt.month - delta
                year = now_dt.year
                if month <= 0:
                    month += 12
                    year -= 1
                recent.append((year, month))
            months_set = set(recent)

            if fill_missing:
                async with get_database() as db:
                    existing_months = await updater.get_db_months(db)
                start_year = 1997
                for fy in range(start_year, now_dt.year + 1):
                    last_month = 12 if fy < now_dt.year else now_dt.month
                    for fm in range(1, last_month + 1):
                        if (fy, fm) not in existing_months:
                            months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        months_fetched_label = (
            str(len(months_to_fetch))
            if months_to_fetch is not None
            else "latest available 3"
        )

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_label}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="HK Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting HK Data",
            content=(
                "Hong Kong CHP data may be revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing Hong Kong monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed Hong Kong source only."
        elif imported_new_data:
            summary_message = f"Hong Kong CHP data upserted: {imported} rows across {months_fetched_label} month(s)."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "Hong Kong source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

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
        """Execute the Brazil DATASUS SINAN monthly open-data pipeline."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "br"
        run_id: Optional[int] = None
        history_start_year = self._br_history_start_year(
            task, updater, default_start_year=start_year
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: BR\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
                f"History Start Year: {history_start_year}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="BR",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching BR Source",
            content=(
                "Fetching Brazil DATASUS SINAN DBC files and aggregating national monthly rows...\n"
                + (
                    f"Mode: {'fill missing months' if fill_missing else 'recent months'} "
                    f"(refresh window: {updater.refresh_recent_months}, start: {history_start_year})"
                    if not force
                    else f"Mode: full history refresh from {history_start_year}"
                )
                + (" + force re-fetch" if force else "")
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        months_to_fetch = None
        if fill_missing or force:
            if force:
                months_to_fetch = updater.history_months(start_year=history_start_year)
            else:
                now_dt = datetime.now()
                recent: list[tuple[int, int]] = []
                for delta in range(max(1, updater.refresh_recent_months)):
                    m = now_dt.month - delta
                    y = now_dt.year
                    if m <= 0:
                        m += 12
                        y -= 1
                    recent.append((y, m))
                months_set = set(recent)

                async with get_database() as db:
                    existing_months = await updater.get_db_months(db)
                for fy in range(history_start_year, now_dt.year + 1):
                    for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                        if (fy, fm) not in existing_months:
                            months_set.add((fy, fm))

                months_to_fetch = sorted(months_set)

        requested_batches: list[Optional[List[tuple[int, int]]]]
        if months_to_fetch is None:
            requested_batches = [None]
        else:
            month_batch_size = (
                int(getattr(updater, "history_batch_months", 0) or 0)
                if fill_missing or force
                else 0
            )
            requested_batches = (
                self._chunk_months(months_to_fetch, month_batch_size)
                if month_batch_size > 0
                else [months_to_fetch]
            )

        use_batch_csv_write = len(requested_batches) > 1
        shared_crawler = None
        if use_batch_csv_write:
            from src.data.crawlers import BrazilSINANCrawler

            shared_crawler = BrazilSINANCrawler(
                save_raw=save_raw,
                raw_dir=raw_dir if save_raw else None,
            )

        phase1_started = perf_counter()
        merged_rows: list[dict] = []
        fetched_batch_messages: list[str] = []
        failed_batches: list[tuple[int, Exception]] = []
        source_latest: Optional[date] = None
        source_csv: Optional[Path] = None
        source_latest_date: Optional[date] = None
        for idx, batch_months in enumerate(requested_batches, start=1):
            batch_start = perf_counter()
            batch_label_len = (
                len(batch_months)
                if batch_months is not None
                else int(max(1, updater.refresh_recent_months))
            )
            try:
                fetched = updater.refresh_source(
                    source=source,
                    run_external=False,
                    force=force or False,
                    months=batch_months,
                    save_raw=save_raw,
                    raw_dir=raw_dir if save_raw else None,
                    load_csv_fallback=not use_batch_csv_write,
                    write_csv=not use_batch_csv_write,
                    crawler=shared_crawler,
                )
            except Exception as exc:
                failed_batches.append((idx, exc))
                logger.warning(
                    f"[BR] refresh batch failed | batch={idx}/{len(requested_batches)} "
                    f"months={batch_label_len} err={exc}"
                )
                fetched_batch_messages.append(
                    f"[BR] batch {idx}/{len(requested_batches)} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            phase_elapsed = perf_counter() - batch_start
            merged_rows.extend(fetched.rows)
            fetched_batch_messages.append(
                f"[BR] batch {idx}/{len(requested_batches)} success: "
                f"months={batch_label_len} rows={len(fetched.rows)} "
                f"elapsed={phase_elapsed:.1f}s"
            )
            if fetched.source_latest_date is not None and (
                source_latest is None
                or fetched.source_latest_date > source_latest
            ):
                source_latest = fetched.source_latest_date
            source_csv = fetched.source_csv

            batch_progress = 10 + int((idx / max(1, len(requested_batches))) * 20)
            await task_manager.update_task_progress(task.task_uuid, min(30, batch_progress))

            if fetched.script_logs:
                fetched_batch_messages.extend(fetched.script_logs[-2:])

        phase1_elapsed = perf_counter() - phase1_started
        source_rows = len(merged_rows)
        if not merged_rows and failed_batches:
            raise failed_batches[0][1]
        if not merged_rows:
            raise RuntimeError("BR source produced no rows in this run")

        if use_batch_csv_write:
            updater._write_rows_to_output_csv(merged_rows)

        source_latest_date = source_latest
        source_latest_text = source_latest_date.isoformat() if source_latest_date else "none"
        months_fetched_count = len(months_to_fetch) if months_to_fetch is not None else max(
            1,
            int(updater.refresh_recent_months),
        )

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_count}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest_text}\n"
                f"Current national CSV: {source_csv or 'N/A'}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched_batch_messages:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="BR Pipeline Logs",
                content="\n".join(fetched_batch_messages[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting BR Data",
            content=(
                "Brazil SINAN data may be revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    merged_rows,
                    db_latest_date=db_latest,
                    source_latest_date=source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data
                await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest_text}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing Brazil monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed Brazil source only."
        elif imported_new_data:
            summary_message = f"Brazil SINAN data upserted: {imported} rows across {months_fetched_count} month(s)."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "Brazil source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

    @staticmethod
    def _chunk_months(
        values: List[Tuple[int, int]],
        chunk_size: int,
    ) -> List[List[Tuple[int, int]]]:
        if chunk_size <= 0:
            return [values]
        return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]

    @staticmethod
    def _br_history_start_year(
        task: Task,
        updater,
        default_start_year: Optional[int] = None,
    ) -> int:
        raw_value = (
            task.input_data.get("start_year")
            if isinstance(task.input_data, dict)
            else None
        )
        current_year = datetime.now().year
        crawler_default = getattr(updater, "full_history_start_year", None)
        fallback_start_year = (
            int(crawler_default)
            if crawler_default is not None
            else 2000
        )

        if default_start_year is not None:
            try:
                return max(1900, min(int(default_start_year), current_year))
            except (TypeError, ValueError):
                pass

        if raw_value is not None:
            try:
                return max(1900, min(int(raw_value), current_year))
            except (TypeError, ValueError):
                pass

        return max(1900, min(fallback_start_year, current_year))

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
        """Execute the Korea KDCA monthly OpenAPI pipeline."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "kr"
        run_id: Optional[int] = None
        input_data = task.input_data if isinstance(task.input_data, dict) else {}
        source_file = input_data.get("source_file")
        source_dir = input_data.get("source_dir")
        source_file_path = Path(str(source_file)) if source_file else None
        source_dir_path = Path(str(source_dir)) if source_dir else None

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: KR\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
                f"History Start Year: {self._kr_history_start_year(task, updater)}\n"
                f"Download Source File: {source_file_path or 'auto'}\n"
                f"Download Source Dir: {source_dir_path or 'auto'}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="KR",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        history_start_year = self._kr_history_start_year(task, updater)
        if force:
            mode_text = f"full history refresh from {history_start_year}"
        elif fill_missing:
            mode_text = f"fill missing months from {history_start_year}"
        else:
            mode_text = f"recent {updater.refresh_recent_months} months"

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching KR Source",
            content=(
                "Preparing Korea KDCA monthly rows from OpenAPI or portal/KOSIS downloads...\n"
                f"Mode: {mode_text}"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        months_to_fetch = None
        if force:
            months_to_fetch = updater.history_months(start_year=history_start_year)
        elif fill_missing:
            now_dt = datetime.now()
            recent: list = []
            for delta in range(updater.refresh_recent_months):
                m = now_dt.month - delta
                y = now_dt.year
                if m <= 0:
                    m += 12
                    y -= 1
                recent.append((y, m))
            months_set = set(recent)

            async with get_database() as db:
                existing_months = await updater.get_db_months(db)
            for fy in range(history_start_year, now_dt.year + 1):
                for fm in range(1, 13 if fy < now_dt.year else now_dt.month + 1):
                    if (fy, fm) not in existing_months:
                        months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
            source_file=source_file_path,
            source_dir=source_dir_path,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        months_fetched_count = (
            len(months_to_fetch) if months_to_fetch is not None else updater.refresh_recent_months
        )

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Months requested: {months_fetched_count}\n"
                f"Source rows prepared (national monthly): {source_rows}\n"
                f"Source latest month date: {source_latest}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="KR Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting KR Data",
            content=(
                "Korea KDCA monthly data may be revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing Korea monthly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed Korea KDCA source only."
        elif imported_new_data:
            summary_message = f"Korea KDCA data upserted: {imported} rows across {months_fetched_count} month(s)."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "Korea KDCA source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

    @staticmethod
    def _kr_history_start_year(task: Task, updater) -> int:
        raw_value = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
        try:
            start_year = int(raw_value) if raw_value is not None else int(updater.full_history_start_year)
        except (TypeError, ValueError):
            start_year = int(updater.full_history_start_year)

        current_year = datetime.now().year
        return max(1900, min(start_year, current_year))

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
        """Execute the Switzerland FOPH IDD API pipeline."""
        run_started = perf_counter()
        raw_dir = Path("data/raw") / "ch"
        run_id: Optional[int] = None
        history_start_year = self._ch_history_start_year(task, updater)

        if force:
            mode_text = f"full IDD history refresh from {history_start_year}"
        elif fill_missing:
            mode_text = f"fill missing months from {history_start_year}"
        else:
            mode_text = "recent IDD periods"

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Configuration",
            content=(
                "Country: CH\n"
                f"Source: {source}\n"
                f"Force: {'Yes' if force else 'No'}\n"
                f"Process: {'Yes' if process else 'No'}\n"
                f"Save Raw: {'Yes' if save_raw else 'No'}\n"
                f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
                f"History Start Year: {history_start_year}"
            ),
            content_type="text",
        )
        await self._add_raw_archive_entry(task.task_uuid, save_raw=save_raw, raw_dir=raw_dir)

        try:
            async with get_database() as db:
                run = CrawlRun(
                    country_code="CH",
                    source=source,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"Could not create CrawlRun record: {e}")

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching CH Source",
            content=(
                "Fetching Switzerland FOPH/BAG IDD mandatory reporting API series...\n"
                f"Mode: {mode_text}"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        months_to_fetch = None
        if fill_missing and not force:
            now_dt = datetime.now()
            recent: list[tuple[int, int]] = []
            for delta in range(max(1, updater.refresh_recent_months)):
                m = now_dt.month - delta
                y = now_dt.year
                if m <= 0:
                    m += 12
                    y -= 1
                recent.append((y, m))
            months_set = set(recent)

            async with get_database() as db:
                existing_months = await updater.get_db_months(db)
            for fy in range(history_start_year, now_dt.year + 1):
                last_month = 12 if fy < now_dt.year else now_dt.month
                for fm in range(1, last_month + 1):
                    if (fy, fm) not in existing_months:
                        months_set.add((fy, fm))

            months_to_fetch = sorted(months_set)

        phase1_started = perf_counter()
        fetched = updater.refresh_source(
            source=source,
            run_external=False,
            force=force,
            months=months_to_fetch,
            history=force,
            start_year=history_start_year if force else None,
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.source_latest_date.isoformat() if fetched.source_latest_date else "none"
        source_rows = len(fetched.rows)
        periods_requested = (
            str(len(months_to_fetch))
            if months_to_fetch is not None
            else ("all history" if force else "recent configured periods")
        )

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Periods requested: {periods_requested}\n"
                f"Source rows prepared: {source_rows}\n"
                f"Source latest date: {source_latest}\n"
                f"IDD data version: {fetched.version or 'unknown'}\n"
                f"Current national CSV: {fetched.source_csv}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        if fetched.script_logs:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="CH Pipeline Logs",
                content="\n".join(fetched.script_logs[-10:]),
                content_type="text",
            )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Upserting CH Data",
            content=(
                "Swiss IDD data may be revised — upserting all fetched rows "
                f"({source_rows}) into the database unconditionally..."
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 50)

        imported = 0
        skipped_unmapped = 0
        imported_new_data = False
        db_latest_text = "none"

        async with get_database() as db:
            db_latest = await updater.get_db_latest_date(db)
            db_latest_text = db_latest.isoformat() if db_latest else "none"

            if not process:
                imported_new_data = False
                imported = 0
            else:
                import_result = await self._import_rows_with_series(
                    db,
                    updater,
                    fetched.rows,
                    db_latest_date=db_latest,
                    source_latest_date=fetched.source_latest_date,
                    force=force,
                )
                imported = import_result.inserted_or_updated
                skipped_unmapped = import_result.skipped_unmapped
                imported_new_data = import_result.imported_new_data

            await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 80)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"DB latest date: {db_latest_text}\n"
                f"Source latest date: {source_latest}\n"
                f"Upserted rows: {imported}\n"
                f"Skipped unmapped rows: {skipped_unmapped}"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 3/3: Finalizing",
            content="Finalizing Switzerland IDD update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, refreshed Switzerland IDD source only."
        elif imported_new_data:
            summary_message = f"Switzerland IDD data upserted: {imported} rows."
        elif force:
            summary_message = "Force mode completed; no rows were upserted (possibly all skipped due to mapping)."
        else:
            summary_message = "Switzerland source refreshed; no rows matched disease mapping (check mapping config)."

        await self._finish_crawl_run(
            run_id,
            new_reports=imported,
            processed=1 if process else 0,
            records=imported,
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Completed",
            content=(
                f"Summary: {summary_message}\n"
                f"Prepared rows: {source_rows}\n"
                f"Imported rows: {imported}\n"
                f"Duration: {perf_counter() - run_started:.1f}s"
            ),
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 100)

        return CrawlResult(imported, 1 if process else 0, imported, run_id)

    @staticmethod
    def _ch_history_start_year(task: Task, updater) -> int:
        raw_value = (task.input_data or {}).get("start_year") if isinstance(task.input_data, dict) else None
        try:
            start_year = int(raw_value) if raw_value is not None else int(updater.full_history_start_year)
        except (TypeError, ValueError):
            start_year = int(updater.full_history_start_year)

        current_year = datetime.now().year
        return max(1900, min(start_year, current_year))

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
