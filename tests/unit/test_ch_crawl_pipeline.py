from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines import ch
from src.services.crawl_service import CrawlService


class _Database:
    def __init__(self, events):
        self.events = events
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.added[-1].id = 91

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
        self.finished = []

    async def _add_raw_archive_entry(self, task_uuid, **values):
        self.events.append("archive")

    async def _finish_crawl_run(self, run_id, **values):
        self.events.append("finish")
        self.finished.append((run_id, values))

    async def _import_rows_with_series(self, db, updater, rows, **kwargs):
        self.events.append(("dual-write", list(rows), kwargs))
        return SimpleNamespace(
            inserted_or_updated=1,
            skipped_unmapped=0,
            imported_new_data=True,
        )


class _Updater:
    refresh_recent_months = 2
    full_history_start_year = 2025

    def __init__(self, events):
        self.events = events
        self.refresh_calls = []

    async def get_db_months(self, db):
        return {(2025, month) for month in range(1, 13)} | {
            (2026, month) for month in range(1, 6)
        }

    def refresh_source(self, **values):
        self.events.append("refresh")
        self.refresh_calls.append(values)
        return SimpleNamespace(
            rows=[{"Disease": "Campylobacteriosis"}],
            source_latest_date=date(2026, 7, 1),
            source_csv="switzerland_national_monthly.csv",
            script_logs=["old"] * 10 + ["latest"],
            version="2026-08-01",
        )

    async def get_db_latest_date(self, db):
        return date(2026, 6, 1)


@pytest.mark.asyncio
async def test_ch_fill_missing_preserves_period_plan_logs_and_commit_order(monkeypatch):
    class FixedDateTime(ch.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 5, tzinfo=tz)

    monkeypatch.setattr(ch, "datetime", FixedDateTime)
    events = []

    @asynccontextmanager
    async def get_database():
        yield _Database(events)

    service = _Service(events)
    updater = _Updater(events)
    manager = _TaskManager()
    result = await ch.execute_ch_pipeline(
        service,
        task=SimpleNamespace(task_uuid="task-ch", input_data={}),
        source="foph_idd",
        force=False,
        process=True,
        save_raw=False,
        fill_missing=True,
        updater=updater,
        get_database=get_database,
        task_manager=manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda *values: values,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert result == (1, 1, 1, 91)
    assert updater.refresh_calls == [
        {
            "source": "foph_idd",
            "run_external": False,
            "force": False,
            "months": [(2026, 6), (2026, 7), (2026, 8)],
            "history": False,
            "start_year": None,
            "save_raw": False,
            "raw_dir": None,
        }
    ]
    log_entry = next(entry for _, entry in manager.entries if entry["title"] == "CH Pipeline Logs")
    assert log_entry["content"].splitlines() == ["old"] * 9 + ["latest"]
    dual_write_index = next(
        index for index, event in enumerate(events) if isinstance(event, tuple)
    )
    assert events.index("archive") < events.index("refresh") < dual_write_index
    assert dual_write_index < events.index("commit") < events.index("finish")
    assert manager.progress == [
        ("task-ch", 10),
        ("task-ch", 30),
        ("task-ch", 50),
        ("task-ch", 80),
        ("task-ch", 100),
    ]
    assert service.finished == [
        (91, {"new_reports": 1, "processed": 1, "records": 1})
    ]


def test_ch_history_helper_and_service_wrapper_preserve_clamping(monkeypatch):
    class FixedDateTime(ch.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 5, tzinfo=tz)

    monkeypatch.setattr(ch, "datetime", FixedDateTime)
    updater = SimpleNamespace(full_history_start_year=2000)

    assert CrawlService._ch_history_start_year(
        SimpleNamespace(input_data={"start_year": "1899"}), updater
    ) == 1900
    assert CrawlService._ch_history_start_year(
        SimpleNamespace(input_data={"start_year": "2099"}), updater
    ) == 2026
    assert CrawlService._ch_history_start_year(
        SimpleNamespace(input_data={"start_year": None}), updater
    ) == 2000
