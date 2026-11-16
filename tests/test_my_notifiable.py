"""MOH annual notifications retain all source categories and national grain."""
import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests

from src.data.crawlers.my_notifiable import (
    MYNotifiableContractError,
    parse_moh_annual_payload,
    parse_moh_annual_records,
)
from src.data.processors.my_notifiable import MYAnnualUpdater
from src.services.ingestion.countries.my_notifiable import organize

FIXTURE = Path(__file__).parent / "fixtures/ingestion/MY/std_state.parquet"


def raw_record(**changes):
    return {"date": "2024-01-01", "state": "Malaysia", "disease": "syphillis", "cases": 6, "incidence": 0.02, **changes}


def test_real_release_covers_all_five_categories_without_adding_states():
    rows = parse_moh_annual_payload(FIXTURE.read_bytes(), retrieved_at="2026-11-15T14:00:00Z")
    assert len(rows) == 40
    assert {row["SourceDiseaseCode"] for row in rows} == {"aids", "chancroid", "gonorrhea", "hiv", "syphillis"}
    assert {row["Date"] for row in rows} == {f"{year}-01-01" for year in range(2017, 2025)}
    assert sum(int(row["Cases"]) for row in rows if row["SourceDiseaseCode"] == "hiv") == 25692
    assert next(row for row in rows if row["Date"] == "2022-01-01" and row["SourceDiseaseCode"] == "chancroid")["Cases"] == "0"
    assert all(row["Frequency"] == "annual" and row["GeographyKey"] == "country:MY:national" for row in rows)


def test_new_upstream_disease_categories_are_retained_without_whitelist():
    row = parse_moh_annual_records([raw_record(disease="new_notification_category")])[0]
    assert row["SourceDiseaseCode"] == "new_notification_category"
    assert row["DiseaseCode"] == "__source_native__"


def test_missing_count_is_unknown_and_rate_never_becomes_cases():
    row = parse_moh_annual_records([raw_record(cases=None, incidence=123.45)])[0]
    assert row["Cases"] == ""
    assert row["MissingValueStatus"] == "source_missing"
    assert row["SourceIncidencePer100000"] == "123.45"


def test_nullable_parquet_integer_coercion_keeps_observed_values():
    import io
    import pandas as pd

    frame = pd.DataFrame([raw_record(cases=3), raw_record(cases=None, disease="hiv")])
    stream = io.BytesIO()
    frame.to_parquet(stream)
    rows = parse_moh_annual_payload(stream.getvalue())
    assert {row["SourceDiseaseCode"]: row["Cases"] for row in rows} == {"hiv": "", "syphillis": "3"}


@pytest.mark.parametrize("changes", [{"cases": -1}, {"cases": 1.5}, {"cases": True}, {"date": "2024-02-01"}, {"date": "bad"}])
def test_invalid_source_contract_fails(changes):
    with pytest.raises(MYNotifiableContractError):
        parse_moh_annual_records([raw_record(**changes)])


def test_duplicate_national_cells_and_state_only_source_fail():
    with pytest.raises(MYNotifiableContractError, match="Duplicate"):
        parse_moh_annual_records([raw_record(), raw_record()])
    with pytest.raises(MYNotifiableContractError, match="No explicit national"):
        parse_moh_annual_records([raw_record(state="Johor")])


def test_state_and_national_records_are_not_double_counted():
    rows = parse_moh_annual_records([raw_record(), raw_record(state="Johor", cases=99)])
    assert len(rows) == 1 and rows[0]["Cases"] == "6"
    assert rows[0]["SourceDiseaseCode"] == "syphillis"  # preserve upstream spelling


def test_annual_refresh_keeps_history_despite_month_planning_window():
    rows = parse_moh_annual_payload(FIXTURE.read_bytes())
    result = MYAnnualUpdater().prepare_rows(rows, months=[(2026, 11)])
    assert len(result.rows) == 40 and result.source_latest_date == date(2024, 1, 1)
    with pytest.raises(ValueError, match="after.*coverage"):
        MYAnnualUpdater().prepare_rows(rows, start_year=2025)


def test_refresh_returns_revised_old_year_and_new_closed_year_categories():
    # Refreshing must not use a latest-date append-only gate: an old count
    # changes and a category not in the initial source list arrives together.
    rows = parse_moh_annual_records([
        raw_record(date="2017-01-01", cases=9),
        raw_record(date="2025-01-01", disease="new_notification_category", cases=2),
    ])
    fetched = MYAnnualUpdater().prepare_rows(rows, months=[(2026, 11)])
    assert {(row["Date"], row["SourceDiseaseCode"], row["Cases"]) for row in fetched.rows} == {
        ("2017-01-01", "syphillis", "9"),
        ("2025-01-01", "new_notification_category", "2"),
    }


def test_organize_replays_real_artifact_without_source_access(tmp_path, monkeypatch):
    payload = FIXTURE.read_bytes()
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/my/replay/std.parquet")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    artifact = SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind="moh_notifiable_parquet", metadata_={"retrieved_at": "2026-11-15T14:00:00Z"})
    monkeypatch.setattr(requests.Session, "request", lambda *args, **kwargs: pytest.fail("organize accessed network"))
    result = organize([artifact], updater=MYAnnualUpdater(), source="moh_std_annual")
    assert len(result.rows) == 40


async def test_database_latest_date_is_restricted_to_this_source():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar=lambda: date(2024, 1, 1))))
    assert await MYAnnualUpdater().get_db_latest_date(db) == date(2024, 1, 1)
    query, params = db.execute.call_args.args
    assert "source_system=:source_id" in str(query)
    assert params == {"code": "MY", "source_id": "SRC_MY_MOH_STD"}
