"""Exercise the observation model's NULL-value check in PostgreSQL."""

import os

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    MetaData,
    Table,
    insert,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.data.storage.series_observation_store import SeriesObservationStore
from src.domain.disease_ontology import DiseaseSeriesObservation


async def test_postgres_observation_check_accepts_canonical_unreadable_source_cell():
    url = os.environ.get("TEST_SNAPSHOT_DATABASE_URL")
    if not url:
        pytest.skip("TEST_SNAPSHOT_DATABASE_URL must point to a disposable PostgreSQL database")
    check = next(constraint for constraint in DiseaseSeriesObservation.__table__.constraints
                 if constraint.name == "ck_disease_series_observation_value_or_suppressed")
    table = Table(
        "test_observation_missing_status", MetaData(),
        Column("value", Float), Column("suppressed", Boolean, nullable=False),
        Column("metadata", JSON, nullable=False), Column("raw_data", JSON, nullable=False),
        CheckConstraint(str(check.sqltext), name=check.name),
        prefixes=["TEMPORARY"],
    )
    raw = {"Date": "2026-07-01", "DiseaseCode": "2801", "Cases": "",
           "RawDiseaseLabel": "Coronavirus SARS-CoV-2",
           "MissingValueStatus": "source_unreadable", "SourceAccumulatedValueRaw": "4,95"}
    built = SeriesObservationStore().build_observations(
        [raw], "FI", source_id="SRC_FI_THL_TTR")
    observation = built.observations[0]
    assert observation["value"] is None
    assert observation["metadata"]["missing_value_status"] == "source_missing"
    assert observation["metadata"]["missing_value_reason"] == "source_unreadable"
    assert observation["raw_data"]["SourceAccumulatedValueRaw"] == "4,95"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
            await connection.execute(insert(table).values(
                value=observation["value"], suppressed=observation["suppressed"],
                metadata=observation["metadata"], raw_data=observation["raw_data"]))
            for status in ("source_missing", "not_applicable"):
                await connection.execute(insert(table).values(
                    value=None, suppressed=False, metadata={"missing_value_status": status},
                    raw_data={}))
            await connection.execute(insert(table).values(
                value=None, suppressed=True, metadata={}, raw_data={}))
            await connection.execute(insert(table).values(
                value=1, suppressed=False, metadata={}, raw_data={}))
            for status in ("unknown", "source_unreadable"):
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(insert(table).values(
                            value=None, suppressed=False,
                            metadata={"missing_value_status": status} if status else {},
                            raw_data={}))
    finally:
        await engine.dispose()
