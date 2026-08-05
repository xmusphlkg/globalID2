from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines import kr
from src.services.crawl_service import CrawlService


class _Database:
    def __init__(self, events):
        self.events = events
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.added[-1].id = 81

    async def commit(self):
        self.events.append("commit")


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
    def __init__(self, events):
        self.events = events
        self.archive_calls = []
        self.finished = []

    async def _add_raw_archive_entry(self, task_uuid, **values):
        self.events.append("archive")
        self.archive_calls.append((task_uuid, values))

    async def _finish_crawl_run(self, run_id, **values):
        self.events.append("finish")
        self.finished.append((run_id, values))

    async def _import_rows_with_series(self, db, updater, rows, **kwargs):
        self.events.append(("dual-write", list(rows), kwargs))
        return SimpleNamespace(
            inserted_or_updated=2,
            skipped_unmapped=1,
            imported_new_data=True,
        )


class _Updater:
    refresh_recent_months = 3
    full_history_start_year = 2001

    def __init__(self, events):
        self.events = events
        self.refresh_calls = []

    def history_months(self, *, start_year):
        assert start_year == 2024
        return [(2024, 1), (2024, 2)]

    def refresh_source(self, **values):
        self.events.append("refresh")
        self.refresh_calls.append(values)
        return SimpleNamespace(
            rows=[{"Disease": "Example"}, {"Disease": "Second"}],
            source_latest_date=date(2024, 2, 1),
            source_csv="korea_national_monthly.csv",
            script_logs=["one", "two"],
        )

    async def get_db_latest_date(self, db):
        return date(2024, 1, 1)


@pytest.mark.asyncio
async def test_kr_force_pipeline_preserves_source_paths_dual_write_and_commit_order():
    events = []
    databases = []

    @asynccontextmanager
    async def get_database():
        database = _Database(events)
        databases.append(database)
        yield database

    service = _Service(events)
    updater = _Updater(events)
    manager = _TaskManager()
    task = SimpleNamespace(
        task_uuid="task-kr",
        input_data={
            "start_year": "2024",
            "source_file": "/tmp/kdca.xlsx",
            "source_dir": "/tmp/kdca",
        },
    )

    result = await kr.execute_kr_pipeline(
        service,
        task=task,
        source="kdca_open_api",
        force=True,
        process=True,
        save_raw=True,
        fill_missing=False,
        updater=updater,
        get_database=get_database,
        task_manager=manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda *values: values,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert result == (2, 1, 2, 81)
    assert updater.refresh_calls == [
        {
            "source": "kdca_open_api",
            "run_external": False,
            "force": True,
            "months": [(2024, 1), (2024, 2)],
            "save_raw": True,
            "raw_dir": service.archive_calls[0][1]["raw_dir"],
            "source_file": kr.Path("/tmp/kdca.xlsx"),
            "source_dir": kr.Path("/tmp/kdca"),
        }
    ]
    assert events.index("archive") < events.index("refresh")
    assert events.index("refresh") < next(
        index for index, event in enumerate(events) if isinstance(event, tuple)
    )
    assert events.index("commit") < events.index("finish")
    assert manager.progress == [
        ("task-kr", 10),
        ("task-kr", 30),
        ("task-kr", 50),
        ("task-kr", 80),
        ("task-kr", 100),
    ]
    assert service.finished == [
        (81, {"new_reports": 2, "processed": 1, "records": 2})
    ]


def test_kr_history_helper_and_service_wrapper_preserve_clamping(monkeypatch):
    class FixedDateTime(kr.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 5, tzinfo=tz)

    monkeypatch.setattr(kr, "datetime", FixedDateTime)
    updater = SimpleNamespace(full_history_start_year=2001)

    assert CrawlService._kr_history_start_year(
        SimpleNamespace(input_data={"start_year": "1899"}), updater
    ) == 1900
    assert CrawlService._kr_history_start_year(
        SimpleNamespace(input_data={"start_year": "2099"}), updater
    ) == 2026
    assert CrawlService._kr_history_start_year(
        SimpleNamespace(input_data={"start_year": "invalid"}), updater
    ) == 2001
