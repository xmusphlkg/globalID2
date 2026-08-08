from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines import monthly
from src.services.crawl_pipelines.monthly import CONFIGS, execute_monthly_pipeline


class _Database:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        if self.added:
            self.added[-1].id = 42

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
        raise AssertionError("process=False must not import rows")


class _Updater:
    def __init__(self):
        self.refresh_calls = []

    def refresh_source(self, **values):
        self.refresh_calls.append(values)
        return SimpleNamespace(
            source_latest_date=date(2026, 7, 1),
            rows=[{"disease": "Example"}],
            source_csv="monthly.csv",
            script_logs=["prepared"],
        )

    async def get_db_latest_date(self, db):
        return date(2026, 6, 1)


class _Logger:
    def warning(self, message):
        raise AssertionError(message)


@pytest.mark.asyncio
async def test_force_history_rule_remains_country_specific(monkeypatch):
    class FixedDateTime(monthly.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 2, 15, tzinfo=tz)

    @asynccontextmanager
    async def unused_database():
        raise AssertionError("force-only month planning must not query the database")
        yield

    monkeypatch.setattr(monthly, "datetime", FixedDateTime)

    au_months = await monthly._months_to_fetch(
        CONFIGS["AU"],
        SimpleNamespace(),
        fill_missing=False,
        force=True,
        get_database=unused_database,
    )
    nz_months = await monthly._months_to_fetch(
        CONFIGS["NZ"],
        SimpleNamespace(),
        fill_missing=False,
        force=True,
        get_database=unused_database,
    )
    fi_months = await monthly._months_to_fetch(
        CONFIGS["FI"],
        SimpleNamespace(),
        fill_missing=False,
        force=True,
        get_database=unused_database,
    )
    no_months = await monthly._months_to_fetch(
        CONFIGS["NO"],
        SimpleNamespace(),
        fill_missing=False,
        force=True,
        get_database=unused_database,
    )
    se_months = await monthly._months_to_fetch(
        CONFIGS["SE"],
        SimpleNamespace(),
        fill_missing=False,
        force=True,
        get_database=unused_database,
    )

    assert au_months == [(2025, 12), (2026, 1), (2026, 2)]
    assert nz_months[0] == (2016, 1)
    assert nz_months[-1] == (2026, 2)
    assert len(nz_months) == 122
    assert fi_months[0] == (1995, 1)
    assert no_months[0] == (1977, 1)
    assert se_months[0] == (2016, 1)
    assert fi_months[-1] == no_months[-1] == se_months[-1] == (2026, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country_code", "expected_months"),
    [
        ("AU", "3"),
        ("NZ", "3"),
        ("TW", "3"),
        ("HK", "latest available 3"),
        ("CA-ON", "current-year snapshot"),
        ("FI", "3"),
        ("NO", "3"),
        ("SE", "3"),
    ],
)
async def test_monthly_pipeline_preserves_no_process_lifecycle(
    country_code, expected_months
):
    databases = []

    @asynccontextmanager
    async def get_database():
        db = _Database()
        databases.append(db)
        yield db

    service = _Service()
    task_manager = _TaskManager()
    updater = _Updater()
    result = await execute_monthly_pipeline(
        service,
        config=CONFIGS[country_code],
        task=SimpleNamespace(task_uuid="task-1"),
        source="all",
        force=False,
        process=False,
        save_raw=True,
        fill_missing=False,
        updater=updater,
        get_database=get_database,
        task_manager=task_manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda *values: values,
        logger=_Logger(),
    )

    assert result == (0, 0, 0, 42)
    assert updater.refresh_calls == [
        {
            "source": "all",
            "run_external": False,
            "force": False,
            "months": None,
            "save_raw": True,
            "raw_dir": service.archive_calls[0][1]["raw_dir"],
        }
    ]
    assert len(databases) == 2
    assert databases[1].commits == 1
    assert task_manager.progress == [
        ("task-1", 10),
        ("task-1", 30),
        ("task-1", 50),
        ("task-1", 80),
        ("task-1", 100),
    ]
    assert any(
        f"Months requested: {expected_months}" in entry["content"]
        for _, entry in task_manager.entries
    )
    assert service.finished == [
        (42, {"new_reports": 0, "processed": 0, "records": 0})
    ]


@pytest.mark.asyncio
async def test_ontario_snapshot_rejects_fill_missing_before_creating_a_run():
    @asynccontextmanager
    async def unused_database():
        raise AssertionError("unsupported mode must fail before database access")
        yield

    with pytest.raises(ValueError, match="CA-ON publishes a complete snapshot"):
        await execute_monthly_pipeline(
            _Service(),
            config=CONFIGS["CA-ON"],
            task=SimpleNamespace(task_uuid="ca-on-fill"),
            source="pho_idto_monthly",
            force=False,
            process=True,
            save_raw=False,
            fill_missing=True,
            updater=_Updater(),
            get_database=unused_database,
            task_manager=_TaskManager(),
            crawl_run_type=_CrawlRun,
            result_type=lambda *values: values,
            logger=_Logger(),
        )
