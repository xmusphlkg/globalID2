"""Transaction policies: authoritative replacement belongs to its source only."""
from datetime import datetime, timezone

from src.services.ingestion.stages import mark_dirty, mark_observation_changes_dirty
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock


class UpdaterCommitStrategy:
    async def commit(self, db, *, service, updater, fetched, force, task_uuid, **kwargs):
        latest = await updater.get_db_latest_date(db)
        publication_results = []
        # Some connectors retain cached rows for diagnostics but must not let
        # stale snapshots overwrite newer database observations.
        store_rows = getattr(fetched, "store_rows", None)
        result = await service._import_rows_with_series(
            db, updater, fetched.rows if store_rows is None else store_rows,
            series_rows=getattr(fetched, "series_rows", None),
            db_latest_date=latest,
            source_latest_date=getattr(fetched, "latest_date", None) or getattr(fetched, "source_latest_date", None),
            force=force,
            publication_results=publication_results,
            **({"authoritative_window_start": kwargs["authoritative_window_start"]}
               if "authoritative_window_start" in kwargs else {}),
        )
        await mark_observation_changes_dirty(
            db, result=publication_results[0] if publication_results else None,
            scope_key=updater.country_code, task_uuid=task_uuid,
            conservative=bool(kwargs.get("publication_window_replaced", False)),
        )
        return result


class EcdcWindowCommitStrategy(UpdaterCommitStrategy):
    async def commit(self, db, *, updater, fetched, start_year, **kwargs):
        if getattr(fetched, "authoritative_window_start_year", None) != int(start_year):
            raise ValueError("Authoritative replacement requires a completely validated source window")
        await acquire_country_data_mutation_lock(db, updater.country_code)
        return await super().commit(
            db, updater=updater, fetched=fetched,
            authoritative_window_start=datetime(int(start_year), 1, 1, tzinfo=timezone.utc),
            **kwargs,
        )


class CanadaCndssWindowCommitStrategy(EcdcWindowCommitStrategy):
    async def commit(self, db, *, updater, start_year, **kwargs):
        if updater.country_code != "CA":
            raise ValueError("CNDSS replacement is restricted to national Canada")
        return await super().commit(db, updater=updater, start_year=start_year, **kwargs)


class ChinaReportCommitStrategy:
    """A failed report never rolls back another report's successful commit."""
    async def commit_reports(self, reports, *, processor, task_uuid):
        from src.core.database import get_db
        from src.core.logging import get_logger
        logger = get_logger(__name__)
        stored = records = failed = 0
        for result, frame in reports:
            try:
                async with get_db() as db:
                    await processor.store_report(db, frame, result)
                    await mark_dirty(db, line="surveillance", scope_key="CN", task_uuid=task_uuid)
                stored += 1
                records += len(frame)
            except Exception:
                failed += 1
                logger.exception("China report transaction failed: {}", result.url)
        if reports and not stored:
            raise RuntimeError("No China reports could be committed")
        return stored, records, failed
