"""Brazil DATASUS SINAN crawl orchestration.

This module deliberately keeps Brazil's batching and partial-failure semantics
separate from the simpler configured monthly pipelines.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import List, Optional, Tuple


def chunk_months(
    values: List[Tuple[int, int]],
    chunk_size: int,
) -> List[List[Tuple[int, int]]]:
    if chunk_size <= 0:
        return [values]
    return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]


def history_start_year(task, updater, default_start_year: Optional[int] = None) -> int:
    raw_value = (
        task.input_data.get("start_year")
        if isinstance(task.input_data, dict)
        else None
    )
    current_year = datetime.now().year
    crawler_default = getattr(updater, "full_history_start_year", None)
    fallback_start_year = int(crawler_default) if crawler_default is not None else 2000

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


async def execute_br_pipeline(
    service,
    *,
    task,
    source: str,
    force: bool,
    process: bool,
    save_raw: bool,
    fill_missing: bool,
    start_year: Optional[int],
    updater,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    crawler_type,
    logger,
):
    """Execute Brazil's batched SINAN pipeline with injected service hooks."""
    run_started = perf_counter()
    raw_dir = Path("data/raw") / "br"
    run_id: Optional[int] = None
    history_year = history_start_year(task, updater, default_start_year=start_year)

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
            f"History Start Year: {history_year}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid, save_raw=save_raw, raw_dir=raw_dir
    )

    try:
        async with get_database() as db:
            run = crawl_run_type(
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
    except Exception as exc:
        logger.warning(f"Could not create CrawlRun record: {exc}")

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching BR Source",
        content=(
            "Fetching Brazil DATASUS SINAN DBC files and aggregating national monthly rows...\n"
            + (
                f"Mode: {'fill missing months' if fill_missing else 'recent months'} "
                f"(refresh window: {updater.refresh_recent_months}, start: {history_year})"
                if not force
                else f"Mode: full history refresh from {history_year}"
            )
            + (" + force re-fetch" if force else "")
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    months_to_fetch = None
    if fill_missing or force:
        if force:
            months_to_fetch = updater.history_months(start_year=history_year)
        else:
            now_dt = datetime.now()
            recent: list[tuple[int, int]] = []
            for delta in range(max(1, updater.refresh_recent_months)):
                month = now_dt.month - delta
                year = now_dt.year
                if month <= 0:
                    month += 12
                    year -= 1
                recent.append((year, month))
            months_set = set(recent)

            async with get_database() as db:
                existing_months = await updater.get_db_months(db)
            for year in range(history_year, now_dt.year + 1):
                for month in range(
                    1, 13 if year < now_dt.year else now_dt.month + 1
                ):
                    if (year, month) not in existing_months:
                        months_set.add((year, month))

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
            chunk_months(months_to_fetch, month_batch_size)
            if month_batch_size > 0
            else [months_to_fetch]
        )

    use_batch_csv_write = len(requested_batches) > 1
    shared_crawler = None
    if use_batch_csv_write:
        shared_crawler = crawler_type(
            save_raw=save_raw,
            raw_dir=raw_dir if save_raw else None,
        )

    phase1_started = perf_counter()
    merged_rows: list[dict] = []
    fetched_batch_messages: list[str] = []
    failed_batches: list[tuple[int, Exception]] = []
    source_latest: Optional[date] = None
    source_csv: Optional[Path] = None
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
            source_latest is None or fetched.source_latest_date > source_latest
        ):
            source_latest = fetched.source_latest_date
        source_csv = fetched.source_csv

        batch_progress = 10 + int((idx / max(1, len(requested_batches))) * 20)
        await task_manager.update_task_progress(
            task.task_uuid, min(30, batch_progress)
        )

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
    source_latest_text = (
        source_latest_date.isoformat() if source_latest_date else "none"
    )
    months_fetched_count = (
        len(months_to_fetch)
        if months_to_fetch is not None
        else max(1, int(updater.refresh_recent_months))
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
            import_result = await service._import_rows_with_series(
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
        summary_message = (
            f"Brazil SINAN data upserted: {imported} rows across "
            f"{months_fetched_count} month(s)."
        )
    elif force:
        summary_message = (
            "Force mode completed; no rows were upserted "
            "(possibly all skipped due to mapping)."
        )
    else:
        summary_message = (
            "Brazil source refreshed; no rows matched disease mapping "
            "(check mapping config)."
        )

    await service._finish_crawl_run(
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

    return result_type(imported, 1 if process else 0, imported, run_id)
