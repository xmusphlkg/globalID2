from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data.crawlers.nz import NewZealandPHFCrawler
from src.data.processors.nz import NZMonthlyUpdater
from src.services.ingestion.countries.nz import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/NZ/report.zip"


def test_nz_monthly_contract_retains_national_zero_without_dhb_rows(tmp_path, monkeypatch):
    raw = FIXTURE.read_bytes()
    report = {"type": "individual", "year": 2026, "month": 3, "url": "https://example.org/report"}
    url = "https://example.org/report.zip"
    monkeypatch.setattr(NewZealandPHFCrawler, "fetch_report_index", lambda self, **kwargs: [report])
    monkeypatch.setattr(NewZealandPHFCrawler, "resolve_download_url", lambda self, report: url)
    import requests
    monkeypatch.setattr(requests.Session, "get", lambda self, *args, **kwargs: SimpleNamespace(content=raw, raise_for_status=lambda: None))
    updater = NZMonthlyUpdater(output_csv=tmp_path / "nz.csv")
    expected = updater.refresh_source(months=[(2026, 3)], raw_dir=tmp_path / "archive")
    monkeypatch.chdir(tmp_path)
    saved = artifact(raw, code="NZ", kind="nz_report_zip", url=url, metadata={"report": report, "media_type": "application/zip"})
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize([saved], updater=updater, source="phf_monthly", months=[(2026, 3)], output_csv=updater.output_csv)
    assert_same_facts(actual, expected, "NZ", "SRC_NZ_PHS")
    assert actual.series_rows == expected.series_rows
