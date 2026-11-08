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


def test_incremental_cache_window_keeps_recent_and_downloaded_history(tmp_path, monkeypatch):
    from src.services.ingestion.countries import jp

    header = 'Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week,"Current week, flag"\n'
    cache = (header + '総数,2024,1,AIDS,1,\n総数,2026,40,AIDS,2,\n総数,2026,41,AIDS,3,\n').encode()
    artifacts = [SimpleNamespace(kind="standardized_cache", metadata_={}, source_url="cache"),
                 SimpleNamespace(kind="weekly_csv", metadata_={"source_kind": "zensu"}, source_url="history")]
    monkeypatch.setattr(jp, "read_artifact", lambda artifact: cache if artifact.kind == "standardized_cache" else b"header\n")
    monkeypatch.setattr(JapanIDWRCrawler, "_normalize_rows", lambda *_args, **_kwargs: [{
        "Reporting Area": "総数", "Current MMWR Year": "2023", "MMWR WEEK": "1",
        "Disease": "AIDS", "Current week": "5",
    }])
    updater = JPWeeklyUpdater(output_csv=tmp_path / "jp.csv")
    result = organize(artifacts, updater=updater, source="jp_weekly", output_csv=updater.output_csv, incremental_cache_weeks=2)
    assert {(row["MMWRYear"], row["MMWRWeek"]) for row in result.rows} == {("2023", "1"), ("2026", "40"), ("2026", "41")}
    assert "skipped 1 older cached rows" in result.script_logs[0]
    full = organize(artifacts, updater=updater, source="jp_weekly", output_csv=updater.output_csv, force=True, incremental_cache_weeks=2)
    assert any(row["MMWRYear"] == "2024" for row in full.rows)


@pytest.mark.asyncio
async def test_failed_source_refresh_does_not_succeed_using_stale_cache(tmp_path, monkeypatch):
    from src.services.ingestion.countries import jp
    output = tmp_path / "cache.csv"
    output.write_text("cached")
    class Crawler:
        explicit_csv_url = "https://example.test/zensu.csv"
        session = SimpleNamespace(close=lambda: None)
        def _load_existing_year_weeks(self, _path): return set()
        def get(self, _url): raise OSError("source unavailable")
    class Writer:
        def __init__(self): self.artifacts = []
        async def write(self, _payload, *, kind, **_kwargs): self.artifacts.append(SimpleNamespace(kind=kind))
    monkeypatch.setattr(jp, "JapanIDWRCrawler", Crawler)
    with pytest.raises(RuntimeError, match="source unavailable"):
        await jp.collect(Writer(), source="jp_weekly", output_csv=output)
