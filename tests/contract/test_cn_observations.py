"""China keeps the incremental gate and per-report transaction boundary."""
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.data.crawlers.base import CrawlerResult
from src.services.ingestion.countries import cn
from src.services.ingestion.strategies import ChinaReportCommitStrategy
from src.domain.collection import PublishDirty
from pathlib import Path
import hashlib
import json

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/CN"


async def test_cn_offline_source_observations_preserve_zero_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.data.processors import cn as processor_module
    from src.data.storage.series_observation_store import SeriesObservationStore
    from tests.contract.compare import assert_observations_match

    class Mapper:
        async def map_dataframe(self, df, *, disease_col):
            frame = df.copy()
            ids = {"甲型肝炎": "D007", "乙型肝炎": "D008", "丙型肝炎": "D009"}
            names = {"甲型肝炎": "Hepatitis A", "乙型肝炎": "Hepatitis B", "丙型肝炎": "Hepatitis C"}
            frame["disease_id"] = frame[disease_col].map(ids)
            frame["standard_name_en"] = frame[disease_col].map(names)
            frame["standard_name_zh"] = frame[disease_col]
            return frame

    monkeypatch.setattr(processor_module, "create_disease_mapper", AsyncMock(return_value=Mapper()))
    processor = processor_module.DataProcessor(output_dir=tmp_path / "processed")
    monkeypatch.setattr(processor.parser, "fetch_and_parse", lambda *a, **kw: pytest.fail("organization fetched a report"))
    payload = (FIXTURES / "report.html").read_bytes()
    path = Path("data/raw/cn/contract/report.html")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    artifact = SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), metadata_={"title": "report", "url": "https://example.com/report", "date": "2025-02-01T00:00:00", "year_month": "2025 February", "metadata": {"language": "zh", "source": "China CDC Weekly", "ontology_source_id": "SRC_CN_CDC"}})
    reports, failed = await cn.organize([artifact], processor=processor)
    assert failed == 0
    frame = reports[0][1]
    assert frame.loc[frame.RawDiseaseLabel == "甲型肝炎", "Cases"].iloc[0] == 0
    import pandas as pd
    assert pd.isna(frame.loc[frame.RawDiseaseLabel == "乙型肝炎", "Cases"].iloc[0])
    built = SeriesObservationStore().build_observations(frame.to_dict("records"), "CN", source_id="SRC_CN_CDC", geography_key="country:CN:national")
    assert built.observations
    assert_observations_match(built.observations, json.loads((FIXTURES / "observations.json").read_text()))


async def test_older_month_never_reaches_report_parser(monkeypatch):
    older = CrawlerResult(title="old", content="must not parse", date=datetime(2025, 1, 1), year_month="2025 January")
    newer = CrawlerResult(title="new", content="<table></table>", date=datetime(2025, 2, 1), year_month="2025 February")
    crawler = SimpleNamespace(fetch_list=AsyncMock(return_value=[older, newer]), _select_preferred_period_results=lambda rows: rows, check_new_data=AsyncMock(return_value={"new": [newer]}))
    writer = SimpleNamespace(task_uuid="task", attempt=1, artifacts=[])

    async def write(payload, **kwargs):
        writer.artifacts.append((payload, kwargs))
        return writer.artifacts[-1]
    writer.write = write
    monkeypatch.setattr(cn.task_manager, "is_cancel_requested", AsyncMock(return_value=False))
    saved, failed = await cn.collect(writer, crawler=crawler, source="all", force=False, fill_missing=False)
    assert failed == 0
    assert len(saved) == 1
    assert saved[0][1]["metadata"]["year_month"] == "2025 February"
    crawler.check_new_data.assert_awaited_once()


async def test_one_report_rollback_preserves_successful_report_and_dirty(monkeypatch):
    pytest.importorskip("aiosqlite")
    from src.core import database
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(PublishDirty.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_maker", maker)
    committed = []

    class Processor:
        async def store_report(self, db, frame, report):
            if report.title == "bad":
                # A pending dirty mark must be rolled back with this report.
                from src.services.ingestion.stages import mark_dirty
                await mark_dirty(db, line="surveillance", scope_key="BAD", task_uuid="same-task")
                raise ValueError("bad report")
            committed.append(report.title)

    try:
        result = await ChinaReportCommitStrategy().commit_reports([(CrawlerResult(title="ok"), [1]), (CrawlerResult(title="bad"), [2])], processor=Processor(), task_uuid="same-task")
        assert result == (1, 1, 1)
        assert committed == ["ok"]
        async with maker() as db:
            dirty = list((await db.execute(select(PublishDirty))).scalars())
            assert [(row.scope_key, row.source_task_uuid) for row in dirty] == [("CN", "same-task")]
    finally:
        await engine.dispose()
