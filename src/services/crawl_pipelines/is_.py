"""Iceland Directorate of Health mixed-frequency crawl orchestration."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Optional

from sqlalchemy import text

from src.core.country_library import get_country_bootstrap_config, get_country_profile


_CURRENT_SCOPES = {
    "all",
    "annual",
    "sti",
    "respiratory",
    "is_doh_annual",
    "is_doh_sti",
    "is_doh_respiratory",
}
_HISTORY_SCOPES = {"is_doh_history", "is_doh_legacy_icd"}


async def _mark_is_history_failed(
    service,
    *,
    run_id: Optional[int],
    task,
    task_manager,
    exc: Exception,
) -> None:
    """Close a provisioned history run without masking the root exception."""

    message = f"{type(exc).__name__}: {exc}"[:2000]
    await service._finish_crawl_run(
        run_id,
        new_reports=0,
        processed=0,
        records=0,
        status="failed",
        error=message,
    )
    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="error",
            title="Iceland Historical Crawl Failed",
            content=message,
            content_type="text",
        )
    except Exception:
        pass


async def _ensure_is_country(db) -> None:
    """Provision the canonical Iceland row before facts are written."""

    profile = get_country_profile("IS")
    bootstrap = get_country_bootstrap_config("IS")
    await db.execute(
        text(
            """
            INSERT INTO countries (
                code, name, name_en, name_local, language, timezone,
                data_source_url, data_source_type,
                crawler_config, parser_config, disease_mapping_rules, report_config,
                is_active, metadata, notes, created_at, updated_at
            ) VALUES (
                :code, :name, :name_en, :name_local, :language, :timezone,
                :data_source_url, :data_source_type,
                CAST(:crawler_config AS json), CAST(:parser_config AS json),
                CAST(:disease_mapping_rules AS json), CAST(:report_config AS json),
                true, CAST(:metadata AS json), :notes,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                name_en = EXCLUDED.name_en,
                name_local = EXCLUDED.name_local,
                language = EXCLUDED.language,
                timezone = EXCLUDED.timezone,
                data_source_url = EXCLUDED.data_source_url,
                data_source_type = EXCLUDED.data_source_type,
                crawler_config = EXCLUDED.crawler_config,
                parser_config = EXCLUDED.parser_config,
                disease_mapping_rules = EXCLUDED.disease_mapping_rules,
                report_config = EXCLUDED.report_config,
                is_active = true,
                metadata = (
                    COALESCE(countries.metadata, '{}'::json)::jsonb
                    || EXCLUDED.metadata::jsonb
                )::json,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "code": profile.code,
            "name": profile.name,
            "name_en": profile.name_en,
            "name_local": profile.name_local,
            "language": profile.language,
            "timezone": profile.timezone,
            "data_source_url": bootstrap.get("data_source_url"),
            "data_source_type": bootstrap.get("data_source_type"),
            "crawler_config": json.dumps(bootstrap.get("crawler_config", {})),
            "parser_config": json.dumps(bootstrap.get("parser_config", {})),
            "disease_mapping_rules": json.dumps(
                bootstrap.get("disease_mapping_rules", {})
            ),
            "report_config": json.dumps(bootstrap.get("report_config", {})),
            "metadata": json.dumps(
                {
                    "standard_source": profile.source,
                    "iso_alpha2": profile.code,
                    "iceland_surveillance_pipeline": True,
                }
            ),
            "notes": bootstrap.get("notes"),
        },
    )


