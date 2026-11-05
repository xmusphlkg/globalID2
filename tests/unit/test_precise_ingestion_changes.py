"""Only accepted, completely covered observation changes drive precise releases."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.data.storage.series_observation_store import (
    SeriesObservationBuildResult, SeriesObservationQualityPolicy,
    SeriesObservationStore, observation_publication_changes,
)
from src.services.ingestion.stages import mark_observation_changes_dirty
from src.services.ingestion.strategies import (
    CanadaCndssWindowCommitStrategy, EcdcWindowCommitStrategy,
)


def _point(**overrides):
    return {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "series_code": "SER_TEST", "geography_key": "country:US-AK:national",
        "dimension_key": "all", "value": 10, "unit": "count",
        "suppressed": False, "suppression_reason": None,
        "quality_status": "validated", "dimensions": {},
        "metadata": {"frequency": "monthly"}, "raw_data": {}, **overrides,
    }


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


@pytest.mark.parametrize("before,accepted,expected", [
    (_point(), _point(), False),
    (None, _point(), True),
    (_point(), _point(value=12), True),
    (_point(), _point(quality_status="final"), True),
    (_point(), _point(suppressed=True, suppression_reason="source_suppressed"), True),
    (_point(), _point(metadata={"frequency": "annual"}), True),
    (_point(), _point(raw_data={"artifact": "new-location"},
                     metadata={"frequency": "monthly", "retrieved_at": "later"}), False),
])
async def test_save_reports_accepted_semantics_not_proposal_or_upsert_count(
    monkeypatch, before, accepted, expected,
):
    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.data.storage.series_observation_store.acquire_disease_data_mutation_lock", noop,
    )

    class Store(SeriesObservationStore):
        def build_observations(self, *_args, **_kwargs):
            # A rejected incoming correction must not cause publication merely
            # because it differs. The database's accepted state is authoritative.
            return SeriesObservationBuildResult([_point(value=999)], 0, 0, 0)

        async def _build_holding_observations(self, *_args, **_kwargs):
            return [], 0

    class DB:
        def __init__(self):
            self.current = deepcopy(before)
            self.revisions = []

        async def execute(self, statement, *params):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            if getattr(statement, "is_select", False):
                if "disease_series_observation_revisions" in sql:
                    return _Result(list(reversed(self.revisions)))
                if "disease_series_observations" in sql:
                    return _Result([self.current] if self.current else [])
                return _Result(["SER_TEST"])
            if "disease_series_observation_revisions" in sql:
                self.revisions.extend(deepcopy(params[0]))
                return _Result([row["time"] for row in params[0]])
            self.current = deepcopy(accepted)
            # SQL upsert count alone cannot distinguish semantic changes.
            return _Result(["SER_TEST"])

    result = await Store().save_rows(
        DB(), [{"Cases": "999"}], "US",
        quality_policy=SeriesObservationQualityPolicy(mode="off"),
    )
    assert result.upserted == 1
    assert result.publication_changes is not None
    assert bool(result.publication_changes) == expected
    if expected:
        assert result.publication_changes[0]["geography_key"] == "country:US-AK:national"


def test_change_ranges_do_not_bridge_unchanged_historical_years():
    changes = observation_publication_changes([
        _point(), _point(time=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _point(time=datetime(2026, 4, 1, tzinfo=timezone.utc)),
    ])
    assert len(changes) == 2
    assert changes[0]["time_end"].startswith("2024-")
    assert changes[1]["time_start"].startswith("2026-01")
    assert changes[1]["time_end"].startswith("2026-04")


@pytest.mark.parametrize("changes,conservative,expected", [
    ((), False, []), (None, False, ["US"]), ((), True, ["US"]),
    (observation_publication_changes([_point()]), False, ["US-AK"]),
    (observation_publication_changes([_point(geography_key="unknown")]), False, ["US"]),
])
async def test_complete_noop_and_legacy_fallback(changes, conservative, expected):
    markers = []
    await mark_observation_changes_dirty(
        SimpleNamespace(add=markers.append),
        result=SimpleNamespace(publication_changes=changes),
        scope_key="US", task_uuid="crawl-1", conservative=conservative,
    )
    assert [row.scope_key for row in markers] == expected
    if expected == ["US-AK"]:
        assert markers[0].change_set == {
            "version": 1, "precise": True, "changes": list(changes),
        }
    elif markers:
        assert markers[0].change_set is None


@pytest.mark.parametrize("strategy,code", [
    (EcdcWindowCommitStrategy, "DE"), (CanadaCndssWindowCommitStrategy, "CA"),
])
async def test_empty_authoritative_replacement_cannot_hide_withdrawals(monkeypatch, strategy, code):
    markers = []

    async def lock(_db):
        pass

    monkeypatch.setattr("src.services.ingestion.strategies.acquire_disease_data_mutation_lock", lock)

    class Updater:
        country_code = code

        async def get_db_latest_date(self, _db):
            return None

        async def delete_authoritative_window(self, _db, **_kwargs):
            return 12

    class Service:
        async def _import_rows_with_series(self, *_args, publication_results, **_kwargs):
            publication_results.append(SimpleNamespace(publication_changes=()))
            return SimpleNamespace(inserted_or_updated=0)

    await strategy().commit(
        SimpleNamespace(add=markers.append), updater=Updater(), service=Service(),
        fetched=SimpleNamespace(rows=[]), force=True, task_uuid="crawl-1", start_year=2024,
    )
    assert [row.scope_key for row in markers] == [code]
    assert markers[0].change_set is None
