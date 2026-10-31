from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data.crawlers.at import AustriaAGESRadarCrawler, AGESIssue
from src.data.processors.at import ATMonthlyUpdater
from src.services.ingestion.countries.at import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/AT/ages.csv"


def test_at_source_native_observations_remain_unpublished(tmp_path, monkeypatch):
    raw = FIXTURE.read_bytes()
    issue = AGESIssue("https://example.org/issue", "https://example.org/ages.csv", date(2026, 2, 1), datetime(2026, 3, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(AustriaAGESRadarCrawler, "discover_issues", lambda self: [issue])
    monkeypatch.setattr(AustriaAGESRadarCrawler, "get", lambda *args, **kwargs: SimpleNamespace(content=raw))
    updater = ATMonthlyUpdater(output_csv=tmp_path / "at.csv")
    expected = updater.refresh_source(months=[(2026, 2)])
    monkeypatch.chdir(tmp_path)
    saved = artifact(raw, code="AT", kind="ages_csv", url=issue.csv_url, metadata={"detail_url": issue.detail_url, "report_month": issue.report_month.isoformat(), "retrieved_at": issue.retrieved_at.isoformat()})
    monkeypatch.setattr(AustriaAGESRadarCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize([saved], updater=updater, source="ages_radar", months=[(2026, 2)], output_csv=updater.output_csv)
    assert_same_facts(actual, expected, "AT", "SRC_AT_AGES_RADAR")
    assert actual.rows[0]["PublicReleaseEnabled"] == "false"
