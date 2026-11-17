"""Authoritative Colombia FluNet revision window, isolated from other sources."""
from datetime import date, datetime, time, timezone

from src.services.ingestion.strategies import UpdaterCommitStrategy


class ColombiaFluNetCommitStrategy(UpdaterCommitStrategy):
    def __init__(self, start_year=2025):
        self.start_year = max(2025, int(start_year))

    async def commit(self, db, *, updater, fetched, **options):
        if (updater.country_code != "CO" or updater.ontology_source_id != "SRC_CO_WHO_FLUNET"
                or fetched.source_audit.get("coverage_complete") is not True or not fetched.rows
                or any(int(row["SourceYear"]) < self.start_year for row in fetched.rows)):
            raise ValueError("Complete verified Colombia FluNet source-year window required")
        boundary = datetime.combine(date.fromisocalendar(self.start_year, 1, 1), time.min, tzinfo=timezone.utc)
        return await super().commit(db, updater=updater, fetched=fetched,
            authoritative_window_start=boundary, **options)
