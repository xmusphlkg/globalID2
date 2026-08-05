from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from scripts import backfill_disease_series_observations as backfill


def test_parse_args_accepts_custom_value_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_disease_series_observations.py",
            "--country",
            "JP",
            "--value-field",
            "Current week",
        ],
    )

    args = backfill.parse_args()

    assert args.value_field == "Current week"


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_run_propagates_value_field_to_dry_run_and_save(
    apply: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    input_path = tmp_path / "jp.csv"
    input_path.write_text(
        "Current MMWR Year,MMWR WEEK,Disease,Current week\n"
        "2025,1,AIDS,2\n",
        encoding="utf-8",
    )
    build_calls: list[dict[str, object]] = []
    save_calls: list[dict[str, object]] = []
    db = object()

    class FakeStore:
        def build_observations(self, rows, country_code, **kwargs):
            build_calls.append(
                {"rows": rows, "country_code": country_code, **kwargs}
            )
            return SimpleNamespace(
                observations=[{"series_code": "SER_JP_AIDS_WEEKLY"}],
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
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
            return SimpleNamespace(upserted=1, skipped_registry_not_synced=0)

    class FakeDBContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(backfill, "SeriesObservationStore", FakeStore)
    monkeypatch.setattr(backfill, "get_db", lambda: FakeDBContext())
    args = argparse.Namespace(
        country="jp",
        input=input_path,
        source_id="SRC_JP_NIID",
        geography_key="country:JP:national",
        value_field="Current week",
        apply=apply,
    )

    summary = await backfill.run(args)

    assert summary["mode"] == ("apply" if apply else "dry_run")
    assert summary["value_field"] == "Current week"
    assert build_calls == [
        {
            "rows": [
                {
                    "Current MMWR Year": "2025",
                    "MMWR WEEK": "1",
                    "Disease": "AIDS",
                    "Current week": "2",
                }
            ],
            "country_code": "JP",
            "source_id": "SRC_JP_NIID",
            "value_field": "Current week",
            "geography_key": "country:JP:national",
        }
    ]
    assert len(save_calls) == int(apply)
    if apply:
        assert save_calls[0]["db"] is db
        assert save_calls[0]["value_field"] == "Current week"
        assert summary["saved"] == {
            "upserted": 1,
            "skipped_registry_not_synced": 0,
        }
