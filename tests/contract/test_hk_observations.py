from pathlib import Path
import pytest
from src.data.crawlers.hk import HongKongCHPCrawler
from src.data.processors.hk import HKMonthlyUpdater
from src.services.ingestion.countries.hk import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/HK/annual.csv"


def test_hk_observation_contract_preserves_explicit_zero_and_excludes_persons(tmp_path, monkeypatch):
    raw = FIXTURE.read_bytes()
    monkeypatch.setattr(HongKongCHPCrawler, "_download_year_csv", lambda self, year: raw.decode())
    updater = HKMonthlyUpdater(output_csv=tmp_path / "hk.csv")
    expected = updater.refresh_source(months=[(2026, 1), (2026, 2)], raw_dir=tmp_path / "archive")
    monkeypatch.chdir(tmp_path)
    url = HongKongCHPCrawler()._csv_url(2026)
    saved = artifact(raw, code="HK", kind="annual_csv", url=url, metadata={"year": 2026})
    monkeypatch.setattr(HongKongCHPCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize([saved], updater=updater, source="chp_notifiable", months=[(2026, 1), (2026, 2)], output_csv=updater.output_csv)
    assert_same_facts(actual, expected, "HK", "SRC_HK_CHP")
    assert [row["Cases"] for row in actual.rows] == ["1", "0"]
