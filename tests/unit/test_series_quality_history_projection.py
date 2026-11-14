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
