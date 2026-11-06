from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from src.data.processors.tw import TWMonthlyUpdater
from src.services import crawl_service
from src.services.crawl_service import CrawlService
from src.services.ingestion import runner


def test_tw_recent_window_crosses_year_and_respects_current_month():
    updater = TWMonthlyUpdater(refresh_recent_months=4)
    assert updater._default_recent_months(as_of=date(2026, 2, 15)) == [
        (2025, 10), (2025, 11), (2025, 12), (2026, 1)
    ]
    updater.include_current_month = True
    assert updater._default_recent_months(as_of=date(2026, 2, 15)) == [
        (2025, 11), (2025, 12), (2026, 1), (2026, 2)
    ]


def test_tw_runtime_configuration_applies_revision_window_and_open_month():
    updater = TWMonthlyUpdater()
    CrawlService._configure_monthly_runtime_policy(
        updater, country_code='TW', source='nidss_open_data',
        include_current_month=True, revision_window_months=5,
    )
    assert updater.include_current_month is True
    assert updater.refresh_recent_months == 5
    assert len(updater._default_recent_months(as_of=date(2026, 2, 15))) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('options,fill_missing,force,expected', [
    ({'scheduled_trigger': True}, True, False, 'recent'),
    ({'scheduled_trigger': True}, False, False, 'recent'),
    ({'scheduled_trigger': True, 'backfill_history': True}, False, False, 'recent'),
    ({'scheduled_trigger': True}, False, True, 'recent'),
    ({'manual_trigger': True}, True, False, 'missing'),
    ({'manual_trigger': True, 'start_year': 2000}, True, False, 'bounded'),
    ({'manual_trigger': True}, True, True, 'missing'),
])
async def test_tw_dispatch_plans_automatic_and_explicit_history(monkeypatch, options, fill_missing, force, expected):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 2, 15, tzinfo=tz)

    monkeypatch.setattr(crawl_service, 'datetime', FixedDateTime)
    captured = {}

    async def staged(_service, **kwargs):
        captured.update(kwargs)
        return 'ok'
    monkeypatch.setattr(runner, 'run_surveillance', staged)

    @asynccontextmanager
    async def fake_database():
        assert expected in {'missing', 'bounded'}, 'recent planning must not query the database'
        yield object()
    monkeypatch.setattr(crawl_service, 'get_database', fake_database)
    updater = TWMonthlyUpdater()
    async def existing(_db):
        return {(year, month) for year in range(1998, 2027) for month in range(1, 13)
                if (year, month) not in {(1999, 3), (2024, 3)}}
    updater.get_db_months = existing
    task = SimpleNamespace(input_data=options)
    result = await CrawlService()._execute_configured_monthly(
        'TW', task=task, updater=updater, source='nidss_open_data',
        force=force, process=True, save_raw=True, fill_missing=fill_missing,
    )
    assert result == 'ok'
    months = captured['connector_options']['months']
    assert (2026, 2) not in months
    if expected == 'recent':
        assert months == [(2025, 11), (2025, 12), (2026, 1)]
    elif expected == 'bounded':
        assert months == [(2024, 3), (2025, 11), (2025, 12), (2026, 1)]
    else:
        assert months == [(1999, 3), (2024, 3), (2025, 11), (2025, 12), (2026, 1)]


@pytest.mark.asyncio
async def test_tw_scheduled_dispatch_can_explicitly_include_current_month(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 2, 15, tzinfo=tz)

    monkeypatch.setattr(crawl_service, 'datetime', FixedDateTime)
    captured = {}

    async def staged(_service, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(runner, 'run_surveillance', staged)
    updater = TWMonthlyUpdater(include_current_month=True, refresh_recent_months=2)
    await CrawlService()._execute_configured_monthly(
        'TW', task=SimpleNamespace(input_data={'scheduled_trigger': True}),
        updater=updater, source='nidss_open_data', force=False, process=True,
        save_raw=True, fill_missing=True,
    )
    assert captured['connector_options']['months'] == [(2026, 1), (2026, 2)]
