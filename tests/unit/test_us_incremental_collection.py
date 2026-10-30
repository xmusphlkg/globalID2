"""US transport planning uses persisted source facts without refetching history."""
from contextlib import asynccontextmanager
from datetime import datetime, date, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.domain import Base, Country, DiseaseSeriesObservation, DiseaseSurveillanceSeries
from src.services.ingestion.countries import us
from src.data.crawlers.us import DEFAULT_CSV_API_URL, NNDSS_ARCHIVE_YEARS
from src.services.exceptions import TaskCancelledError


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    from sqlalchemy import event
    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_functions(connection, _record):
        connection.create_function("btrim", 1, lambda value: value.strip() if value else value, deterministic=True)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            Country.__table__, DiseaseSurveillanceSeries.__table__, DiseaseSeriesObservation.__table__,
        ]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Country(id=1, code="US", name="United States", name_en="United States"))
        for identity, source in [("nndss-holding", "SRC_US_NNDSS"), ("nhss", "SRC_US_NHSS")]:
            db.add(DiseaseSurveillanceSeries(series_code=identity, country_code="US", source_system=source, source_series_code=identity, source_label=identity, disease_id=None, target_group_code="unmapped", mapping_relation="unmapped"))
        await db.commit()
        yield db
    await engine.dispose()


def fact(day, *, series="nndss-holding", geography="country:US:national", year=None):
    return DiseaseSeriesObservation(time=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc), series_code=series, geography_key=geography, dimension_key="all", value=1, unit="count", suppressed=False, quality_status="raw", raw_data={"MMWRYear": str(year or day.year)})


@pytest.mark.parametrize("latest,expected", [
    (date(2021, 1, 2), (2020, 53)),
    (date(2021, 1, 9), (2021, 1)),
    (date(2020, 1, 3), (2020, 1)),
    (date(2025, 11, 15), (2025, 46)),
])
def test_mmwr_boundary_uses_source_calendar_and_keeps_week_53(latest, expected):
    assert us.mmwr_lower_bound(latest) == expected


async def test_unmapped_ledger_advances_window_without_nhss_total_or_other_geography(database):
    database.add_all([
        fact(date(2026, 1, 10)),
        fact(date(2030, 1, 5), series="nhss"),
        fact(date(2031, 1, 4), geography="source:SRC_US_NNDSS:reporting-area:total"),
        fact(date(2028, 1, 1), geography="country:US-CA:national"),
    ])
    await database.commit()
    assert await us.plan_collection(database, country_code="US", source="all") == {
        "nndss_start": (2025, 46), "archive_years": (),
    }


async def test_source_specific_latest_never_uses_global_nhss_latest(database):
    database.add_all([fact(date(2026, 1, 10)), fact(date(2032, 1, 3), series="nhss")])
    await database.commit()
    result = await us.plan_collection(database, country_code="US", source="nndss")
    assert result == {"nndss_start": (2025, 46), "archive_years": ()}


async def test_subdivision_checkpoint_reads_native_fact_geography(database):
    database.add_all([fact(date(2026, 1, 10), geography="country:US-CA:national"), fact(date(2030, 1, 5))])
    await database.commit()
    result = await us.plan_collection(database, country_code="US-CA", source="nndss_api")
    assert result == {"nndss_start": (2025, 46), "archive_years": ()}


async def test_default_does_not_repair_absent_archive_years(database):
    database.add(fact(date(2026, 1, 10)))
    await database.commit()
    result = await us.plan_collection(database, country_code="US", source="nndss_api")
    assert result["archive_years"] == ()


async def test_fill_missing_uses_source_mmwr_year_instead_of_calendar_end_date(database):
    database.add_all([
        fact(date(2026, 1, 10)),
        fact(date(2015, 1, 3), year=2014),
        fact(date(2021, 1, 2), year=2020),
    ])
    await database.commit()
    result = await us.plan_collection(database, country_code="US", source="nndss_api", fill_missing=True)
    assert result["nndss_start"] is None
    assert result["archive_years"] == tuple(year for year in NNDSS_ARCHIVE_YEARS if year not in {2014, 2020})


