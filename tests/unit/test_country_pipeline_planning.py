"""Window planning is independent of transport and preserves source boundaries."""
from datetime import datetime
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from src.services.ingestion import planning


@pytest.mark.parametrize('value,expected', [('1899',1900),('2099',2026),('invalid',2001),(None,2001)])
def test_history_boundary_is_clamped(value, expected, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026,8,5,tzinfo=tz)
    monkeypatch.setattr(planning,'datetime',Clock)
    assert planning.history_start_year(SimpleNamespace(input_data={'start_year':value}),SimpleNamespace(full_history_start_year=2001)) == expected


async def test_normal_refresh_does_not_query_database(monkeypatch):
    monkeypatch.setattr(planning,'get_db',lambda: pytest.fail('normal refresh queried history'))
    assert await planning.plan_months(SimpleNamespace(input_data={}),object(),force=False,fill_missing=False) is None


async def test_force_passes_bounded_start_year_to_source_history():
    updater=SimpleNamespace(history_months=lambda **kw:[(kw['start_year'],1)])
    assert await planning.plan_months(SimpleNamespace(input_data={}),updater,force=True,fill_missing=False,start_year=2015) == [(2015,1)]


async def test_fill_missing_keeps_revision_window_and_only_adds_gaps(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026,4,5,tzinfo=tz)
    monkeypatch.setattr(planning,'datetime',Clock)
    @asynccontextmanager
    async def database():
        yield object()
    monkeypatch.setattr(planning,'get_db',database)
    updater=SimpleNamespace(refresh_recent_months=2,get_db_months=AsyncMock(return_value={(2026,1)}))
    assert await planning.plan_months(SimpleNamespace(input_data={}),updater,force=False,fill_missing=True,start_year=2026) == [(2026,2),(2026,3),(2026,4)]
