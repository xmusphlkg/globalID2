from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backfill_br_ntra_history as backfill


def _args(output: Path, *, apply: bool) -> argparse.Namespace:
    return argparse.Namespace(
        start_year=2019,
        end_year=2019,
        output=output,
        apply=apply,
    )


def test_month_range_is_bounded_and_current_year_stops_at_current_month() -> None:
    assert backfill.months_for_year_range(
        2025,
        2026,
        today=date(2026, 8, 4),
    ) == [
        *((2025, month) for month in range(1, 13)),
        *((2026, month) for month in range(1, 9)),
    ]

    with pytest.raises(ValueError, match="future"):
        backfill.months_for_year_range(2026, 2027, today=date(2026, 8, 4))


@pytest.mark.asyncio
@pytest.mark.parametrize("apply", [False, True])
async def test_run_is_ntra_only_and_db_writes_require_apply(
    apply: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "br_ntra_history.csv"
    db = object()
    refresh_calls: list[dict[str, object]] = []
    import_calls: list[dict[str, object]] = []
    build_calls: list[dict[str, object]] = []
    save_calls: list[dict[str, object]] = []
    crawler = object()
    source_rows = [
        {
            "Date": "2019-01-01",
            "DiseaseCode": "NTRA",
            "Cases": "5",
            "PersonsExamined": "150",
            "DatasetStatus": "final",
            "SourceFile": "NTRABR19.dbc",
            "SourceURL": "ftp://example/NTRABR19.dbc",
        },
        {
            "Date": "2019-02-01",
            "DiseaseCode": "NTRA",
            "Cases": "9",
            "PersonsExamined": "20",
            "DatasetStatus": "final",
            "SourceFile": "NTRABR19.dbc",
            "SourceURL": "ftp://example/NTRABR19.dbc",
        },
        {
            "Date": "2019-03-01",
            "DiseaseCode": "DENG",
            "Cases": "99",
            "PersonsExamined": "99",
        },
        {
            "Date": "2019-04-01",
            "DiseaseCode": "NTRA",
            "Cases": "-1",
            "PersonsExamined": "10",
        },
    ]

    class FakeUpdater:
        def __init__(self, *, output_csv):
            assert output_csv == output.resolve()

        def refresh_source(self, **kwargs):
            refresh_calls.append(kwargs)
            return SimpleNamespace(rows=source_rows)

        async def import_rows(self, received_db, rows, **kwargs):
            import_calls.append({"db": received_db, "rows": rows, **kwargs})
            return SimpleNamespace(inserted_or_updated=2, skipped_unmapped=0)

    class FakeStore:
        def build_observations(self, rows, country_code, **kwargs):
            build_calls.append(
                {"rows": rows, "country_code": country_code, **kwargs}
            )
            return SimpleNamespace(
                observations=[
                    {"series_code": "SER_BR_TRACHOMA_SURVEY_POSITIVE_NTRA"},
                    {"series_code": "SER_BR_TRACHOMA_SURVEY_POSITIVE_NTRA"},
                ],
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
            return SimpleNamespace(upserted=2, skipped_registry_not_synced=0)

    class FakeDBContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(backfill, "BrazilSINANCrawler", lambda **kwargs: crawler)
    monkeypatch.setattr(backfill, "BRMonthlyUpdater", FakeUpdater)
    monkeypatch.setattr(backfill, "SeriesObservationStore", FakeStore)
    if apply:
        monkeypatch.setattr(backfill, "get_db", lambda: FakeDBContext())
    else:
        monkeypatch.setattr(
            backfill,
            "get_db",
            lambda: (_ for _ in ()).throw(AssertionError("dry-run touched DB")),
        )

    summary = await backfill.run(_args(output, apply=apply))

    assert output.exists()
    with output.open(encoding="utf-8", newline="") as handle:
        written_rows = list(csv.DictReader(handle))
    assert [row["DiseaseCode"] for row in written_rows] == ["NTRA", "NTRA"]
    assert summary["mode"] == ("apply" if apply else "dry_run")
    assert summary["positive_cases"] == 14
    assert summary["persons_examined"] == 170
    assert summary["legacy_upserts"] == (2 if apply else 0)
    assert summary["series_upserts"] == (2 if apply else 0)
    assert summary["skip_counts"]["non_ntra"] == 1
    assert summary["skip_counts"]["invalid_row"] == 1
    assert refresh_calls[0]["source"] == "NTRA"
    assert refresh_calls[0]["load_csv_fallback"] is False
    assert refresh_calls[0]["write_csv"] is False
    assert refresh_calls[0]["crawler"] is crawler
    assert len(build_calls) == 1
    assert build_calls[0]["country_code"] == "BR"
    assert build_calls[0]["source_id"] == "SRC_BR_SINAN"
    assert build_calls[0]["geography_key"] == "country:BR:national"
    assert len(save_calls) == int(apply)
    assert len(import_calls) == int(apply)
    if apply:
        assert save_calls[0]["db"] is db
        assert save_calls[0]["source_id"] == "SRC_BR_SINAN"
        assert import_calls[0]["force"] is True


@pytest.mark.asyncio
async def test_run_refuses_to_overwrite_full_br_current_csv() -> None:
    args = _args(Path(backfill.DEFAULT_OUTPUT_CSV), apply=False)

    with pytest.raises(ValueError, match="full BR current CSV"):
        await backfill.run(args)
