"""A matching annual total must not mask wrong Colombia source facts."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import verify_co_sivigila_history as verifier


def source_row(record_id, cases):
    return {
        "SourceRecordID": record_id,
        "Date": "2007-01-01",
        "GeographyKey": "country:CO:source-municipality:08372",
        "SourceDiseaseCode": "sivigila_210",
        "DefinitionVersion": "co-ins-sivigila-municipality-week-v1",
        "Cases": str(cases),
        "Dimensions": '{"source_epi_year": 2007}',
    }


def database_row(row):
    return (
        row,
        float(row["Cases"]),
        datetime(2007, 1, 1, tzinfo=timezone.utc),
        row["GeographyKey"],
        verifier._expected_record(row)[-1],
        row["SourceDiseaseCode"],
        row["DefinitionVersion"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_rows,expected_mismatch,expected_duplicate",
    [
        ([source_row("a", 99), source_row("b", 2)], 1, 0),
        ([source_row("a", 1), source_row("a", 1)], 0, 1),
    ],
)
async def test_equal_year_total_does_not_hide_wrong_or_duplicate_rows(
    monkeypatch,
    database_rows,
    expected_mismatch,
    expected_duplicate,
):
    source = [source_row("a", 1), source_row("b", 2)]

    def source_spool(*args):
        spool = sqlite3.connect(":memory:", check_same_thread=False)
        spool.execute(
            "CREATE TABLE expected (record_id TEXT PRIMARY KEY, digest TEXT, "
            "report_date TEXT, geography TEXT, source_code TEXT, "
            "definition_version TEXT, value REAL, dimension_key TEXT)"
        )
        spool.execute("CREATE TABLE seen_db (record_id TEXT PRIMARY KEY)")
        spool.executemany(
            "INSERT INTO expected VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (row["SourceRecordID"], *verifier._expected_record(row))
                for row in source
            ),
        )
        return spool, 2, 1, True

    monkeypatch.setattr(verifier, "_source_rows", source_spool)

    class Database:
        async def execute(self, statement):
            return None

        async def stream(self, statement, parameters):
            async def rows():
                for row in database_rows:
                    yield database_row(row)

            return rows()

    @asynccontextmanager
    async def database():
        yield Database()

    async def dispose():
        pass

    monkeypatch.setattr(verifier, "get_db", database)
    monkeypatch.setattr(verifier, "dispose_database", dispose)
    state = {
        "status": "complete",
        "identity": {
            "year_counts": {"2007": 2},
            "dataset_version": {"rows_updated_at": 1},
        },
    }
    report = await verifier.verify(state)
    year = report["years"]["2007"]
    assert year["official_rows"] == year["database_rows"] == 2
    assert year["mismatched_rows"] == expected_mismatch
    assert year["duplicate_database_record_ids"] == expected_duplicate
    assert report["complete"] is False


def test_cross_page_source_record_id_collision_is_rejected(monkeypatch, tmp_path):
    row = source_row("same-id", 1)
    monkeypatch.setattr(verifier, "load_archive_manifest", lambda *args: [object()])
    monkeypatch.setattr(
        verifier.co,
        "organize",
        lambda *args, **kwargs: SimpleNamespace(
            rows=[row],
            source_audit={
                "dataset_version": {"rows_updated_at": 1},
                "year_source_rows": 2,
            },
        ),
    )
    state = {
        "identity": {
            "year_counts": {"2007": 2},
            "page_limit": 1,
            "dataset_version": {"rows_updated_at": 1},
        },
        "batches": {
            f"2007:{offset}": {
                "year": 2007,
                "offset": offset,
                "rows": 1,
                "manifest": "ignored",
                "applied": True,
            }
            for offset in (0, 1)
        },
    }
    with pytest.raises(ValueError, match="Duplicate CO source record ID"):
        verifier._source_rows(state, 2007, object(), tmp_path / "source.sqlite")


def test_physical_value_is_checked_even_when_raw_source_row_matches():
    row = source_row("a", 1)
    expected = verifier._expected_record(row)
    actual = (*expected[:5], 2.0, expected[6])
    assert verifier._different_fields(expected, actual) == ["value"]
