"""KDCA OpenAPI, AJAX and downloaded exports have identical stored facts."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.kr import KoreaKDCAOpenAPICrawler, _extract_items
from src.data.processors.kr import KRMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.kr import organize
from tests.contract.compare import assert_observations_match

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/KR"


@pytest.mark.parametrize("channel", ["openapi", "portal", "download"])
@pytest.mark.parametrize("recover_from_history", [False, True])
def test_kr_source_contract(channel, tmp_path, monkeypatch, recover_from_history):
    monkeypatch.chdir(tmp_path)
    months = [(2025, 1), (2025, 2)]
    output = tmp_path / "monthly.csv"
    original = KoreaKDCAOpenAPICrawler.__init__
    def init(self, *args, **kwargs):
        kwargs["service_key"] = "fixture" if channel == "openapi" else ""
        original(self, *args, **kwargs)
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "__init__", init)
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "_manual_source_paths", lambda *args, **kwargs: [kwargs["source_file"]] if kwargs.get("source_file") else [])
    payload = (FIXTURES / ("openapi.json" if channel == "openapi" else "portal_2025.json")).read_bytes()
    decoded = json.loads(payload)
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "fetch_period_region_monthly", lambda *args, **kwargs: _extract_items(decoded))
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "_fetch_bass_stats_list", lambda *args, **kwargs: decoded["value"])
    options = {}
    if channel == "download":
        manual = tmp_path / "kdca_2025.json"
        manual.write_bytes(payload)
        options["source_file"] = manual
    expected = KRMonthlyUpdater(output_csv=output).refresh_source(months=months, **options)
    path = Path("data/raw/kr/contract/response.json")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    artifact = SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind="download_file" if channel == "download" else "source_json", metadata_={"portal_year": 2025, "filename": "kdca_2025.json"}, source_url="https://example.com/PeriodRegion" if channel == "openapi" else "https://example.com/selectBassDissStatsList")
    artifacts = [artifact]
    if recover_from_history:
        cached = output.read_bytes()
        cache_path = path.with_name("history.csv")
        cache_path.write_bytes(cached)
        artifacts.insert(0, SimpleNamespace(relative_path=str(cache_path), byte_size=len(cached), sha256=hashlib.sha256(cached).hexdigest(), kind="history_csv"))
        def invalid_live(*args, **kwargs):
            raise ValueError("invalid live JSON")
        monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "crawl_monthly_national", invalid_live)
        expected = KRMonthlyUpdater(output_csv=output).refresh_source(months=months, **options)
        invalid = b"invalid live JSON"
        path.write_bytes(invalid)
        artifact.byte_size = len(invalid)
        artifact.sha256 = hashlib.sha256(invalid).hexdigest()
    monkeypatch.setattr(KoreaKDCAOpenAPICrawler, "get", lambda *a, **kw: pytest.fail("offline organization used network"))
    actual = organize(artifacts, updater=KRMonthlyUpdater(output_csv=output), source="all", months=months, history_csv=output)
    assert actual.rows == expected.rows
    store = SeriesObservationStore()
    before = store.build_observations(expected.rows, "KR", source_id="SRC_KR_KDCA").observations
    after = store.build_observations(actual.rows, "KR", source_id="SRC_KR_KDCA").observations
    assert after
    assert_observations_match(after, before)
