"""Transaction policies: destructive replacement belongs to its source only."""
from src.services.ingestion.stages import mark_dirty
from src.core.disease_mutation_lock import acquire_disease_data_mutation_lock


class UpdaterCommitStrategy:
    async def commit(self, db, *, service, updater, fetched, force, task_uuid, **kwargs):
        latest = await updater.get_db_latest_date(db)
        result = await service._import_rows_with_series(
            db, updater, fetched.rows,
            series_rows=getattr(fetched, "series_rows", None),
            db_latest_date=latest,
            source_latest_date=getattr(fetched, "latest_date", None) or getattr(fetched, "source_latest_date", None),
            force=force,
        )
        await mark_dirty(db, line="surveillance", scope_key=updater.country_code, task_uuid=task_uuid)
        return result


class EcdcWindowCommitStrategy(UpdaterCommitStrategy):
    async def commit(self, db, *, updater, start_year, **kwargs):
        await acquire_disease_data_mutation_lock(db)
        await updater.delete_authoritative_window(db, start_year=start_year)
        return await super().commit(db, updater=updater, **kwargs)


class CanadaCndssWindowCommitStrategy(UpdaterCommitStrategy):
    async def commit(self, db, *, updater, start_year, **kwargs):
        if updater.country_code != "CA":
            raise ValueError("CNDSS replacement is restricted to national Canada")
        await acquire_disease_data_mutation_lock(db)
        await updater.delete_authoritative_window(db, start_year=start_year)
        return await super().commit(db, updater=updater, **kwargs)


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
