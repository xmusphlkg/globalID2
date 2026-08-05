from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines import br
from src.services.crawl_service import CrawlService


class _Database:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.added[-1].id = 73

    async def commit(self):
        self.commits += 1


class _CrawlRun:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.id = None


class _TaskManager:
    def __init__(self):
        self.entries = []
        self.progress = []

    async def add_workbook_entry(self, task_uuid, **entry):
        self.entries.append((task_uuid, entry))

    async def update_task_progress(self, task_uuid, progress):
        self.progress.append((task_uuid, progress))


class _Service:
    def __init__(self):
        self.archive_calls = []
        self.finished = []

    async def _add_raw_archive_entry(self, task_uuid, **values):
        self.archive_calls.append((task_uuid, values))

    async def _finish_crawl_run(self, run_id, **values):
        self.finished.append((run_id, values))

    async def _import_rows_with_series(self, *args, **kwargs):
        raise AssertionError("process=False must not dual-write")


class _Updater:
    refresh_recent_months = 3
    history_batch_months = 24
    full_history_start_year = 2007

    def __init__(self):
        self.refresh_calls = []

    def refresh_source(self, **values):
        self.refresh_calls.append(values)
        return SimpleNamespace(
            rows=[{"Disease": "Dengue", "Year": 2026, "Month": 7}],
            source_latest_date=date(2026, 7, 1),
            source_csv="brazil_national_monthly.csv",
            script_logs=["prepared"],
        )

    async def get_db_latest_date(self, db):
        return date(2026, 6, 1)


class _UnexpectedCrawler:
    def __init__(self, **kwargs):
        raise AssertionError("single-batch refresh must not create a shared crawler")


class _Logger:
    def warning(self, message):
        raise AssertionError(message)


@pytest.mark.asyncio
async def test_br_pipeline_preserves_no_process_lifecycle_and_no_commit():
    databases = []

    @asynccontextmanager
    async def get_database():
        database = _Database()
        databases.append(database)
        yield database

    task_manager = _TaskManager()
    service = _Service()
    updater = _Updater()
    result = await br.execute_br_pipeline(
        service,
        task=SimpleNamespace(task_uuid="task-br", input_data={}),
        source="sinan",
        force=False,
        process=False,
        save_raw=True,
        fill_missing=False,
        start_year=None,
        updater=updater,
        get_database=get_database,
        task_manager=task_manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda *values: values,
        crawler_type=_UnexpectedCrawler,
        logger=_Logger(),
    )

    assert result == (0, 0, 0, 73)
    assert updater.refresh_calls == [
        {
            "source": "sinan",
            "run_external": False,
            "force": False,
            "months": None,
            "save_raw": True,
            "raw_dir": service.archive_calls[0][1]["raw_dir"],
            "load_csv_fallback": True,
            "write_csv": True,
            "crawler": None,
        }
    ]
    assert len(databases) == 2
    assert databases[1].commits == 0
    assert task_manager.progress == [
        ("task-br", 10),
        ("task-br", 30),
        ("task-br", 30),
        ("task-br", 50),
        ("task-br", 80),
        ("task-br", 100),
    ]
    assert service.finished == [
        (73, {"new_reports": 0, "processed": 0, "records": 0})
    ]


def test_br_compatibility_helpers_delegate_to_country_module():
    task = SimpleNamespace(input_data={"start_year": "2008"})
    updater = SimpleNamespace(full_history_start_year=2007)

    assert CrawlService._chunk_months([(2026, 1), (2026, 2), (2026, 3)], 2) == [
        [(2026, 1), (2026, 2)],
        [(2026, 3)],
    ]
    assert CrawlService._br_history_start_year(task, updater) == 2008
    assert CrawlService._br_history_start_year(task, updater, 2010) == 2010


@pytest.mark.asyncio
async def test_br_pipeline_preserves_partial_batch_dual_write_and_commit_order():
    events = []
    databases = []

    class RecordingDatabase(_Database):
        async def commit(self):
            events.append("commit")
            await super().commit()

    @asynccontextmanager
    async def get_database():
        database = RecordingDatabase()
        databases.append(database)
        yield database

    class RecordingService(_Service):
        async def _import_rows_with_series(self, db, updater, rows, **kwargs):
            events.append(("dual-write", list(rows), kwargs))
            return SimpleNamespace(
                inserted_or_updated=1,
                skipped_unmapped=0,
                imported_new_data=True,
            )

    class BatchedUpdater(_Updater):
        history_batch_months = 2

        def __init__(self):
            super().__init__()
            self.written_rows = None

        def history_months(self, *, start_year):
            assert start_year == 2026
            return [(2026, 1), (2026, 2), (2026, 3)]

        def refresh_source(self, **values):
            self.refresh_calls.append(values)
            if values["months"] == [(2026, 1), (2026, 2)]:
                raise RuntimeError("first batch unavailable")
            return SimpleNamespace(
                rows=[{"Disease": "Dengue", "Year": 2026, "Month": 3}],
                source_latest_date=date(2026, 3, 1),
                source_csv="batch.csv",
                script_logs=[],
            )

        def _write_rows_to_output_csv(self, rows):
            events.append("write-csv")
            self.written_rows = list(rows)

    crawler_instances = []

    class Crawler:
        def __init__(self, **values):
            self.values = values
            crawler_instances.append(self)

    service = RecordingService()
    updater = BatchedUpdater()
    task_manager = _TaskManager()
    result = await br.execute_br_pipeline(
        service,
        task=SimpleNamespace(task_uuid="task-br-force", input_data={}),
        source="sinan",
        force=True,
        process=True,
        save_raw=False,
        fill_missing=False,
        start_year=2026,
        updater=updater,
        get_database=get_database,
        task_manager=task_manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda *values: values,
        crawler_type=Crawler,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert result == (1, 1, 1, 73)
    assert len(crawler_instances) == 1
    assert all(call["crawler"] is crawler_instances[0] for call in updater.refresh_calls)
    assert updater.written_rows == [
        {"Disease": "Dengue", "Year": 2026, "Month": 3}
    ]
    assert events[0] == "write-csv"
    assert events[1][0] == "dual-write"
    assert events[2] == "commit"
    assert databases[-1].commits == 1
    assert any(
        "first batch unavailable" in entry["content"]
        for _, entry in task_manager.entries
        if entry["title"] == "BR Pipeline Logs"
    )
