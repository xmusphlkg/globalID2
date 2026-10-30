from pathlib import Path
import pytest
from src.data.crawlers.tw import TaiwanNIDSSCrawler, TWDiseaseSource
from src.data.processors.tw import TWMonthlyUpdater
from src.services.ingestion.countries.tw import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/TW/monthly.csv"


def test_tw_observation_contract_retains_local_imported_counts(tmp_path, monkeypatch):
    raw = FIXTURE.read_bytes()
    source = TWDiseaseSource("042", "後天免疫缺乏症候群", "https://od.cdc.gov.tw/eic/Age_County_Gender_042.csv", "")
    monkeypatch.setattr(TaiwanNIDSSCrawler, "fetch_disease_index", lambda self: [source])
    monkeypatch.setattr(TaiwanNIDSSCrawler, "_download_csv_text", lambda self, disease: raw.decode())
    updater = TWMonthlyUpdater(output_csv=tmp_path / "tw.csv")
    expected = updater.refresh_source(months=[(2026, 1), (2026, 2)], raw_dir=tmp_path / "archive")
    monkeypatch.chdir(tmp_path)
    saved = artifact(raw, code="TW", kind="monthly_csv", url=source.monthly_csv_url, metadata={"code": source.code, "name": source.name, "weekly_csv_url": ""})
    monkeypatch.setattr(TaiwanNIDSSCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize([saved], updater=updater, source="nidss_open_data", months=[(2026, 1), (2026, 2)], output_csv=updater.output_csv)
    assert_same_facts(actual, expected, "TW", "SRC_TW_NIDSS")
    assert actual.rows[0]["LocalCases"] == "5"
