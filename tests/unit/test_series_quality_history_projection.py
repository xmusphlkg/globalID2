"""JP history reads preserve quality decisions without decoding provenance."""
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from src.data.storage.series_observation_store import (
    SeriesObservationQualityPolicy,
    SeriesObservationStore,
)
from src.domain import DiseaseSeriesObservation


def observation(code, month, value, *, suppressed=False):
    return {
        "time": datetime(2026, month, 1, tzinfo=timezone.utc),
        "series_code": code,
        "geography_key": "country:JP-01:national",
        "dimension_key": "all",
        "value": value,
        "unit": "count",
        "suppressed": suppressed,
        "quality_status": "raw",
        "metadata": {"frequency": "monthly"},
    }


@pytest.mark.parametrize("authoritative", [False, True])
@pytest.mark.parametrize("mode", ["report", "quarantine", "fail_closed"])
def test_history_metadata_does_not_change_quality_or_blocking(mode, authoritative):
    incoming, history = [], []
    for number in range(6):
        code = f"SER_JP_EXAMPLE_{number}"
        for month in (1, 3, 5):
            previous = observation(code, month, 100)
            # Historical revision flags never authorize an incoming change;
            # incoming series frequency takes precedence over old metadata.
            previous["metadata"] = {
                "frequency": "weekly", "authoritative_revision": True,
                "source_artifact": "historical-source.json",
            }
            history.append(previous)
        for month in (3, 5):
            current = observation(code, month, 0)
            current["metadata"]["authoritative_revision"] = authoritative
            incoming.append(current)
    history.append(observation("SER_JP_SUPPRESSED", 3, None, suppressed=True))
    incoming.append(observation("SER_JP_SUPPRESSED", 3, 0))
    policy = SeriesObservationQualityPolicy(mode=mode)
    full = SeriesObservationStore.assess_quality(
        incoming, country_code="JP", existing_observations=history, policy=policy,
    )
    compact = SeriesObservationStore.assess_quality(
        incoming, country_code="JP", policy=policy,
        existing_observations=[{k: v for k, v in row.items() if k != "metadata"} for row in history],
    )
    assert compact.to_dict() == full.to_dict()
    assert policy.blocks(compact) == policy.blocks(full)
    assert "projected_series_time_gap" in {issue.code for issue in compact.issues}
    if not authoritative:
        assert "single_series_positive_overwritten_by_zero" in {issue.code for issue in compact.issues}


@pytest.mark.asyncio
async def test_compact_history_avoids_json_decode_and_keeps_stored_provenance():
    decoded = []

    def decode(value):
        decoded.append(len(value))
        return json.loads(value)

    engine = create_async_engine("sqlite+aiosqlite://", json_deserializer=decode)
    table = DiseaseSeriesObservation.__table__
    row = observation("SER_JP_EXAMPLE", 3, 100)
    row["raw_data"] = {"source_url": "https://example.test/weekly.csv", "original_value": "100"}
    row["metadata"]["retained_source_details"] = "x" * 100_000
    try:
        async with engine.begin() as connection:
            # Create the real observation table without PostgreSQL-only indexes.
            await connection.execute(CreateTable(table))
            await connection.execute(insert(table), [row])
        async with async_sessionmaker(engine)() as db:
            policy = SeriesObservationQualityPolicy()
            original = await SeriesObservationStore._load_recent_history(db, [row], policy)
            assert decoded  # The default contract still returns metadata.
            decoded.clear()
            compact = await SeriesObservationStore._load_recent_history(
                db, [row], policy, include_metadata=False,
            )
            assert decoded == []
            assert compact == [{k: v for k, v in old.items() if k != "metadata"} for old in original]
            source = (await db.execute(select(table.c.raw_data, table.c.metadata))).one()
            assert source.raw_data == row["raw_data"]
            assert source.metadata == row["metadata"]
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frequency", "predecessor"),
    [
        ("weekly", datetime(2011, 12, 25, tzinfo=timezone.utc)),
        ("monthly", datetime(2011, 12, 1, tzinfo=timezone.utc)),
        ("annual", datetime(2011, 1, 1, tzinfo=timezone.utc)),
    ],
)
async def test_relevant_history_keeps_preceding_period_and_same_identity(
    frequency, predecessor,
):
    engine = create_async_engine("sqlite+aiosqlite://")
    table = DiseaseSeriesObservation.__table__
    current = datetime(2012, 1, 1, tzinfo=timezone.utc)
    distant = datetime(2007, 1, 1, tzinfo=timezone.utc)
    incoming = []
    stored = []
    for number in range(5):
        code = f"HOLD_CO_EXAMPLE_{number}"
        for when in (distant, predecessor, current):
            stored.append({
                "time": when, "series_code": code,
                "geography_key": "country:CO:municipality:11001",
                "dimension_key": "all", "value": 100, "unit": "count",
                "suppressed": False, "quality_status": "raw",
                "metadata": {"frequency": frequency},
            })
        incoming.append({
            **stored[-1], "value": 0,
        })
    policy = SeriesObservationQualityPolicy(
        mode="fail_closed", history_lookback_days=20_000,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateTable(table))
            await connection.execute(insert(table), stored)
        async with async_sessionmaker(engine)() as db:
            full = await SeriesObservationStore._load_recent_history(
                db, incoming, policy,
            )
            bounded = await SeriesObservationStore._load_recent_history(
                db, incoming, policy, relevant_periods_only=True,
            )
        assert len(full) == 15
        assert len(bounded) == 10
        assert {row["time"].replace(tzinfo=timezone.utc) for row in bounded} == {
            predecessor, current,
        }
        original_report = SeriesObservationStore.assess_quality(
            incoming, country_code="CO", existing_observations=full, policy=policy,
        )
        bounded_report = SeriesObservationStore.assess_quality(
            incoming, country_code="CO", existing_observations=bounded, policy=policy,
        )
        assert bounded_report.issues == original_report.issues
        assert policy.blocks(bounded_report)
        assert {issue.code for issue in bounded_report.issues} >= {
            "cross_series_zero_overwrites_positive",
            "single_series_positive_overwritten_by_zero",
        }
    finally:
        await engine.dispose()
