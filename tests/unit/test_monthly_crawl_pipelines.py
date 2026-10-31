from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.services.crawl_pipelines import monthly
from src.services.crawl_pipelines.monthly import CONFIGS


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
@pytest.mark.parametrize("country_code,updater_name", [("AU", "AUMonthlyUpdater"), ("NZ", "NZMonthlyUpdater"), ("TW", "TWMonthlyUpdater"), ("HK", "HKMonthlyUpdater"), ("FI", "FIMonthlyUpdater"), ("CA-ON", "CAOntarioMonthlyUpdater"), ("SE", "SEMonthlyUpdater"), ("AT", "ATMonthlyUpdater")])
async def test_monthly_countries_use_staged_connector_and_never_refresh_source(monkeypatch, country_code, updater_name):
    from uuid import uuid4
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import runner
    service = CrawlService()
    updater = service._make_updater(updater_name, country_code)
    def forbidden(**_kwargs):
        raise AssertionError("mixed refresh_source must not execute")
    updater.refresh_source = forbidden
    captured = {}
    async def staged(_service, **kwargs):
        captured.update(kwargs)
        return "staged-result"
    monkeypatch.setattr(runner, "run_surveillance", staged)
    result = await service._execute_configured_monthly(country_code, task=SimpleNamespace(task_uuid=str(uuid4()), input_data={}), source="all", force=False, process=False, save_raw=False, fill_missing=False, updater=updater)
    assert result == "staged-result"
    assert callable(captured["connector"].collect)
    assert callable(captured["connector"].organize)
    assert captured["process"] is False
    assert captured["connector_options"]["output_csv"] == updater.output_csv


@pytest.mark.asyncio
async def test_norway_is_refused_by_generic_monthly_dispatch():
    from src.services.crawl_service import CrawlService
    with pytest.raises(ValueError, match="No staged monthly contract for NO"):
        await CrawlService()._execute_configured_monthly("NO")


@pytest.mark.asyncio
async def test_ontario_fill_missing_still_rejected():
    from src.services.crawl_service import CrawlService
    with pytest.raises(ValueError, match="fill_missing is not supported"):
        await CrawlService()._execute_configured_monthly("CA-ON", fill_missing=True)
