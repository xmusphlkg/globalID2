"""All PHF historical DHB jurisdictions retain their annual source facts."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.nz import NewZealandPHFCrawler, DHB_JURISDICTIONS, DHB_TOTAL_CSV_MIRRORS
from src.data.processors.nz import NZDHBAnnualUpdater
from src.services.ingestion.countries.nz_dhb import organize
from tests.contract.native import assert_native_observations_match

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/NZ-DHB/annual.csv"


@pytest.mark.parametrize("country", list(DHB_JURISDICTIONS.values()))
def test_phf_annual_dhb_contract(country, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = FIXTURE.read_bytes()
    output = tmp_path / "dhb.csv"
    url = DHB_TOTAL_CSV_MIRRORS[0]
    monkeypatch.setattr(NewZealandPHFCrawler, "get", lambda *a, **kw: SimpleNamespace(content=payload))
    expected = NZDHBAnnualUpdater(country_code=country, output_csv=output).refresh_source(source="phf_dhb_annual")
    path = Path("data/raw/nz-dhb/contract/annual.csv")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    artifact = SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload), kind="annual_csv", source_url=url)
    monkeypatch.setattr(NewZealandPHFCrawler, "get", lambda *a, **kw: pytest.fail("DHB organization requested source"))
    updater = NZDHBAnnualUpdater(country_code=country, output_csv=output)
    actual = organize([artifact], updater=updater, source="phf_dhb_annual", country_code=country, output_csv=output)
    assert actual.rows == expected.rows == []
    assert actual.series_rows == expected.series_rows
    assert {row["Frequency"] for row in actual.series_rows} == {"annual"}
    assert {row["GeographyKey"] for row in actual.series_rows} == {f"country:{country}:national"}
    assert_native_observations_match(actual.series_rows, expected.series_rows, updater)
