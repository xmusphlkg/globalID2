"""Every enabled Japanese jurisdiction retains its observation contract."""
import csv
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.country_library import SUBDIVISION_REGISTRY
from src.data.crawlers.jp import JapanIDWRCrawler
from src.data.processors.jp import JPWeeklyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.jp import organize
from tests.contract.compare import assert_observations_match

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/JP/weekly.csv"
COUNTRIES = ["JP"] + [code for code, row in SUBDIVISION_REGISTRY.items() if row.get("parent_country_code") == "JP"]


@pytest.mark.parametrize("country", COUNTRIES)
def test_jp_native_and_legacy_contract(country, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "weekly_cases_standardized.csv"
    old = 'Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week,"Current week, flag"\n総数,2024,1,AIDS,0,\n' 
    output.write_text(old)
    url = "https://example.com/2025/12/zensu202512.csv"
    payload = FIXTURE.read_bytes()
    monkeypatch.setattr(JapanIDWRCrawler, "_discover_weekly_csv_urls", lambda *a, **kw: ([url], [url], []))
    monkeypatch.setattr(JapanIDWRCrawler, "get", lambda self, url: SimpleNamespace(content=payload))
    expected = JPWeeklyUpdater(country_code=country, output_csv=output).refresh_source(source="jp_weekly")
    artifacts = []
    for index, (data, kind, metadata) in enumerate([(old.encode(), "standardized_cache", {}), (payload, "weekly_csv", {"source_kind": "zensu"})]):
        path = Path(f"data/raw/jp/contract/{index}.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append(SimpleNamespace(relative_path=str(path), sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data), kind=kind, metadata_=metadata, source_url=url))
    monkeypatch.setattr(JapanIDWRCrawler, "get", lambda *a, **kw: pytest.fail("organize requested the network"))
    actual = organize(artifacts, updater=JPWeeklyUpdater(country_code=country, output_csv=output), source="jp_weekly", output_csv=output)
    assert actual.rows == expected.rows
    assert actual.rows
    store = SeriesObservationStore()
    old_observations = store.build_observations(expected.rows, "JP", source_id="SRC_JP_NIID").observations
    new_observations = store.build_observations(actual.rows, "JP", source_id="SRC_JP_NIID").observations
    assert new_observations
    assert_observations_match(new_observations, old_observations)
    if country == "JP":
        assert any(row["SurveillanceType"] == "unspecified" for row in actual.rows)
