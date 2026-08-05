from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from scripts import backfill_disease_series_from_legacy as backfill


def test_parse_args_is_dry_run_by_default_and_accepts_batch_size() -> None:
    args = backfill.parse_args(["--country", "HK", "--batch-size", "25"])

    assert args.country == "HK"
    assert args.source_id is None
    assert args.batch_size == 25
    assert args.apply is False


def test_recover_source_rows_accepts_dict_list_and_json_string() -> None:
    record_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dict_rows, dict_skipped = backfill.recover_source_rows(
        {"Disease": "A"},
        record_time=record_time,
        record_cases=3,
    )
    list_rows, list_skipped = backfill.recover_source_rows(
        [{"Disease": "B"}, 7, {"Disease": "C", "Cases": "*"}],
        record_time=record_time,
        record_cases=4,
    )
    json_rows, json_skipped = backfill.recover_source_rows(
        json.dumps([{"Disease": "D", "Date": "2025-02-01", "Cases": "5"}]),
        record_time=record_time,
        record_cases=6,
    )

    assert dict_rows == [{"Disease": "A", "Date": record_time, "Cases": 3}]
    assert dict_skipped == 0
    assert list_rows == [
        {"Disease": "B", "Date": record_time, "Cases": 4},
        {"Disease": "C", "Cases": "*", "Date": record_time},
    ]
    assert list_skipped == 1
    assert json_rows == [
        {"Disease": "D", "Date": "2025-02-01", "Cases": "5"}
    ]
    assert json_skipped == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_run_reports_counts_and_only_writes_with_apply(
    apply: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = object()
    record_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    legacy_records = [
        {
            "record_time": record_time,
            "record_cases": 10,
            "raw_data": {"Disease": "mapped-one"},
        },
        {
            "record_time": record_time,
            "record_cases": 20,
            "raw_data": [
                {"Date": "2025-01-01", "Disease": "mapped-two", "Cases": "2"},
                42,
                {"Date": "2025-01-01", "Disease": "unmatched", "Cases": "3"},
            ],
        },
        {
            "record_time": record_time,
            "record_cases": 30,
            "raw_data": json.dumps(
                {"Date": "2025-01-01", "Disease": "invalid", "Cases": ""}
            ),
        },
        {
            "record_time": record_time,
            "record_cases": 40,
            "raw_data": "not-json",
        },
    ]
    build_calls: list[dict[str, object]] = []
    save_calls: list[dict[str, object]] = []

    async def fake_iter_legacy_batches(
        received_db,
        country_code,
        batch_size,
    ):
        assert received_db is db
        assert country_code == "HK"
        assert batch_size == 2
        yield legacy_records

    class FakeStore:
        def build_observations(self, rows, country_code, **kwargs):
            build_calls.append(
                {"rows": rows, "country_code": country_code, **kwargs}
            )
            return SimpleNamespace(
                observations=[
                    {"series_code": "SER_HK_ONE"},
                    {"series_code": "SER_HK_TWO"},
                ],
                skipped_unmatched=1,
                skipped_ambiguous=0,
                skipped_invalid=1,
            )

        async def save_rows(self, received_db, rows, country_code, **kwargs):
            save_calls.append(
                {
                    "db": received_db,
                    "rows": rows,
                    "country_code": country_code,
                    **kwargs,
                }
            )
            return SimpleNamespace(upserted=1, skipped_registry_not_synced=1)

    class FakeDBContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(backfill, "iter_legacy_batches", fake_iter_legacy_batches)
    monkeypatch.setattr(backfill, "SeriesObservationStore", FakeStore)
    monkeypatch.setattr(backfill, "get_db", lambda: FakeDBContext())
    args = argparse.Namespace(
        country="hk",
        source_id="SRC_HK_CHP",
        batch_size=2,
        apply=apply,
    )

    summary = await backfill.run(args)

    assert summary["mode"] == ("apply" if apply else "dry_run")
    assert summary["geography_key"] == "country:HK:national"
    assert summary["scanned"] == 4
    assert summary["raw_items_scanned"] == 6
    assert summary["source_rows_recovered"] == 4
    assert summary["mappable"] == 2
    assert summary["skipped"] == 4
    assert summary["written"] == int(apply)
    assert summary["skip_breakdown"] == {
        "raw_data_invalid": 2,
        "unmatched": 1,
        "ambiguous": 0,
        "invalid_observation": 1,
        "duplicate_observation": 0,
    }
    assert summary["write_skip_breakdown"] == {
        "registry_not_synced": int(apply)
    }
    assert len(build_calls) == 1
    assert build_calls[0]["country_code"] == "HK"
    assert build_calls[0]["source_id"] == "SRC_HK_CHP"
    assert build_calls[0]["geography_key"] == "country:HK:national"
    assert build_calls[0]["rows"][0] == {
        "Disease": "mapped-one",
        "Date": record_time,
        "Cases": 10,
    }
    assert len(save_calls) == int(apply)
    if apply:
        assert save_calls[0]["db"] is db
        assert save_calls[0]["geography_key"] == "country:HK:national"
