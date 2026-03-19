"""
Crawl Service

Encapsulates all business logic for data crawling + processing,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import CrawlRun, Task

logger = get_logger(__name__)


@dataclass
class CrawlResult:
    new_reports: int
    processed_reports: int
    total_records: int
    crawl_run_id: Optional[int]


class CrawlService:
    """Orchestrates the three-phase crawl → parse → store pipeline."""

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
        from src.data.crawlers import ChinaCDCCrawler
        from src.data.processors import AUMonthlyUpdater, DataProcessor, JPWeeklyUpdater, USWeeklyUpdater

        if country_code not in ("CN", "US", "JP", "AU"):
            raise ValueError(f"Unsupported country: {country_code}. Available: CN, US, JP, AU")

        if country_code == "US":
            return await self._execute_us_weekly(
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=USWeeklyUpdater(),
            )

        if country_code == "JP":
            return await self._execute_jp_weekly(
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=JPWeeklyUpdater(),
            )

        if country_code == "AU":
            return await self._execute_au_monthly(
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=AUMonthlyUpdater(),
            )

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
            content="Fetching latest US NNDSS TOTAL rows from CDC API...",
            content_type="text",
        )
        await task_manager.update_task_progress(task.task_uuid, 10)

        phase1_started = perf_counter()
        fetched = updater.fetch_latest()
        phase1_elapsed = perf_counter() - phase1_started
        source_latest = fetched.latest_date.isoformat() if fetched.latest_date else "none"
        source_rows = len(fetched.rows)

        await task_manager.update_task_progress(task.task_uuid, 30)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Source rows fetched: {source_rows}\n"
                f"Source latest week date: {source_latest}\n"
                f"Source endpoint: {fetched.source_ref}\n"
                f"Duration: {phase1_elapsed:.1f}s"
            ),
            content_type="text",
        )

        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 2/3: Checking Incremental Updates",
            content="Comparing source latest week with US latest date in database...",
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
                import_result = await updater.import_rows(
                    db,
                    fetched.rows,
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
            content="Finalizing US weekly update and crawl run summary...",
            content_type="text",
        )

        if not process:
            summary_message = "Process disabled, fetched source rows only."
        elif imported_new_data:
            summary_message = "New US weekly data imported successfully."
        elif force:
            summary_message = "Force mode completed; no rows were upserted after filtering/mapping."
        else:
            summary_message = "No newer US weekly data detected, import skipped."

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
                import_result = await updater.import_rows(
                    db,
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
            from datetime import date as _date
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
                import_result = await updater.import_rows(
                    db,
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