async def _execute_is_history_pipeline(
    service,
    *,
    task,
    normalized_source: str,
    force: bool,
    process: bool,
    save_raw: bool,
    fill_missing: bool,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    logger,
):
    """Fetch and import one reviewed family of official historical workbooks."""

    from src.data.crawlers.is_history import IcelandHistoryCrawler, OFFICIAL_WORKBOOKS
    from src.data.processors.is_history import IcelandHistoryProcessor

    started = perf_counter()
    source_kinds = (
        {"registry_annual", "registry_disease_monthly"}
        if normalized_source == "is_doh_history"
        else {"legacy_icd_monthly"}
    )
    specs = [
        spec
        for spec in OFFICIAL_WORKBOOKS
        if spec.source_kind in source_kinds and not spec.validation_only
    ]
    if not specs:
        raise ValueError(f"No reviewed Iceland workbooks for {normalized_source}")

    run_stamp = datetime.now(timezone.utc)
    raw_dir = (
        Path("data/raw/is/history")
        / run_stamp.strftime("%Y/%m/%d")
        / normalized_source
    )
    output_dir = Path("data/current/is/history") / normalized_source
    run_id: Optional[int] = None

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Iceland Historical Crawl Configuration",
        content=(
            f"Source: {normalized_source}\n"
            f"Reviewed workbooks: {len(specs)}\n"
            "Raw workbook archival: required for reproducibility\n"
            f"Process: {'Yes' if process else 'No'}\n"
            "Missing values: preserved as unknown; no synthetic filling"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid,
        save_raw=True,
        raw_dir=raw_dir,
    )

    async with get_database() as db:
        await _ensure_is_country(db)
        run = crawl_run_type(
            country_code="IS",
            source=normalized_source,
            status="running",
            started_at=run_stamp,
            raw_dir=str(raw_dir),
            metadata_={
                "force": force,
                "process": process,
                "requested_save_raw": save_raw,
                "raw_archive_required": True,
                "fill_missing_requested": fill_missing,
                "fill_missing_applied": False,
                "historical_workbooks": True,
            },
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

    try:
        await task_manager.update_task_progress(task.task_uuid, 10)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Archiving Official Workbooks",
            content=(
                "Downloading the reviewed Excel catalogue and recording landing-page, "
                "file URL, media type, byte size, and SHA-256 provenance."
            ),
            content_type="text",
        )
        crawler = IcelandHistoryCrawler(raw_dir=raw_dir)
        downloaded = await asyncio.to_thread(
            crawler.download_history,
            output_dir=raw_dir,
            specs=specs,
            discover=True,
        )

        await task_manager.update_task_progress(task.task_uuid, 38)
        processor = IcelandHistoryProcessor()
        prepared = await asyncio.to_thread(
            processor.prepare_manifest,
            downloaded.manifest_path,
        )
        outputs = await asyncio.to_thread(
            processor.write_outputs,
            prepared,
            output_dir,
        )
        counts = prepared.manifest.get("counts", {})
        quarantine = prepared.manifest.get("quarantine", {})
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 1/3 Complete",
            content=(
                f"Archived workbooks: {len(downloaded.raw_files)}\n"
                f"Registered source observations: {len(prepared.series_rows)}\n"
                f"Safe compatibility projection rows: {len(prepared.rows)}\n"
                f"Quarantined source rows: {len(prepared.quarantine)}\n"
                f"Raw manifest: {downloaded.manifest_path}\n"
                f"Normalized manifest: {outputs.get('manifest.json')}"
            ),
            content_type="text",
        )

        projection_upserted = 0
        projection_skipped = 0
        projection_skipped_due_current = 0
        series_upserted = 0
        if process:
            await task_manager.update_task_progress(task.task_uuid, 58)
            async with get_database() as db:
                await _ensure_is_country(db)
                # The series save acquires the shared data-mutation lock.  The
                # lock remains held for the following compatibility projection
                # and both writes commit (or roll back) in one transaction.
                series_result = await service._save_series_rows(
                    db,
                    processor,
                    prepared.series_rows,
                )
                import_result = await processor.import_rows(
                    db,
                    prepared.rows,
                    db_latest_date=None,
                    source_latest_date=None,
                    force=force,
                )
                series_upserted = int(series_result.upserted)
                projection_upserted = import_result.inserted_or_updated
                projection_skipped = import_result.skipped_unmapped
                projection_skipped_due_current = getattr(
                    import_result, "skipped_current_precedence", 0
                )
                await db.commit()

        await task_manager.update_task_progress(task.task_uuid, 88)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Phase 2/3 Complete",
            content=(
                f"Source-series observations upserted: {series_upserted}\n"
                f"Compatibility rows upserted: {projection_upserted}\n"
                f"Compatibility rows skipped (unmapped): {projection_skipped}\n"
                "Compatibility rows skipped_due_current: "
                f"{projection_skipped_due_current}\n"
                f"Quarantine reasons: {json.dumps(quarantine.get('by_reason', {}), ensure_ascii=False, sort_keys=True)}\n"
                f"Parser counters: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}"
            ),
            content_type="text",
        )
        await service._finish_crawl_run(
            run_id,
            new_reports=series_upserted,
            processed=1 if process else 0,
            records=series_upserted,
        )
        await task_manager.update_task_progress(task.task_uuid, 100)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Historical Crawl Completed",
            content=(
                f"Source: {normalized_source}\n"
                f"Imported observations: {series_upserted}\n"
                f"Compatibility skipped_due_current: "
                f"{projection_skipped_due_current}\n"
                f"Duration: {perf_counter() - started:.1f}s"
            ),
            content_type="text",
        )
        return result_type(
            new_reports=series_upserted,
            processed_reports=1 if process else 0,
            total_records=series_upserted,
            crawl_run_id=run_id,
        )
    except Exception as exc:
        await _mark_is_history_failed(
            service,
            run_id=run_id,
            task=task,
            task_manager=task_manager,
            exc=exc,
        )
        raise


