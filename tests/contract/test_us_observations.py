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
    actual = organize(saved, updater=USWeeklyUpdater(country_code=country), source="all")
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