async def test_old_checkpoint_collects_revision_archive_and_newer_years(database):
    database.add(fact(date(2021, 1, 2), year=2020))
    await database.commit()
    result = await us.plan_collection(database, country_code="US", source="nndss_api")
    assert result["nndss_start"] == (2020, 45)
    assert result["archive_years"] == (2020, 2021)


async def test_empty_nndss_scope_bootstraps_but_nhss_data_does_not_advance_it(database):
    database.add(fact(date(2030, 1, 5), series="nhss"))
    await database.commit()
    assert await us.plan_collection(database, country_code="US", source="all") == {
        "nndss_start": None, "archive_years": NNDSS_ARCHIVE_YEARS,
    }


async def test_explicit_force_and_nhss_only_do_not_need_nndss_checkpoint_queries():
    db = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("unexpected query")))
    assert await us.plan_collection(db, country_code="US", source="all", force=True) == {
        "nndss_start": None, "archive_years": NNDSS_ARCHIVE_YEARS,
    }
    assert await us.plan_collection(db, country_code="US", source="nhss") == {}


def test_incremental_soql_preserves_select_order_and_reporting_area_filter():
    original = parse_qs(urlsplit(DEFAULT_CSV_API_URL).query)
    url = us.csv_page_url(offset=50000, nndss_start=(2020, 53))
    actual = parse_qs(urlsplit(url).query)
    assert actual["$select"] == original["$select"]
    assert actual["$order"] == original["$order"]
    assert actual["$limit"] == ["50000"] and actual["$offset"] == ["50000"]
    assert actual["$where"] == [f"({original['$where'][0]}) AND (year::number > 2020 OR (year::number = 2020 AND week::number >= 53))"]
    full = parse_qs(urlsplit(us.csv_page_url(offset=0)).query)
    assert full["$where"] == original["$where"]


class Writer:
    def __init__(self, on_write=None):
        self.artifacts = []
        self.on_write = on_write
    async def write(self, payload, **metadata):
        self.artifacts.append((payload, metadata))
        if self.on_write:
            self.on_write()


class Crawler:
    def __init__(self, fetch):
        self.get = fetch
        self.session = SimpleNamespace(close=lambda: None)


def response(payload, url="https://example.org/response"):
    import json
    content = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    return SimpleNamespace(content=content, text=content.decode(), url=url, json=lambda: json.loads(content))


async def test_incremental_collect_only_one_short_current_page_without_archive_requests(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return response("year,week,label,m1\n2026,1,Disease,1\n", url)
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: Crawler(fetch))
    monkeypatch.setattr(us, "USNHSSHIVCrawler", lambda: pytest.fail("NNDSS must not request NHSS"))
    writer = Writer()
    await us.collect(writer, source="nndss_api", nndss_start=(2025, 46), archive_years=())
    assert len(calls) == len(writer.artifacts) == 1
    assert "year::number > 2025" in parse_qs(urlsplit(calls[0][0]).query)["$where"][0]


async def test_operator_cancellation_after_durable_page_prevents_next_page_and_archive(monkeypatch):
    from src.core.task_manager import task_manager
    cancelled = False
    def cancel():
        nonlocal cancelled
        cancelled = True
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return response("year,week\n2026,1\n", url)
    monkeypatch.setattr(task_manager, "is_cancel_requested", AsyncMock(side_effect=lambda uuid: cancelled))
    monkeypatch.setattr(us, "SOCRATA_PAGE_SIZE", 1)
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: Crawler(fetch))
    writer = Writer(on_write=cancel)
    with pytest.raises(TaskCancelledError):
        await us.collect(writer, source="all", task_uuid=str(uuid4()))
    assert len(calls) == len(writer.artifacts) == 1
    assert writer.artifacts[0][1]["kind"] == "csv_page"


