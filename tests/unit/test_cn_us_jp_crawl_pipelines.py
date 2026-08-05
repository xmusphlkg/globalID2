from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines.cn import execute_cn_pipeline
from src.services.crawl_pipelines.jp import execute_jp_pipeline
from src.services.crawl_pipelines.us import execute_us_pipeline


class _Database:
    def add(self, value):
        value.id = 71

    async def flush(self):
        return None


class _DatabaseContext:
    async def __aenter__(self):
        return _Database()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _get_database():
    return _DatabaseContext()


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
        self.finished = []
        self.archives = []
        self.import_calls = []

    async def _add_raw_archive_entry(self, task_uuid, **values):
        self.archives.append((task_uuid, values))

    async def _finish_crawl_run(self, run_id, *args, **kwargs):
        self.finished.append((run_id, args, kwargs))

    async def _import_rows_with_series(self, db, updater, rows, **kwargs):
        self.import_calls.append((db, updater, rows, kwargs))
        return SimpleNamespace(
            inserted_or_updated=3,
            skipped_unmapped=1,
            imported_new_data=True,
        )

    @staticmethod
    def _source_distribution(results):
        counts = {}
        for result in results:
            source = result.metadata.get("source", "Unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts


def _run_type(**values):
    return SimpleNamespace(id=None, **values)


def _result_type(new_reports, processed_reports, total_records, crawl_run_id):
    return SimpleNamespace(
        new_reports=new_reports,
        processed_reports=processed_reports,
        total_records=total_records,
        crawl_run_id=crawl_run_id,
    )


def _task():
    return SimpleNamespace(task_uuid="task-1", input_data={})


@pytest.mark.asyncio
async def test_cn_empty_source_finishes_without_constructing_processor():
    manager = _TaskManager()
    service = _Service()

    class Crawler:
        last_crawl_stats = {
            "max_date": "2026-01-01",
            "missing_months": [],
            "missing_months_count": 0,
            "total_candidates": 4,
        }

        async def crawl(self, **kwargs):
            assert kwargs == {
                "source": "cdc_weekly",
                "force": False,
                "fill_missing": True,
            }
            return []

    class Processor:
        def __init__(self, **kwargs):
            raise AssertionError("empty source must not construct a processor")

    result = await execute_cn_pipeline(
        service,
        task=_task(),
        country_code="CN",
        source="cdc_weekly",
        force=False,
        process=True,
        save_raw=False,
        fill_missing=True,
        get_database=_get_database,
        task_manager=manager,
        crawl_run_type=_run_type,
        result_type=_result_type,
        crawler_type=Crawler,
        processor_type=Processor,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert (result.new_reports, result.processed_reports, result.total_records) == (
        0,
        0,
        0,
    )
    assert result.crawl_run_id == 71
    assert service.finished == [
        (71, (), {"new_reports": 0, "processed": 0, "records": 0})
    ]
    assert manager.progress[-1] == ("task-1", 100)


@pytest.mark.asyncio
async def test_cn_crawler_failure_propagates_without_false_completion():
    manager = _TaskManager()
    service = _Service()

    class Crawler:
        async def crawl(self, **kwargs):
            raise RuntimeError("upstream unavailable")

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await execute_cn_pipeline(
            service,
            task=_task(),
            country_code="CN",
            source="cdc_weekly",
            force=False,
            process=True,
            save_raw=False,
            fill_missing=False,
            get_database=_get_database,
            task_manager=manager,
            crawl_run_type=_run_type,
            result_type=_result_type,
            crawler_type=Crawler,
            processor_type=object,
            logger=SimpleNamespace(warning=lambda message: None),
        )

    assert service.finished == []
    assert manager.progress[-1] == ("task-1", 10)


@pytest.mark.asyncio
async def test_us_success_preserves_series_rows_and_import_summary():
    manager = _TaskManager()
    service = _Service()
    rows = [{"Date": "2026-01-03"}]
    series_rows = [{"Date": "2026-01-03", "ReportingArea": "US RESIDENTS"}]

    class Updater:
        def fetch_latest(self, *, source):
            assert source == "all"
            return SimpleNamespace(
                rows=rows,
                series_rows=series_rows,
                latest_date=date(2026, 1, 3),
                latest_by_source={"US CDC NNDSS": date(2026, 1, 3)},
                source_ref="cdc-api",
            )

        async def get_db_latest_date(self, db):
            return date(2025, 12, 27)

    result = await execute_us_pipeline(
        service,
        task=_task(),
        source="all",
        force=True,
        process=True,
        save_raw=True,
        fill_missing=False,
        updater=Updater(),
        get_database=_get_database,
        task_manager=manager,
        crawl_run_type=_run_type,
        result_type=_result_type,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert (result.new_reports, result.processed_reports, result.total_records) == (
        3,
        1,
        3,
    )
    assert service.import_calls[0][2] is rows
    assert service.import_calls[0][3] == {
        "series_rows": series_rows,
        "db_latest_date": date(2025, 12, 27),
        "source_latest_date": date(2026, 1, 3),
        "force": True,
    }
    assert service.finished == [
        (71, (), {"new_reports": 3, "processed": 1, "records": 3})
    ]
    assert manager.progress[-1] == ("task-1", 100)


@pytest.mark.asyncio
async def test_jp_process_disabled_refreshes_source_without_importing():
    manager = _TaskManager()
    service = _Service()

    class Updater:
        def refresh_source(self, **kwargs):
            assert kwargs == {
                "source": "jp_weekly",
                "run_external": False,
                "force": False,
                "fill_missing": False,
                "save_raw": False,
                "raw_dir": None,
            }
            return SimpleNamespace(
                rows=[{"Date": "2026-01-04"}],
                source_latest_date=date(2026, 1, 4),
                source_csv="jp.csv",
                script_logs=["refreshed"],
            )

        async def get_db_latest_date(self, db):
            return date(2025, 12, 28)

    result = await execute_jp_pipeline(
        service,
        task=_task(),
        source="jp_weekly",
        force=False,
        process=False,
        save_raw=False,
        fill_missing=False,
        updater=Updater(),
        get_database=_get_database,
        task_manager=manager,
        crawl_run_type=_run_type,
        result_type=_result_type,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert (result.new_reports, result.processed_reports, result.total_records) == (
        0,
        0,
        0,
    )
    assert service.import_calls == []
    assert service.finished == [
        (71, (), {"new_reports": 0, "processed": 0, "records": 0})
    ]
    assert any(entry[1]["title"] == "JP Update Logs" for entry in manager.entries)
