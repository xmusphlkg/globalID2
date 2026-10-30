from datetime import datetime, timezone
from pathlib import Path
import pytest
from src.data.crawlers import ca as crawler_module
from src.data.crawlers.ca import CanadaOntarioPHOCrawler
from src.data.processors.ca import CAOntarioMonthlyUpdater
from src.services.ingestion.countries.ca_on import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/CA-ON/ontario_2026.csv"
NOW = datetime(2026, 8, 3, 11, tzinfo=timezone.utc)


def test_ontario_official_file_contract_preserves_zero_and_preliminary(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(crawler_module, "datetime", Clock)
    updater = CAOntarioMonthlyUpdater(output_csv=tmp_path / "ontario.csv")
    expected = updater.refresh_source(input_file=FIXTURE, reporting_year=2026, months=[(2026, 1), (2026, 2)], raw_dir=tmp_path / "archive")
    raw = FIXTURE.read_bytes()
    monkeypatch.chdir(tmp_path)
    saved = artifact(raw, code="CA-ON", kind="pho_export_file", url=FIXTURE.as_uri(), metadata={"filename": FIXTURE.name, "source_file_mtime": datetime.fromtimestamp(FIXTURE.stat().st_mtime, timezone.utc).isoformat(), "retrieved_at": NOW.isoformat(), "landing_url": CanadaOntarioPHOCrawler().landing_url})
    monkeypatch.setattr(CanadaOntarioPHOCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize([saved], updater=updater, source="pho_idto_monthly", months=[(2026, 1), (2026, 2)], output_csv=updater.output_csv, reporting_year=2026)
    assert_same_facts(actual, expected, "CA-ON", "SRC_CA_ON_PHO_IDTO", fixture_code="CA-ON-file")
    assert actual.content_sha256 == expected.content_sha256


def test_ontario_live_powerbi_contract_matches_official_parser(tmp_path, monkeypatch):
    import json
    from src.data.crawlers.ca import discover_monthly_visual
    metadata = (FIXTURE.parent / "metadata.json").read_bytes()
    query = (FIXTURE.parent / "query.json").read_bytes()
    crawler = CanadaOntarioPHOCrawler()
    visual = discover_monthly_visual(json.loads(metadata), page_display_name=crawler.page_display_name, preferred_visual_name=crawler.visual_name)
    monkeypatch.setattr(CanadaOntarioPHOCrawler, "_fetch_live", lambda self: (visual, "https://wabi-canada-central-api.analysis.windows.net", json.loads(metadata), json.loads(query), "temporary-token"))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(crawler_module, "datetime", Clock)
    updater = CAOntarioMonthlyUpdater(output_csv=tmp_path / "live.csv")
    expected = updater.refresh_source(months=[(2026, 1), (2026, 2), (2026, 3)])
    monkeypatch.chdir(tmp_path)
    saved = [artifact(metadata, code="CA-ON", kind="metadata", url="https://wabi-canada-central-api.analysis.windows.net/explore/reports/report/modelsAndExploration"), artifact(query, code="CA-ON", kind="query", url="https://wabi-canada-central-api.analysis.windows.net/explore/querydata?synchronous=true")]
    saved[-1].created_at = NOW
    monkeypatch.setattr(CanadaOntarioPHOCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize(saved, updater=updater, source="pho_idto_monthly", months=[(2026, 1), (2026, 2), (2026, 3)], output_csv=updater.output_csv)
    assert_same_facts(actual, expected, "CA-ON", "SRC_CA_ON_PHO_IDTO", fixture_code="CA-ON-live")
    assert actual.model_refresh_time == expected.model_refresh_time
    assert actual.content_sha256 == expected.content_sha256
