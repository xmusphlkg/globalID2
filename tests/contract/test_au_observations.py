"""AU national source semantics are identical for staged and original parsers."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.au import AustraliaNINDSSCrawler, _parse_dm0_to_state_counts
from src.data.processors.au import AUMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.au import organize
from tests.contract.compare import assert_observations_match
from tests.contract.monthly_helpers import assert_golden

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/AU/location.json"


@pytest.mark.parametrize("country_code,expected_cases", [("AU", 39), ("AU-NSW", 12), ("AU-ACT", 0), ("AU-VIC", 2), ("AU-QLD", 3), ("AU-SA", 4), ("AU-WA", 5), ("AU-TAS", 6), ("AU-NT", 7)])
def test_au_national_observation_contract(tmp_path, monkeypatch, country_code, expected_cases):
    raw = (FIXTURE.parents[1] / country_code / "location.json").read_bytes()
    dm0, payload = AustraliaNINDSSCrawler.parse_payload_bytes(raw)
    counts = _parse_dm0_to_state_counts(dm0, payload, "2026")
    monkeypatch.setattr(AustraliaNINDSSCrawler, "_load_config", lambda self: True)
    monkeypatch.setattr(AustraliaNINDSSCrawler, "get_all_diseases", lambda self: ["Hepatitis (NEC)"])
    monkeypatch.setattr(AustraliaNINDSSCrawler, "_fetch_month_disease", lambda self, *args: counts)
    monkeypatch.setattr(AUMonthlyUpdater, "_default_raw_archive_root", staticmethod(lambda code: tmp_path / "empty-archive"))
    updater = AUMonthlyUpdater(country_code=country_code, output_csv=tmp_path / "source.csv")
    expected = updater.refresh_source(months=[(2026, 3)])
    monkeypatch.chdir(tmp_path)
    path = Path("data/raw/au/fixture/location.json")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    artifact = SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), kind="powerbi_json", metadata_={"year": 2026, "month": 3, "disease": "Hepatitis (NEC)"})
    monkeypatch.setattr(AustraliaNINDSSCrawler, "_load_config", lambda self: pytest.fail("offline organization requested source"))
    actual = organize([artifact], updater=updater, source="au", months=[(2026, 3)])
    assert actual.rows == expected.rows
    assert actual.rows[0]["Cases"] == str(expected_cases)
    store = SeriesObservationStore()
    expected_obs = store.build_observations(expected.rows, "AU", source_id="SRC_AU_NINDSS")
    actual_obs = store.build_observations(actual.rows, "AU", source_id="SRC_AU_NINDSS")
    assert expected_obs.observations
    assert_observations_match(actual_obs.observations, expected_obs.observations)
    assert_golden(expected.rows, expected_obs.observations, fixture_code=country_code)
