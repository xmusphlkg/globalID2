from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.data.crawlers import fi as crawler_module
from src.data.crawlers.fi import FinlandTHLCrawler
from src.data.processors.fi import FIMonthlyUpdater
from src.services.ingestion.countries.fi import organize
from tests.contract.monthly_helpers import artifact, assert_same_facts
FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/FI"
NOW = datetime(2026, 8, 7, 9, tzinfo=timezone.utc)


def test_fi_observation_contract_retains_parent_child_and_zero(tmp_path, monkeypatch):
    dimensions, raw = (FIXTURE / "dimensions.jsonp").read_bytes(), (FIXTURE / "cube.csv").read_bytes()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(crawler_module, "datetime", Clock)
    def get(self, url, **kwargs):
        content = dimensions if url.endswith(".dimensions.json") else raw
        return SimpleNamespace(content=content, url=url if content == dimensions else "https://example.org/query.csv", encoding="utf-8", headers={"last-modified": "Fri, 07 Aug 2026 08:00:00 GMT"})
    monkeypatch.setattr(FinlandTHLCrawler, "get", get)
    updater = FIMonthlyUpdater(output_csv=tmp_path / "fi.csv")
    expected = updater.refresh_source(months=[(2026, 7)], as_of=date(2026, 8, 7), raw_dir=tmp_path / "archive")
    monkeypatch.chdir(tmp_path)
    saved = [artifact(dimensions, code="FI", kind="thl_dimensions", url="https://example.org/dimensions"), artifact(raw, code="FI", kind="thl_csv", url="https://example.org/query.csv", metadata={"retrieved_at": NOW.isoformat(), "source_updated_at": "Fri, 07 Aug 2026 08:00:00 GMT"})]
    monkeypatch.setattr(FinlandTHLCrawler, "get", lambda *args, **kwargs: pytest.fail("organize fetched source"))
    actual = organize(saved, updater=updater, source="thl_ttr_monthly", months=[(2026, 7)], output_csv=updater.output_csv, as_of=date(2026, 8, 7))
    assert_same_facts(actual, expected, "FI", "SRC_FI_THL_TTR")
    assert [row["Cases"] for row in actual.rows] == ["3", "0"]
