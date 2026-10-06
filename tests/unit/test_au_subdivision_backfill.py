import json
from datetime import date

import pytest

from scripts.fetch_au_subdivision_history import (
    decode_year_rows,
    write_monthly_archives,
)
from scripts.update_au_subdivisions import parse_state_count, restore_state_rows


def test_source_suppression_is_missing_not_zero():
    assert parse_state_count("'<5'") is None
    assert parse_state_count(None) is None
    assert parse_state_count("0L") == 0
    assert parse_state_count("'1,234'") == 1234
    for value in [-1, 1.5, True, "not available", "<invalid"]:
        with pytest.raises(ValueError):
            parse_state_count(value)


def test_restore_preserves_explicit_state_zero_without_splitting_national_total(
    tmp_path,
):
    folder = tmp_path / "2025/01"
    folder.mkdir(parents=True)
    (folder / "Dengue.json").write_text(
        json.dumps(
            {
                "disease": "Dengue",
                "parsed_counts": {"NSW": 12, "ACT": 0, "VIC": "'<5'", "AUS": 999},
            }
        )
    )
    rows, files = restore_state_rows(tmp_path, 2025, 2025)
    assert files == 1
    assert rows["AU-NSW"][0]["Cases"] == "12"
    assert rows["AU-ACT"][0]["Cases"] == "0"
    assert rows["AU-VIC"][0]["Cases"] == "<5"
    assert rows["AU-NSW"][0]["JurisdictionCode"] == "AU-NSW"
    assert rows["AU-NSW"][0]["ParentCountryCode"] == "AU"


def test_year_decoder_ignores_subtotals_and_handles_compression():
    raw = {
        "results": [
            {
                "result": {
                    "data": {
                        "descriptor": {
                            "Select": [
                                {"Name": name, "Value": code}
                                for name, code in zip(
                                    ["year", "month", "state", "disease", "cases"],
                                    ["G0", "G1", "G2", "G3", "M0"],
                                )
                            ]
                        },
                        "dsr": {
                            "DS": [
                                {
                                    "PH": [
                                        {"DM0": [{"S": [{"N": "A0"}], "A0": "'999'"}]},
                                        {
                                            "DM1": [
                                                {
                                                    "S": [
                                                        {"N": code}
                                                        for code in [
                                                            "G0",
                                                            "G1",
                                                            "G2",
                                                            "G3",
                                                            "M0",
                                                        ]
                                                    ],
                                                    "C": [
                                                        "2025",
                                                        "January",
                                                        "NSW",
                                                        "Dengue",
                                                        "'<5'",
                                                    ],
                                                },
                                                {"R": 11, "C": ["ACT", "0L"]},
                                            ]
                                        },
                                    ],
                                }
                            ]
                        },
                    }
                }
            }
        ]
    }
    rows = decode_year_rows(raw, 2025)
    assert len(rows) == 2
    assert rows[0]["cases"] is None
    assert rows[1]["state"] == "AU-ACT" and rows[1]["cases"] == 0
    assert "DM0" in raw["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]
    raw["results"][0]["result"]["data"]["dsr"]["DS"][0]["RT"] = ["next-page"]
    with pytest.raises(ValueError, match="truncated"):
        decode_year_rows(raw, 2025)


def test_open_month_not_imported_and_suppressed_value_preserved(tmp_path):
    rows = [
        {
            "year": 2026,
            "month": month,
            "state": "AU-NSW",
            "disease": "Dengue",
            "cases": None,
            "source_value": "'<5'",
        }
        for month in [9, 10]
    ]
    assert write_monthly_archives(rows, tmp_path, date(2026, 10, 6)) == 1
    payload = json.loads((tmp_path / "2026/09/Dengue.json").read_text())
    assert payload["parsed_counts"]["AU-NSW"] == "'<5'"
    assert not (tmp_path / "2026/10").exists()


def test_au_csv_retains_suppressed_rows(tmp_path):
    from src.data.processors.au import AUMonthlyUpdater

    path = tmp_path / "au.csv"
    path.write_text(
        "Disease,Date,Cases,JurisdictionCode,ParentCountryCode,LocationType,Geocode,GeographyKey,ReportingArea\n"
        "Dengue,2025-01-01,<5,AU-NSW,AU,subdivision,AU-NSW,country:AU-NSW:national,New South Wales\n"
    )
    rows = AUMonthlyUpdater(country_code="AU-NSW")._load_rows(path)
    assert len(rows) == 1
    assert rows[0]["Cases"] == "<5"
    updater = AUMonthlyUpdater(country_code="AU-NSW", output_csv=path)
    updater._write_rows_to_output_csv(rows)
    assert updater._load_rows(path)[0]["Cases"] == "<5"


@pytest.mark.asyncio
async def test_au_legacy_import_writes_suppressed_count_as_null(monkeypatch):
    from src.data.processors.au import AUMonthlyUpdater

    updater = AUMonthlyUpdater(country_code="AU-NSW")

    async def country_id(_db):
        return 7

    async def mapping(_db):
        return {"dengue": 21}

    async def latest(_db):
        return date(2025, 1, 1)

    monkeypatch.setattr(updater, "_get_country_id", country_id)
    monkeypatch.setattr(updater, "_load_mapping_dict", mapping)
    monkeypatch.setattr(updater, "get_db_latest_date", latest)

    class Database:
        parameters = None

        async def execute(self, statement, parameters):
            self.parameters = parameters

    db = Database()
    row = updater._normalized_output_row(
        year=2025, month=1, disease="Dengue", cases=0, source_file="official"
    )
    row["Cases"] = "<5"
    result = await updater.import_rows(
        db, [row], db_latest_date=None, source_latest_date=date(2025, 1, 1)
    )
    assert result.inserted_or_updated == 1
    assert db.parameters[0]["cases"] is None
    assert json.loads(db.parameters[0]["metadata"])["source_value_suppressed"] is True
