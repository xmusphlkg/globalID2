"""Iceland history commits source observations before safe legacy projections."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.crawl_service import CrawlService
from src.services.ingestion.countries.is_ import (
    IcelandCurrentCommitStrategy,
    IcelandHistoryCommitStrategy,
)


@pytest.fixture(autouse=True)
def country_exists(monkeypatch):
    monkeypatch.setattr(
        "src.services.ingestion.countries.is_.ensure_iceland_country", AsyncMock()
    )


async def test_history_store_reports_real_series_count_and_keeps_atomic_order(
    monkeypatch,
):
    order = []
    db = object()

    async def save(session, *args):
        assert session is db
        order.append("series")
        return SimpleNamespace(upserted=2)

    async def imported(session, *args, **kwargs):
        assert session is db
        order.append("legacy")
        return SimpleNamespace(inserted_or_updated=1)

    async def dirty(session, **kwargs):
        assert session is db and kwargs["scope_key"] == "IS"
        order.append("dirty")

    monkeypatch.setattr("src.services.ingestion.countries.is_.mark_dirty", dirty)
    result = await IcelandHistoryCommitStrategy().commit(
        db,
        service=SimpleNamespace(_save_series_rows=save),
        updater=SimpleNamespace(import_rows=imported),
        fetched=SimpleNamespace(
            series_rows=[{}, {}, {}], rows=[{}], source_latest_date=None
        ),
        force=True,
        task_uuid="run",
    )
    assert result.inserted_or_updated == 2
    assert order == ["series", "legacy", "dirty"]


@pytest.mark.parametrize("failure", ["series", "legacy"])
async def test_history_store_failure_never_marks_published_scope(failure, monkeypatch):
    dirty = AsyncMock()
    monkeypatch.setattr("src.services.ingestion.countries.is_.mark_dirty", dirty)
    service = SimpleNamespace(
        _save_series_rows=AsyncMock(
            return_value=SimpleNamespace(upserted=2),
            side_effect=RuntimeError("series store unavailable")
            if failure == "series"
            else None,
        )
    )
    updater = SimpleNamespace(
        import_rows=AsyncMock(
            side_effect=RuntimeError("legacy projection unavailable")
            if failure == "legacy"
            else None
        )
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        await IcelandHistoryCommitStrategy().commit(
            object(),
            service=service,
            updater=updater,
            fetched=SimpleNamespace(
                series_rows=[{}], rows=[{}], source_latest_date=None
            ),
            force=False,
            task_uuid="run",
        )
    dirty.assert_not_awaited()
    if failure == "series":
        updater.import_rows.assert_not_awaited()


@pytest.mark.parametrize(
    "source",
    [
        "all",
        "is_doh_annual",
        "is_doh_sti",
        "is_doh_respiratory",
        "is_doh_history",
        "is_doh_legacy_icd",
    ],
)
async def test_every_reviewed_iceland_source_dispatches_the_three_stage_runner(
    source, monkeypatch
):
    from src.data.processors import ISMultiFrequencyUpdater
    from src.data.processors.is_history import IcelandHistoryProcessor

    run = AsyncMock(return_value="stored")
    monkeypatch.setattr("src.services.ingestion.runner.run_surveillance", run)
    result = await CrawlService()._execute_is_mixed(
        task=SimpleNamespace(input_data={}),
        source=source,
        force=False,
        process=True,
        save_raw=True,
        fill_missing=False,
        updater=ISMultiFrequencyUpdater(),
    )
    assert result == "stored"
    options = run.call_args.kwargs
    assert options["source"] == source
    assert isinstance(
        options["updater"],
        IcelandHistoryProcessor
        if source in {"is_doh_history", "is_doh_legacy_icd"}
        else ISMultiFrequencyUpdater,
    )
    assert (
        isinstance(options["strategy"], IcelandHistoryCommitStrategy)
        if source in {"is_doh_history", "is_doh_legacy_icd"}
        else isinstance(options["strategy"], IcelandCurrentCommitStrategy)
    )