async def test_cancel_before_source_access_is_respected_inside_collector(monkeypatch):
    from src.core.task_manager import task_manager
    monkeypatch.setattr(task_manager, "is_cancel_requested", AsyncMock(return_value=True))
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: Crawler(lambda *args, **kwargs: pytest.fail("cancelled source requested")))
    with pytest.raises(TaskCancelledError):
        await us.collect(Writer(), source="nndss_api", task_uuid=str(uuid4()))


async def test_dispatch_passes_database_plan_and_cancel_uuid_to_shared_runner(monkeypatch):
    from src.services import crawl_service
    from src.services.ingestion import runner
    db = object()
    @asynccontextmanager
    async def database():
        yield db
    planner = AsyncMock(return_value={"nndss_start": (2025, 46), "archive_years": ()})
    execute = AsyncMock(return_value="done")
    monkeypatch.setattr(crawl_service, "get_database", database)
    monkeypatch.setattr(us, "plan_collection", planner)
    monkeypatch.setattr(runner, "run_surveillance", execute)
    task, updater = SimpleNamespace(task_uuid=str(uuid4())), SimpleNamespace(country_code="US")
    result = await crawl_service.CrawlService()._execute_us_weekly(task=task, updater=updater, source="all", force=False, fill_missing=False, process=True, save_raw=True)
    assert result == "done"
    assert planner.await_args.args == (db,)
    assert planner.await_args.kwargs == {"country_code":"US", "source":"all", "force":False, "fill_missing":False}
    assert execute.await_args.kwargs["connector_options"] == {
        "nndss_start": (2025, 46), "archive_years": (), "subdivision": False, "task_uuid": task.task_uuid,
    }


async def test_cancel_after_archive_catalog_stops_before_any_table_request(monkeypatch):
    from src.core.task_manager import task_manager
    cancelled = False
    writer = Writer()
    def on_write():
        nonlocal cancelled
        cancelled = len(writer.artifacts) == 2
    writer.on_write = on_write
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if url == us.SOCRATA_CATALOG_URL:
            return response({"results":[{"resource":{"id":"fixture", "description":"NNDSS - Table I - 2020. fixture", "columns_name":["Disease, Current week"]}}]}, url)
        return response("year,week\n2026,1\n", url)
    monkeypatch.setattr(task_manager, "is_cancel_requested", AsyncMock(side_effect=lambda uuid: cancelled))
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: Crawler(fetch))
    with pytest.raises(TaskCancelledError):
        await us.collect(writer, source="nndss_api", archive_years=(2020,), task_uuid=str(uuid4()))
    assert len(calls) == 2
    assert [item[1]["kind"] for item in writer.artifacts] == ["csv_page", "archive_json"]


async def test_cancel_after_last_nndss_page_stops_before_nhss_discovery(monkeypatch):
    from src.core.task_manager import task_manager
    checks = 0
    async def cancelled(identity):
        nonlocal checks
        checks += 1
        return checks == 3
    monkeypatch.setattr(task_manager, "is_cancel_requested", cancelled)
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: Crawler(lambda url, **kwargs: response("year,week\n2026,1\n", url)))
    monkeypatch.setattr(us, "USNHSSHIVCrawler", lambda: pytest.fail("NHSS must not start after cancellation"))
    with pytest.raises(TaskCancelledError):
        await us.collect(Writer(), source="all", archive_years=(), task_uuid=str(uuid4()))


async def test_nhss_only_collection_does_not_construct_nndss_crawler(monkeypatch):
    monkeypatch.setattr(us, "USNNDSSCrawler", lambda: pytest.fail("annual NHSS triggered NNDSS history"))
    crawler = Crawler(lambda url: response("workbook bytes", url))
    crawler.discover_current_workbook_url = lambda: "https://example.org/nhss_2024.xlsx"
    monkeypatch.setattr(us, "USNHSSHIVCrawler", lambda: crawler)
    writer = Writer()
    await us.collect(writer, source="nhss_hiv")
    assert [item[1]["kind"] for item in writer.artifacts] == ["nhss_xlsx"]
