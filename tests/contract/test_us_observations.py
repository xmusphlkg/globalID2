"""US source contract: the same captured bytes preserve every normalized fact."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.us import USNNDSSCrawler, USNHSSHIVCrawler
from src.data.processors.us import USWeeklyUpdater
from src.core.country_library import SUBDIVISION_REGISTRY
from src.services.ingestion.countries.us import organize

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/US"
RESOURCE = {"id": "fixture", "name": "archive", "columns_name": ["Hep A, Current week"], "columns_field_name": ["measles"]}


@pytest.mark.parametrize("area", ["US RESIDENTS", "TEXAS"])
def test_archive_keeps_both_measles_cells_with_original_flags(area):
    resource = {
        "id": "7xhe-mv2e",
        "columns_name": ["Measles§, Imported, Current week", "Measles§, Imported, Current week, flag",
                         "Measles§, Indigenous, Current week", "Measles§, Indigenous, Current week, flag",
                         "Malaria, Current week"],
        "columns_field_name": ["imported", "flag", "indigenous", "indigenous_flag", "malaria"],
    }
    row = {"reporting_area": area, "mmwr_year": "2020", "mmwr_week": "53",
           "indigenous": "2", "flag": "-"}
    archive = USNNDSSCrawler.parse_archive_table(
        resource, [row, {**row, "reporting_area": "TOTAL"}], 2020)
    assert [(r["Label"], r["Current week"], r.get("Current week, flag")) for r in archive] == [
        ("Measles§, Imported", None, "-"), ("Measles§, Indigenous", "2", None),
    ]
    import csv
    with (FIXTURES / "current.csv").open() as source:
        fetched = USWeeklyUpdater().organize_sources(
            "nndss_api", nndss=([*csv.DictReader(source), *archive], "fixture"))
    assert all(not r["ArchiveTableID"] for r in fetched.rows)
    historical = [r for r in fetched.series_rows if r["ArchiveTableID"]]
    assert [(r["Date"], r["Cases"], r["CurrentWeekFlag"]) for r in historical] == [
        ("2021-01-02", "", "-"), ("2021-01-02", "2", ""),
    ]


def artifacts(tmp_path):
    # Match real artifact confinement while running in a disposable workspace.
    result = []
    directory = Path("data/raw/us/contract")
    directory.mkdir(parents=True)
    for filename, kind, metadata in [("current.csv", "csv_page", {}), ("archive.json", "archive_json", {"year": 2020, "resource": RESOURCE}), ("nhss_2024.xlsx", "nhss_xlsx", {})]:
        payload = (FIXTURES / filename).read_bytes()
        path = directory / filename
        path.write_bytes(payload)
        result.append(SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind=kind, metadata_=metadata, source_url="https://www.cdc.gov/nhss_2024.xlsx"))
    return result


@pytest.mark.parametrize("country", ["US"] + [code for code, row in SUBDIVISION_REGISTRY.items() if row.get("parent_country_code") == "US"])
def test_offline_us_contract_preserves_legacy_and_series(tmp_path, monkeypatch, country):
    monkeypatch.chdir(tmp_path)
    saved = artifacts(tmp_path)
    # Baseline uses the original public fetch/normalize entry point on fixture bytes.
    import csv
    import io
    from src.data.crawlers.us import DEFAULT_CSV_API_URL
    raw = list(csv.DictReader(io.StringIO((FIXTURES / "current.csv").read_text())))
    archived = USNNDSSCrawler.parse_archive_table(RESOURCE, json.loads((FIXTURES / "archive.json").read_text()), 2020)
    assert len(archived) == 1 and archived[0]["Reporting Area"] == "CALIFORNIA"
    ref = DEFAULT_CSV_API_URL + " | https://data.cdc.gov/d/fixture"
    monkeypatch.setattr(USNNDSSCrawler, "fetch_raw_pages", lambda self: (raw + archived, ref))
    monkeypatch.setattr(USNHSSHIVCrawler, "fetch_national_annual_rows", lambda self: (self.parse_current_workbook((FIXTURES / "nhss_2024.xlsx").read_bytes(), source_url=saved[-1].source_url), saved[-1].source_url))
    expected = USWeeklyUpdater(country_code=country).fetch_latest("all")
    monkeypatch.setattr(USNNDSSCrawler, "get", lambda *a, **kw: pytest.fail("offline parser requested a source"))
    actual = organize(saved, updater=USWeeklyUpdater(country_code=country), source="all", archive_years=(2020,))
    assert actual.rows == expected.rows
    assert actual.series_rows == expected.series_rows
    from src.data.storage.series_observation_store import SeriesObservationStore
    from tests.contract.compare import assert_observations_match
    built = SeriesObservationStore().build_observations(actual.series_rows, "US", source_id={"US CDC NNDSS": "SRC_US_NNDSS", "US CDC NHSS": "SRC_US_NHSS"})
    golden = json.loads((FIXTURES / "observations.json").read_text())
    if country != "US":
        golden = [row for row in golden if row["geography_key"] == f"country:{country}:national"]
    assert built.observations
    assert_observations_match(built.observations, golden)
    if country == "US":
        assert any(r["MissingValueStatus"] == "source_missing" for r in actual.series_rows if r["Source"] == "US CDC NNDSS")
        assert {r["ReportingArea"] for r in actual.series_rows} >= {"US RESIDENTS", "TOTAL", "CALIFORNIA"}


@pytest.mark.parametrize("error", [RuntimeError("missing workbook"), ValueError("invalid source")])
async def test_collect_failure_propagates_before_organization(error, monkeypatch):
    from src.services.ingestion.countries.us import collect
    monkeypatch.setattr(USNHSSHIVCrawler, "discover_current_workbook_url", lambda self: (_ for _ in ()).throw(error))
    with pytest.raises(type(error), match=str(error)):
        await collect(SimpleNamespace(artifacts=[]), source="nhss_hiv")


def test_artifact_tampering_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = artifacts(tmp_path)
    saved[0].sha256 = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        organize(saved, updater=USWeeklyUpdater(), source="all")


def test_partial_measles_table_cannot_reach_store(monkeypatch):
    resource = {
        "id": "9k8a-cbgx",
        "columns_name": ["Measles§, Imported, Current week", "Measles§, Imported, Current week, flag",
                         "Measles§, Indigenous, Current week", "Measles§, Indigenous, Current week, flag"],
        "columns_field_name": ["imp", "imp_flag", "ind", "ind_flag"],
    }
    artifact = SimpleNamespace(kind="archive_json", metadata_={
        "year": 2021, "resource": resource, "measles_cells_preserved": True, "measles_only": True,
    })
    import src.services.ingestion.countries.us as connector
    payload = b'[{"reporting_area":"TEXAS","mmwr_year":"2021","mmwr_week":"1","imp_flag":"-","ind_flag":"U"}]'
    monkeypatch.setattr(connector, "read_artifact", lambda _: payload)
    with pytest.raises(ValueError, match="Incomplete CDC measles archive for 2021"):
        organize([artifact], updater=USWeeklyUpdater(), source="nndss_api",
                 archive_years=(2021,), measles_only_years=(2021,))
