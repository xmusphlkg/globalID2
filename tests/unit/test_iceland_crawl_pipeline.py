from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.is_history import IcelandHistoryCrawler
from src.data.processors.is_history import (
    IcelandHistoryImportResult,
    IcelandHistoryProcessor,
)
from src.services.crawl_pipelines.is_ import execute_is_pipeline


class _Database:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.added[-1].id = 73

    async def commit(self) -> None:
        self.commits += 1


class _CrawlRun:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)
        self.id = None


class _TaskManager:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []
        self.progress: list[int] = []

    async def add_workbook_entry(self, _task_uuid: str, **entry: object) -> None:
        self.entries.append(entry)

    async def update_task_progress(self, _task_uuid: str, progress: int) -> None:
        self.progress.append(progress)


class _Service:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.finished: list[tuple[object, dict[str, object]]] = []
        self.fail_save = fail_save

    async def _add_raw_archive_entry(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def _save_series_rows(self, *_args: object, **_kwargs: object):
        if self.fail_save:
            raise RuntimeError("series store unavailable")
        # Deliberately lower than the three prepared rows: the pipeline must
        # report this real store result rather than len(prepared.series_rows).
        return SimpleNamespace(upserted=2)

    async def _finish_crawl_run(self, run_id: object, **values: object) -> None:
        self.finished.append((run_id, values))


async def _noop_ensure(_db: object) -> None:
    return None


def _install_success_fixtures(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.crawl_pipelines.is_._ensure_is_country", _noop_ensure
    )
    monkeypatch.setattr(
        IcelandHistoryCrawler,
        "download_history",
        lambda *_args, **_kwargs: SimpleNamespace(
            raw_files=[object(), object()],
            manifest_path=Path("raw_manifest.json"),
        ),
    )
    prepared = SimpleNamespace(
        rows=[{"projection": 1}],
        series_rows=[{"series": 1}, {"series": 2}, {"series": 3}],
        quarantine=[],
        manifest={"counts": {"series_rows": 3}, "quarantine": {}},
    )
    monkeypatch.setattr(
        IcelandHistoryProcessor,
        "prepare_manifest",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(
        IcelandHistoryProcessor,
        "write_outputs",
        staticmethod(
            lambda *_args, **_kwargs: {"manifest.json": Path("manifest.json")}
        ),
    )

    async def import_rows(*_args, **_kwargs):
        return IcelandHistoryImportResult(
            inserted_or_updated=0,
            skipped_unmapped=0,
            source_latest_date=None,
            skipped_current_precedence=1,
        )

    monkeypatch.setattr(IcelandHistoryProcessor, "import_rows", import_rows)


@pytest.mark.asyncio
async def test_history_pipeline_reports_real_series_save_count_and_completes(
    monkeypatch,
) -> None:
    _install_success_fixtures(monkeypatch)
    databases: list[_Database] = []

    @asynccontextmanager
    async def get_database():
        db = _Database()
        databases.append(db)
        yield db

    service = _Service()
    manager = _TaskManager()
    result = await execute_is_pipeline(
        service,
        task=SimpleNamespace(task_uuid="is-history-success", input_data={}),
        source="is_doh_history",
        force=True,
        process=True,
        save_raw=True,
        fill_missing=False,
        updater=object(),
        get_database=get_database,
        task_manager=manager,
        crawl_run_type=_CrawlRun,
        result_type=lambda **values: SimpleNamespace(**values),
        logger=SimpleNamespace(warning=lambda *_args: None),
    )

    assert result.new_reports == 2
    assert result.total_records == 2
    assert service.finished == [
        (73, {"new_reports": 2, "processed": 1, "records": 2})
    ]
    assert databases[1].commits == 1
    assert any(
        "skipped_due_current: 1" in str(entry.get("content") or "")
        for entry in manager.entries
    )


@pytest.mark.asyncio
async def test_history_pipeline_marks_run_failed_when_download_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.crawl_pipelines.is_._ensure_is_country", _noop_ensure
    )

    def fail_download(*_args, **_kwargs):
        raise RuntimeError("official workbook unavailable")

    monkeypatch.setattr(IcelandHistoryCrawler, "download_history", fail_download)

    @asynccontextmanager
    async def get_database():
        yield _Database()

    service = _Service()
    manager = _TaskManager()
    with pytest.raises(RuntimeError, match="official workbook unavailable"):
        await execute_is_pipeline(
            service,
            task=SimpleNamespace(task_uuid="is-history-fail", input_data={}),
            source="is_doh_history",
            force=False,
            process=True,
            save_raw=True,
            fill_missing=False,
            updater=object(),
            get_database=get_database,
            task_manager=manager,
            crawl_run_type=_CrawlRun,
            result_type=lambda **values: SimpleNamespace(**values),
            logger=SimpleNamespace(warning=lambda *_args: None),
        )

    assert service.finished[0][0] == 73
    assert service.finished[0][1]["status"] == "failed"
    assert "official workbook unavailable" in str(service.finished[0][1]["error"])


@pytest.mark.asyncio
async def test_history_pipeline_marks_run_failed_when_series_save_fails(
    monkeypatch,
) -> None:
    _install_success_fixtures(monkeypatch)

    @asynccontextmanager
    async def get_database():
        yield _Database()

    service = _Service(fail_save=True)
    with pytest.raises(RuntimeError, match="series store unavailable"):
        await execute_is_pipeline(
            service,
            task=SimpleNamespace(task_uuid="is-history-save-fail", input_data={}),
            source="is_doh_history",
            force=False,
            process=True,
            save_raw=True,
            fill_missing=False,
            updater=object(),
            get_database=get_database,
            task_manager=_TaskManager(),
            crawl_run_type=_CrawlRun,
            result_type=lambda **values: SimpleNamespace(**values),
            logger=SimpleNamespace(warning=lambda *_args: None),
        )

    assert service.finished[0][1]["status"] == "failed"
    assert "series store unavailable" in str(service.finished[0][1]["error"])