async def execute_is_pipeline(
    service,
    *,
    task,
    source: str,
    force: bool,
    process: bool,
    save_raw: bool,
    fill_missing: bool,
    updater,
    get_database,
    task_manager,
    crawl_run_type,
    result_type,
    logger,
):
    """Fetch selected live Iceland reports and atomically dual-write facts."""

    started = perf_counter()
    normalized_source = str(source or "all").strip().lower()
    if normalized_source in _HISTORY_SCOPES:
        return await _execute_is_history_pipeline(
            service,
            task=task,
            normalized_source=normalized_source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=crawl_run_type,
            result_type=result_type,
            logger=logger,
        )
    if normalized_source not in _CURRENT_SCOPES:
        raise ValueError(f"Unsupported Iceland source: {source}")

    raw_dir = Path("data/raw/is")
    run_id: Optional[int] = None
    input_data = (
        task.input_data
        if isinstance(getattr(task, "input_data", None), dict)
        else {}
    )
    start_year = input_data.get("start_year")

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Crawl Configuration",
        content=(
            "Country: IS\n"
            f"Source: {normalized_source}\n"
            "Grains: annual registry, STI month, respiratory ISO week\n"
            f"Force: {'Yes' if force else 'No'}\n"
            f"Process: {'Yes' if process else 'No'}\n"
            f"Save Raw: {'Yes' if save_raw else 'No'}\n"
            f"Fill Missing: {'Yes' if fill_missing else 'No'}\n"
            f"Start Year Filter: {start_year if start_year is not None else 'none'}"
        ),
        content_type="text",
    )
    await service._add_raw_archive_entry(
        task.task_uuid,
        save_raw=save_raw,
        raw_dir=raw_dir,
    )

    try:
        async with get_database() as db:
            await _ensure_is_country(db)
            run = crawl_run_type(
                country_code="IS",
                source=normalized_source,
                status="running",
                started_at=datetime.now(timezone.utc),
                raw_dir=str(raw_dir) if save_raw else None,
                metadata_={
                    "force": force,
                    "process": process,
                    "fill_missing": fill_missing,
                    "mixed_frequency": True,
                    "authoritative_revisions": True,
                },
            )
            db.add(run)
            await db.flush()
            run_id = run.id
            await db.commit()
    except Exception as exc:
        logger.warning(f"Could not provision Iceland crawl run: {exc}")
        raise

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 1/3: Fetching Iceland Sources",
        content=(
            "Discovering the public Power BI model, validating its schema, and "
            "querying only the reviewed national case-count series."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 10)

    fetched = updater.refresh_source(
        source=normalized_source,
        run_external=False,
        force=force,
        history=False,
        start_year=int(start_year) if start_year is not None else None,
        save_raw=save_raw,
        raw_dir=raw_dir if save_raw else None,
    )
    source_rows = len(fetched.rows)
    source_latest = (
        fetched.source_latest_date.isoformat()
        if fetched.source_latest_date is not None
        else "none"
    )
    source_counts = ", ".join(
        f"{scope}={count}"
        for scope, count in sorted(fetched.source_row_counts.items())
    )

    await task_manager.update_task_progress(task.task_uuid, 35)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 1/3 Complete",
        content=(
            f"Rows prepared: {source_rows}\n"
            f"By source: {source_counts or 'none'}\n"
            f"Latest observation date: {source_latest}\n"
            f"Normalized snapshot: {fetched.source_csv}\n"
            f"Schema fingerprints: {len(fetched.schema_fingerprints)}"
        ),
        content_type="text",
    )
    if fetched.script_logs:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Iceland Connector Logs",
            content="\n".join(fetched.script_logs[-12:]),
            content_type="text",
        )

    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="info",
        title="Phase 2/3: Upserting Iceland Data",
        content=(
            "Writing every reviewed fact to the source-series store. The legacy "
            "compatibility table receives only annual dashboard rows so monthly "
            "and weekly facts cannot collide."
        ),
        content_type="text",
    )
    await task_manager.update_task_progress(task.task_uuid, 55)

    legacy_upserted = 0
    skipped_unmapped = 0
    skipped_incompatible_projection = 0
    series_upserted = 0
    if process:
        async with get_database() as db:
            await _ensure_is_country(db)
            db_latest = await updater.get_db_latest_date(db)
            import_result = await service._import_rows_with_series(
                db,
                updater,
                fetched.rows,
                db_latest_date=db_latest,
                source_latest_date=fetched.source_latest_date,
                force=force,
            )
            legacy_upserted = import_result.inserted_or_updated
            skipped_unmapped = import_result.skipped_unmapped
            skipped_incompatible_projection = getattr(
                import_result, "skipped_incompatible_projection", 0
            )
            # Registry coverage is required and fail-closed for IS. Reaching
            # this point means every prepared row staged successfully.
            series_upserted = source_rows
            await db.commit()

    await task_manager.update_task_progress(task.task_uuid, 85)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Phase 2/3 Complete",
        content=(
            f"Source-series observations upserted: {series_upserted}\n"
            f"Annual compatibility rows upserted: {legacy_upserted}\n"
            f"Annual rows kept as series-only to protect monthly facts: "
            f"{skipped_incompatible_projection}\n"
            f"Unmapped annual rows: {skipped_unmapped}"
        ),
        content_type="text",
    )

    await service._finish_crawl_run(
        run_id,
        new_reports=series_upserted,
        processed=1 if process else 0,
        records=series_upserted,
    )
    await task_manager.update_task_progress(task.task_uuid, 100)
    await task_manager.add_workbook_entry(
        task.task_uuid,
        entry_type="success",
        title="Crawl Completed",
        content=(
            f"Prepared Iceland rows: {source_rows}\n"
            f"Imported source-series observations: {series_upserted}\n"
            f"Raw provenance archived: {'Yes' if save_raw else 'No'}\n"
            f"Duration: {perf_counter() - started:.1f}s"
        ),
        content_type="text",
    )
    return result_type(
        new_reports=series_upserted,
        processed_reports=1 if process else 0,
        total_records=series_upserted,
        crawl_run_id=run_id,
    )


__all__ = ["execute_is_pipeline"]
