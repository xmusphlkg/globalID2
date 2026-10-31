from datetime import date, datetime, timezone
from pathlib import Path
import pytest
from src.data.crawlers import se as crawler_module
from src.data.crawlers.se import SwedenSmiNetCrawler, SEDiseasePage
from src.data.processors.se import SEMonthlyUpdater
from src.services.ingestion.countries.se import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
from src.data.crawlers.subdivision_registry import source_label_for_code
from src.services.crawl_service import CrawlService
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/SE"
NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("country_code", [code for code in CrawlService._PIPELINES if code.startswith("SE")])
def test_sweden_national_and_county_contract_preserves_source_totals(tmp_path, monkeypatch, country_code):
    fixture = FIXTURE.parent / country_code
    html, raw = (fixture / "gonorre_2025.html").read_bytes(), (fixture / "gonorre_2025.csv").read_bytes()
    page = SEDiseasePage("gonorre", "Gonorré", "https://www.folkhalsomyndigheten.se/statistik/gonorre-statistik/")
    url = SwedenSmiNetCrawler.page_url_for_year(page, 2025)
    csv_url = "https://sminet3-prod.sminet.se/mapapp/gonorre_mSWE_2025_ALL_20260808.csv"
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(crawler_module, "datetime", Clock)
    monkeypatch.setattr(SwedenSmiNetCrawler, "discover_disease_pages", lambda self: [page])
    monkeypatch.setattr(SwedenSmiNetCrawler, "_fetch_page_html", lambda self, page, year: (html.decode(), url))
    monkeypatch.setattr(SwedenSmiNetCrawler, "_fetch_csv_bytes", lambda self, url: raw)
    updater = SEMonthlyUpdater(country_code=country_code, output_csv=tmp_path / "se.csv")
    expected = updater.refresh_source(months=[(2025, 1), (2025, 2)], today=date(2025, 4, 10), raw_dir=tmp_path / "archive")
    monkeypatch.chdir(tmp_path)
    saved = [artifact(html, code="SE", kind="smi_html", url=url, metadata={"page": {"code": page.code, "index_label": page.index_label, "url": page.url}, "year": 2025, "retrieved_at": NOW.isoformat().replace("+00:00", "Z")}), artifact(raw, code="SE", kind="smi_csv", url=csv_url, metadata={"code": page.code, "year": 2025})]
    monkeypatch.setattr(SwedenSmiNetCrawler, "_get_response", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize(saved, updater=updater, source="fohm_sminet", months=[(2025, 1), (2025, 2)], output_csv=updater.output_csv, today=date(2025, 4, 10))
    assert_same_facts(actual, expected, "SE", "SRC_SE_FOHM_SMINET", fixture_code=country_code)
    if country_code == "SE":
        assert "SE:national" in {row["Geography"] for row in actual.rows}
    else:
        assert {row["Geography"] for row in actual.rows} == {"SE:county:" + source_label_for_code("SE", country_code)}
