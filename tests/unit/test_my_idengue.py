"""Daily dengue observations must never be inferred from cumulative totals."""
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from bs4 import BeautifulSoup

from src.data.crawlers.my_dengue import IDengueContractError, parse_idengue_html
from src.data.processors.my_dengue import MalaysiaIDengueDailyUpdater
from src.services.ingestion.countries.my_dengue import organize

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/MY/idengue_20261115.html"
CAPTURED_AT = "2026-11-16T00:00:00+00:00"


def parsed(payload=None):
    return parse_idengue_html(payload or FIXTURE.read_bytes(), retrieved_at=CAPTURED_AT)


def test_real_malformed_official_html_retains_all_geographies_and_explicit_zeroes():
    result = parsed()
    assert len(result.rows) == 16
    national = next(row for row in result.rows if row["GeographyKey"] == "country:MY:national")
    assert national["Cases"] == "193"
    assert result.source_date == date(2026, 11, 15)
    assert len({row["GeographyKey"] for row in result.rows}) == 16
    assert sum(row["Cases"] == "0" for row in result.rows) == 3
    assert sum(int(row["Cases"]) for row in result.rows if row is not national) == 193
    assert {row["Date"] for row in result.rows} == {"2026-11-15"}
    assert {row["Measure"] for row in result.rows} == {"cases"}
    assert {row["Frequency"] for row in result.rows} == {"daily"}


def test_cumulative_cases_and_deaths_remain_audit_information_not_daily_observations():
    result = parsed()
    national = result.rows[0]
    assert national["Cases"] != national["ExcludedCumulativeCaseText"]
    assert "31 DEC 2023" in national["CumulativeCaseHeading"]
    assert result.source_audit["cumulative_cases_excluded"] is True
    assert result.source_audit["cumulative_deaths"] == {
        "status": "excluded_cumulative_not_daily", "start": "2026-01-01", "end": "2026-11-15", "value": 68,
    }
    assert not any("Deaths" in row or row["Measure"] == "deaths" for row in result.rows)


def test_source_federal_reporting_region_is_not_split_or_relabelled_as_a_state():
    result = parsed()
    federal = next(row for row in result.rows if row["SourceGeographyLabel"] == "WILAYAH PERSEKUTUAN")
    assert federal["Cases"] == "26"
    assert federal["GeographyKey"] == "country:MY:source_region:wilayah_persekutuan"
    assert json.loads(federal["Dimensions"])["source_geography_code"] == "wilayah_persekutuan"
    assert not any("Kuala Lumpur" in row["Geography"] or "Putrajaya" in row["Geography"] for row in result.rows)
    updater = MalaysiaIDengueDailyUpdater()
    assert updater.series_geography_from_rows is True
    assert updater.series_geography_key is None
    assert updater.mapping_notifications_enabled is False
    assert updater.public_release_enabled is False


@pytest.mark.parametrize("mutation", ["missing_region", "duplicate_region", "new_region", "bad_count", "bad_width", "national_missing", "national_mismatch", "daily_heading", "cumulative_end", "attribution"])
def test_incomplete_or_changed_contract_fails_closed(mutation):
    soup = BeautifulSoup(FIXTURE.read_bytes(), "lxml")
    table = soup.find("table")
    region = next(tr for tr in table.find_all("tr") if tr.find("td") is not None)
    if mutation == "missing_region":
        region.decompose()
    elif mutation == "duplicate_region":
        region.insert_after(BeautifulSoup(str(region), "lxml").find("tr"))
    elif mutation == "new_region":
        region.find("td").string = "UNREVIEWED REGION"
    elif mutation == "bad_count":
        region.find_all("td", recursive=False)[1].string = "—"
    elif mutation == "bad_width":
        region.find_all("td", recursive=False)[2].decompose()
    elif mutation == "national_missing":
        soup.find_all("table")[1].decompose()
    elif mutation == "national_mismatch":
        soup.find_all("table")[1].find_all("td")[1].string = "194"
    elif mutation == "daily_heading":
        table.find_all("th")[1].string = "KES TERKUMPUL PADA 15 Nov 2026"
    elif mutation == "cumulative_end":
        table.find_all("th")[2].string = "JUMLAH KES TERKUMPUL DARI 31 DEC 2023 HINGGA 14 Nov 2026"
    else:
        soup = BeautifulSoup(str(soup).replace("CPRC", "REMOVED"), "lxml")
    with pytest.raises(IDengueContractError):
        parsed(str(soup).encode())


def test_future_daily_date_and_naive_retrieval_time_are_rejected():
    with pytest.raises(IDengueContractError, match="future"):
        parse_idengue_html(FIXTURE.read_bytes(), retrieved_at="2026-11-14T00:00:00+00:00")
    with pytest.raises(IDengueContractError, match="timezone"):
        parse_idengue_html(FIXTURE.read_bytes(), retrieved_at="2026-11-16T00:00:00")


def test_current_snapshot_is_not_lost_by_month_windows_or_misrepresented_as_history():
    updater = MalaysiaIDengueDailyUpdater()
    result = updater.prepare_rows(parsed().rows, source=updater.source_scope, start_year=2010, months=[(2010, 1)])
    assert len(result.rows) == 16
    assert result.source_latest_date == date(2026, 11, 15)
    assert "historical daily backfill not verified" in " ".join(result.script_logs)
    with pytest.raises(ValueError, match="after"):
        updater.prepare_rows(parsed().rows, source=updater.source_scope, start_year=2027)


def test_offline_replay_uses_captured_timestamp_and_hash_verified_original_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/my/idengue.html")
    path.parent.mkdir(parents=True)
    raw = FIXTURE.read_bytes()
    path.write_bytes(raw)
    artifact = SimpleNamespace(kind="my_idengue_html", relative_path=str(path),
        byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), metadata_={"retrieved_at": CAPTURED_AT})
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: pytest.fail("offline organize made HTTP request"))
    result = organize([artifact], updater=MalaysiaIDengueDailyUpdater())
    assert result.rows == parsed().rows
    assert result.source_audit["national_daily_count"] == 193
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        organize([artifact], updater=MalaysiaIDengueDailyUpdater())


def test_offline_replay_requires_exactly_one_release_and_captured_time(tmp_path, monkeypatch):
    updater = MalaysiaIDengueDailyUpdater()
    with pytest.raises(ValueError, match="Exactly one"):
        organize([], updater=updater)
    artifact = SimpleNamespace(kind="my_idengue_html", metadata_={})
    with pytest.raises(ValueError, match="retrieval time"):
        organize([artifact], updater=updater)
