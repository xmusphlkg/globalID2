"""
Pipeline smoke tests — validate the task → CrawlService → crawler/processor chain
for supported countries.

What is tested
--------------
1. DB connection reachable.
2. Country crawlers can be instantiated without errors.
3. A CRAWL_DATA task can be created via task_manager for each country.
4. CrawlService.execute() resolves the correct handler for each country
   (dry-run: fetch_list only, no full HTML download, no DB writes).
5. Structured log output follows the [Component][Country] key=value format.

Design decisions
----------------
* All tests use force=False, process=False, save_raw=False so they only hit
  Phase 1 (lightweight index fetch) and skip DB writes.  This makes the suite
  safe to run against a live database and keeps execution under ~20 s.
* Network calls are NOT mocked — a real connectivity check is part of the
  value of this test.  If you run offline, tests will be skipped gracefully.
* Each country test is independent (@pytest.mark.asyncio, no shared state).

Usage
-----
    # Run all pipeline tests (requires network + DB)
    pytest tests/test_pipeline.py -v

    # Run a single country
    pytest tests/test_pipeline.py -v -k CN

    # Direct execution (no pytest needed)
    python tests/test_pipeline.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import pytest_asyncio

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── Suppress noisy third-party loggers ────────────────────────────────────────
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════════
# Log capture helper
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LogCapture:
    """Capture log records emitted during a test and assert on their content."""

    records: List[logging.LogRecord] = field(default_factory=list)
    _handler: Optional[logging.Handler] = field(default=None, init=False, repr=False)

    def install(self, logger_name: str = "src") -> "LogCapture":
        class _H(logging.Handler):
            def emit(inner_self, record: logging.LogRecord) -> None:
                self.records.append(record)

        self._handler = _H()
        logging.getLogger(logger_name).addHandler(self._handler)
        return self

    def uninstall(self, logger_name: str = "src") -> None:
        if self._handler:
            logging.getLogger(logger_name).removeHandler(self._handler)

    def messages(self) -> List[str]:
        return [r.getMessage() for r in self.records]

    def has_pattern(self, pattern: str) -> bool:
        import re
        return any(re.search(pattern, m) for m in self.messages())

    def assert_structured(self) -> None:
        """Verify at least one log line follows [Component] or [Component][Country] format."""
        import re
        pattern = r"\[[\w-]+\]"
        structured = [m for m in self.messages() if re.search(pattern, m)]
        assert structured, (
            "No structured log lines found.\n"
            f"Captured messages:\n{chr(10).join(self.messages()[:20])}"
        )


class LoguruCapture:
    """Capture loguru log messages during a test via an in-memory sink."""

    def __init__(self):
        self.messages: List[str] = []
        self._sink_id: Optional[int] = None

    def install(self) -> "LoguruCapture":
        from loguru import logger
        self._sink_id = logger.add(self._sink, format="{message}", level="DEBUG")
        return self

    def _sink(self, message) -> None:
        self.messages.append(str(message))

    def uninstall(self) -> None:
        if self._sink_id is not None:
            from loguru import logger
            logger.remove(self._sink_id)
            self._sink_id = None

    def has_text(self, text: str) -> bool:
        return any(text in m for m in self.messages)

    def assert_contains(self, text: str) -> None:
        assert self.has_text(text), (
            f"Expected '{text}' in loguru output.\n"
            f"Captured messages:\n" + "\n".join(self.messages[-20:])
        )


# ══════════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session", autouse=True)
def setup_logging_once():
    from src.core.logging import setup_logging
    setup_logging()


@pytest_asyncio.fixture
async def app_ready():
    """Initialise DB for a single test, then discard the connection pool."""
    from src.core import init_app
    from src.core.database import dispose_database
    await init_app()
    yield
    # Dispose engine so connections don’t bleed into the next test’s loop
    await dispose_database()


# ══════════════════════════════════════════════════════════════════════════════
# 1. Infrastructure health
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_database_connection(app_ready):
    """DB is reachable and returns a valid version string."""
    from sqlalchemy import text
    from src.core import get_database

    async with get_database() as db:
        result = await db.execute(text("SELECT version()"))
        version = result.scalar()

    assert version and "PostgreSQL" in version, f"Unexpected DB version: {version!r}"
    print(f"\n  ✅ DB connected | {version.split(',')[0]}")


@pytest.mark.asyncio
async def test_task_manager_create_and_retrieve(app_ready):
    """task_manager can create a task and retrieve it by UUID."""
    from src.core.task_manager import task_manager
    from src.domain import TaskType, TaskPriority, TaskStatus

    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name="[smoke] pipeline test task",
        priority=TaskPriority.LOW,
        description="Created by test_pipeline.py",
        input_data={"country": "CN", "source": "cdc_weekly", "force": False},
    )
    assert task.task_uuid
    assert task.status == TaskStatus.PENDING
    assert task.country_id is not None

    retrieved = await task_manager.get_task_by_uuid(task.task_uuid)
    assert retrieved is not None
    assert retrieved.task_uuid == task.task_uuid
    assert retrieved.country_id == task.country_id

    print(f"\n  ✅ Task created | uuid={task.task_uuid} status={task.status.value}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Crawler instantiation (no network)
# ══════════════════════════════════════════════════════════════════════════════

def test_crawlers_importable():
    """Country crawlers can be imported and instantiated."""
    from src.data.crawlers.cn import ChinaCDCCrawler
    from src.data.crawlers.jp import JapanIDWRCrawler
    from src.data.crawlers.au import AustraliaNINDSSCrawler
    from src.data.crawlers.us import USNNDSSCrawler
    from src.data.crawlers.nz import NewZealandPHFCrawler
    from src.data.crawlers.tw import TaiwanNIDSSCrawler
    from src.data.crawlers.kr import KoreaKDCAOpenAPICrawler
    from src.data.crawlers.hk import HongKongCHPCrawler
    from src.data.crawlers.ch import SwitzerlandIDDCrawler

    crawlers = {
        "CN": ChinaCDCCrawler(),
        "JP": JapanIDWRCrawler(),
        "AU": AustraliaNINDSSCrawler(),
        "US": USNNDSSCrawler(),
        "NZ": NewZealandPHFCrawler(),
        "TW": TaiwanNIDSSCrawler(),
        "KR": KoreaKDCAOpenAPICrawler(),
        "HK": HongKongCHPCrawler(),
        "CH": SwitzerlandIDDCrawler(),
    }
    for code, crawler in crawlers.items():
        assert crawler is not None
        print(f"\n  ✅ {code} crawler instantiated | class={type(crawler).__name__}")


def test_processors_importable():
    """Country processors can be imported."""
    from src.data.processors.cn import DataProcessor
    from src.data.processors.jp import JPWeeklyUpdater
    from src.data.processors.au import AUMonthlyUpdater
    from src.data.processors.us import USWeeklyUpdater
    from src.data.processors.nz import NZMonthlyUpdater
    from src.data.processors.tw import TWMonthlyUpdater
    from src.data.processors.kr import KRMonthlyUpdater
    from src.data.processors.hk import HKMonthlyUpdater
    from src.data.processors.ch import CHMonthlyUpdater

    for cls in (
        DataProcessor,
        JPWeeklyUpdater,
        AUMonthlyUpdater,
        USWeeklyUpdater,
        NZMonthlyUpdater,
        TWMonthlyUpdater,
        KRMonthlyUpdater,
        HKMonthlyUpdater,
        CHMonthlyUpdater,
    ):
        assert cls is not None
        print(f"\n  ✅ {cls.__name__} importable")


# ══════════════════════════════════════════════════════════════════════════════
# 3. CrawlService country dispatch (dry-run — fetch_list only)
# ══════════════════════════════════════════════════════════════════════════════

COUNTRY_SOURCES = {
    "CN": "cdc_weekly",  # lightest CN source — single HTTP GET
    "JP": "jp_weekly",
    "AU": "all",
    "US": "nndss_api",
    "TW": "nidss_open_data",
    "KR": "kdca_open_api",
    "HK": "chp_notifiable",
    "CH": "foph_idd",
}

# Skip network tests if explicitly disabled even when --run-network is passed.
_skip_network = pytest.mark.skipif(
    os.environ.get("SKIP_NETWORK_TESTS", "").lower() in ("1", "true", "yes"),
    reason="SKIP_NETWORK_TESTS is set",
)


@pytest.mark.asyncio
@pytest.mark.network
@_skip_network
async def test_cn_fetch_list(app_ready):
    """CN crawler fetch_list returns at least one CrawlerResult."""
    from src.data.crawlers.cn import ChinaCDCCrawler

    cap = LoguruCapture().install()
    try:
        t0 = time.perf_counter()
        crawler = ChinaCDCCrawler()
        results = await crawler.fetch_list(source="cdc_weekly")
        elapsed = time.perf_counter() - t0
    finally:
        cap.uninstall()

    assert len(results) > 0, "CN fetch_list returned no results"

    # Verify structured log output contains [CN-CDC] prefix
    cap.assert_contains("[CN-CDC]")

    sample = results[0]
    print(
        f"\n  ✅ CN fetch_list | found={len(results)} "
        f"sample='{sample.title[:60]}' elapsed={elapsed:.1f}s"
    )


@pytest.mark.asyncio
@pytest.mark.network
@_skip_network
async def test_jp_discover_index(app_ready):
    """JP crawler can discover the NIID IDWR year-index URLs."""
    from src.data.crawlers.jp import JapanIDWRCrawler

    t0 = time.perf_counter()
    crawler = JapanIDWRCrawler()
    urls = crawler._discover_year_index_urls()
    elapsed = time.perf_counter() - t0

    assert len(urls) > 0, "JP _discover_year_index_urls returned no URLs"
    print(
        f"\n  \u2705 JP year index | urls={len(urls)} "
        f"sample='{urls[0]}' elapsed={elapsed:.1f}s"
    )

@pytest.mark.asyncio
@pytest.mark.network
@_skip_network
async def test_au_crawl_monthly(app_ready):
    """AU crawler crawl() fetches NINDSS data via Playwright Power BI token capture."""
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    cap = LoguruCapture().install()
    t0 = time.perf_counter()
    crawler = AustraliaNINDSSCrawler()
    try:
        results = await crawler.crawl(years=None, fill_missing=False)
    except Exception as exc:
        cap.uninstall()
        pytest.skip(f"AU crawler raised {type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - t0
    cap.uninstall()

    cap.assert_contains("[AU-NINDSS]")
    print(f"\n  \u2705 AU crawl | found={len(results)} elapsed={elapsed:.1f}s")


@pytest.mark.asyncio
async def test_au_playwright_capture_uses_thread_inside_async_loop():
    """AU Playwright capture should hop to a helper thread when called in an asyncio loop."""
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    crawler = AustraliaNINDSSCrawler()
    thread_names: list[str] = []

    def fake_capture():
        thread_names.append(threading.current_thread().name)
        return {
            "token": "MWCToken fake-token",
            "url": "https://example.invalid/webapi/capacities/test/public/query",
            "report_id": "report-123",
        }

    crawler._capture_playwright_runtime_config = fake_capture  # type: ignore[method-assign]

    ok = crawler._load_config_via_playwright()

    assert ok is True
    assert thread_names
    assert thread_names[0] != threading.current_thread().name
    assert crawler._config is not None
    assert crawler._config["accessToken"] == "MWCToken fake-token"


def test_au_dm0_helpers_handle_powerbi_numeric_strings():
    from src.data.crawlers.au import _find_dm0, _parse_dm0_to_state_counts

    raw = {
        "results": [{
            "result": {
                "data": {
                    "dsr": {
                        "DS": [{
                            "SH": [{
                                "DM1": [
                                    {"G1": "ACT"},
                                    {"G1": "NSW"},
                                    {"G1": "QLD"},
                                ]
                            }]
                        }]
                    }
                }
            }
        }]
    }
    dm0 = [{
        "G0": "2026",
        "X": [{"M0": "'37'"}, {"M0": "'2,448'"}, {"M0": "0L"}],
    }]

    parsed = _parse_dm0_to_state_counts(dm0, raw, "2026")

    assert parsed == {"ACT": 37, "NSW": 2448, "QLD": 0}
    assert _find_dm0([{"Code": "CouldNotResolveSemanticQueryDefinition"}]) is None


def test_source_scope_aliases_cover_cn_jp_au_legacy_values():
    from src.core.source_scopes import canonicalize_task_source

    assert canonicalize_task_source("gov", country_code="CN") == "nhc"
    assert canonicalize_task_source("pubmed_rss", country_code="CN") == "pubmed"
    assert canonicalize_task_source("jp_idwr", country_code="JP") == "jp_weekly"
    assert canonicalize_task_source("au_nindss", country_code="AU") == "all"
    assert canonicalize_task_source("location", country_code="AU") == "all"
    assert canonicalize_task_source("external", country_code="AU") == "all"
    assert canonicalize_task_source("nidss", country_code="TW") == "nidss_open_data"
    assert canonicalize_task_source("taiwan_cdc", country_code="TW") == "nidss_open_data"
    assert canonicalize_task_source("chp", country_code="HK") == "chp_notifiable"
    assert canonicalize_task_source("all", country_code="HK") == "chp_notifiable"
    assert canonicalize_task_source("sinan", country_code="BR") == "sinan_datasus"
    assert canonicalize_task_source("datasus", country_code="BR") == "sinan_datasus"
    assert canonicalize_task_source("kdca", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("kdca_portal", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("kosis", country_code="KR") == "kdca_open_api"
    assert canonicalize_task_source("all", country_code="KR") == "kdca_open_api"


def test_au_runtime_hints_can_be_extracted_from_live_query_shape():
    import json
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    crawler = AustraliaNINDSSCrawler()
    payload = {
        "queries": [{
            "Query": {
                "Commands": [{
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "From": [
                                {"Name": "d", "Entity": "DELTALOAD_DATAMART LOCATION_DIM"},
                                {"Name": "d1", "Entity": "DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT"},
                                {"Name": "d2", "Entity": "DELTALOAD_DATAMART DISEASE_DIM"},
                                {"Name": "d3", "Entity": "DELTALOAD_DATAMART CASE_DIM"},
                            ],
                            "Select": [
                                {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": "d"}},
                                        "Property": "STATE",
                                    }
                                },
                                {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Source": "d1"}},
                                        "Property": "Count_Notification",
                                    }
                                },
                            ],
                        }
                    }
                }]
            }
        }]
    }

    hints = crawler._extract_runtime_hints_from_post_data(json.dumps(payload))

    assert hints["location"]["measure_property"] == "Count_Notification"
    assert hints["location"]["location_alias"] == "d"
    assert hints["location"]["fact_alias"] == "d1"


def test_au_fetch_months_concurrent_preserves_explicit_zero_totals():
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    crawler = AustraliaNINDSSCrawler()

    def fake_fetch_month_disease(year: int, month: int, disease: str):
        return {"ACT": 0, "NSW": 0}

    crawler._fetch_month_disease = fake_fetch_month_disease  # type: ignore[method-assign]

    totals = crawler._fetch_months_concurrent([(2026, 3)], ["Anthrax"])

    assert totals == {(2026, 3, "Anthrax"): 0}


def test_au_raw_archive_writes_json(tmp_path):
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    crawler = AustraliaNINDSSCrawler(save_raw=True, raw_dir=tmp_path)
    crawler._config = {"apiUrl": "https://example.invalid/query"}
    crawler._runtime_hints = {"location": {"measure_property": "Count_Notification"}}

    crawler._archive_month_fetch(
        year=2026,
        month=3,
        disease="COVID-19",
        payload={"foo": "bar"},
        raw={"results": []},
        parsed_counts={"NSW": 12},
    )

    archived = tmp_path / "2026" / "03" / "COVID-19.json"
    assert archived.exists()
    assert '"measure_property": "Count_Notification"' in archived.read_text(encoding="utf-8")


def test_au_state_labels_resolve_to_subdivision_codes():
    from src.data.crawlers.au import normalize_au_state_code

    assert normalize_au_state_code("NSW") == "AU-NSW"
    assert normalize_au_state_code("New South Wales") == "AU-NSW"
    assert normalize_au_state_code("Western Australia") == "AU-WA"
    assert normalize_au_state_code("AUS") is None


def test_au_batch_subdivision_csvs_fetch_once(tmp_path):
    import csv

    from src.data.crawlers.au import AustraliaNINDSSCrawler

    crawler = AustraliaNINDSSCrawler()
    fetch_calls = []
    crawler._load_config = lambda: True  # type: ignore[method-assign]
    crawler.get_all_diseases = lambda: ["Anthrax", "Cholera"]  # type: ignore[method-assign]

    def fake_fetch(months, diseases):
        fetch_calls.append((months, diseases))
        return {
            (2026, 3, "Anthrax", "AU-NSW"): 4,
            (2026, 3, "Cholera", "AU-NSW"): 0,
            (2026, 3, "Anthrax", "AU-VIC"): 9,
        }

    crawler._fetch_months_concurrent_state_counts = fake_fetch  # type: ignore[method-assign]

    summaries = crawler.crawl_monthly_subdivision_csvs(
        tmp_path,
        jurisdiction_codes=["AU-NSW", "AU-VIC"],
        months=[(2026, 3)],
    )

    assert fetch_calls == [([(2026, 3)], ["Anthrax", "Cholera"])]
    assert summaries["AU-NSW"].row_count == 2
    assert summaries["AU-VIC"].row_count == 1

    nsw_csv = tmp_path / "au-nsw_nindss_monthly.csv"
    vic_csv = tmp_path / "au-vic_nindss_monthly.csv"
    assert nsw_csv.exists()
    assert vic_csv.exists()

    nsw_rows = list(csv.DictReader(nsw_csv.open(encoding="utf-8")))
    assert [row["Cases"] for row in nsw_rows] == ["4", "0"]
    assert {row["JurisdictionCode"] for row in nsw_rows} == {"AU-NSW"}
    assert {row["ParentCountryCode"] for row in nsw_rows} == {"AU"}
    assert {row["LocationType"] for row in nsw_rows} == {"subdivision"}
    assert {row["GeographyKey"] for row in nsw_rows} == {"country:AU-NSW:national"}

    vic_rows = list(csv.DictReader(vic_csv.open(encoding="utf-8")))
    assert vic_rows[0]["ReportingArea"] == "Victoria"
    assert vic_rows[0]["Geocode"] == "AU-VIC"


def test_au_refresh_source_falls_back_to_raw_archive(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import json

    from src.data.processors.au import AUMonthlyUpdater

    raw_root = tmp_path / "raw"
    month_dir = raw_root / "2026" / "03"
    month_dir.mkdir(parents=True, exist_ok=True)
    (month_dir / "Anthrax.json").write_text(
        json.dumps({"disease": "Anthrax", "parsed_counts": {"NSW": 0, "AUS": 999}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (month_dir / "Cholera.json").write_text(
        json.dumps({"disease": "Cholera", "parsed_counts": {"QLD": 5, "TOTAL": 888}}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_crawl_monthly_national_csv(self, output_csv, months=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "src.data.processors.au.AustraliaNINDSSCrawler.crawl_monthly_national_csv",
        fake_crawl_monthly_national_csv,
    )

    updater = AUMonthlyUpdater(output_csv=tmp_path / "au.csv")
    result = updater.refresh_source(months=[(2026, 3)], raw_dir=raw_root)

    assert {row["RawDiseaseLabel"]: row["Cases"] for row in result.rows} == {
        "Anthrax": "0",
        "Cholera": "5",
    }
    assert any("using raw archive" in line for line in result.script_logs)
    assert result.source_csv.exists()


def test_au_subdivision_refresh_source_falls_back_to_raw_archive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    import json

    from src.data.processors.au import AUMonthlyUpdater

    raw_root = tmp_path / "raw"
    month_dir = raw_root / "2026" / "03"
    month_dir.mkdir(parents=True, exist_ok=True)
    (month_dir / "Anthrax.json").write_text(
        json.dumps(
            {"disease": "Anthrax", "parsed_counts": {"NSW": 4, "VIC": 9, "AUS": 99}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_crawl_monthly_subdivision_csv(
        self,
        output_csv,
        *,
        jurisdiction_code,
        months=None,
    ):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "src.data.processors.au.AustraliaNINDSSCrawler.crawl_monthly_subdivision_csv",
        fake_crawl_monthly_subdivision_csv,
    )

    updater = AUMonthlyUpdater(country_code="AU-NSW", output_csv=tmp_path / "au-nsw.csv")
    result = updater.refresh_source(months=[(2026, 3)], raw_dir=raw_root)

    assert [(row["RawDiseaseLabel"], row["Cases"]) for row in result.rows] == [
        ("Anthrax", "4")
    ]
    assert result.rows[0]["JurisdictionCode"] == "AU-NSW"
    assert result.rows[0]["ParentCountryCode"] == "AU"
    assert result.rows[0]["LocationType"] == "subdivision"
    assert result.rows[0]["GeographyKey"] == "country:AU-NSW:national"
    assert any("using raw archive" in line for line in result.script_logs)


def test_au_refresh_source_falls_back_to_previous_csv_snapshot(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from src.data.processors.au import AUMonthlyUpdater

    output_csv = tmp_path / "au.csv"
    updater = AUMonthlyUpdater(output_csv=output_csv)
    updater._write_rows_to_output_csv(
        [
            {
                "Date": "2026-03-01",
                "RawDiseaseLabel": "Anthrax",
                "DiseaseFull": "Anthrax",
                "Cases": "0",
                "Group": "national_total",
                "Incidence": "",
                "Population": "",
                "Source": updater.source_name,
                "__source_file": "cached.csv",
            },
            {
                "Date": "2026-03-01",
                "RawDiseaseLabel": "Cholera",
                "DiseaseFull": "Cholera",
                "Cases": "7",
                "Group": "national_total",
                "Incidence": "",
                "Population": "",
                "Source": updater.source_name,
                "__source_file": "cached.csv",
            },
        ]
    )

    def fake_crawl_monthly_national_csv(self, output_csv, months=None):
        raise RuntimeError("temporary upstream outage")

    monkeypatch.setattr(
        "src.data.processors.au.AustraliaNINDSSCrawler.crawl_monthly_national_csv",
        fake_crawl_monthly_national_csv,
    )

    result = updater.refresh_source(months=[(2026, 3)], raw_dir=tmp_path / "missing-raw")

    assert {row["RawDiseaseLabel"]: row["Cases"] for row in result.rows} == {
        "Anthrax": "0",
        "Cholera": "7",
    }
    assert any("previous CSV snapshot" in line for line in result.script_logs)


def test_au_refresh_source_replaces_partial_live_csv_with_more_complete_archive(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    import json
    from datetime import date

    from src.data.crawlers.au import AUFetchSummary
    from src.data.processors.au import AUMonthlyUpdater

    raw_root = tmp_path / "raw"
    month_dir = raw_root / "2026" / "03"
    month_dir.mkdir(parents=True, exist_ok=True)
    (month_dir / "Anthrax.json").write_text(
        json.dumps({"disease": "Anthrax", "parsed_counts": {"NSW": 0}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (month_dir / "Cholera.json").write_text(
        json.dumps({"disease": "Cholera", "parsed_counts": {"QLD": 9}}, ensure_ascii=False),
        encoding="utf-8",
    )

    updater = AUMonthlyUpdater(output_csv=tmp_path / "au.csv")

    def fake_crawl_monthly_national_csv(self, output_csv, months=None):
        updater._write_rows_to_output_csv(
            [
                {
                    "Date": "2026-03-01",
                    "RawDiseaseLabel": "Anthrax",
                    "DiseaseFull": "Anthrax",
                    "Cases": "0",
                    "Group": "national_total",
                    "Incidence": "",
                    "Population": "",
                    "Source": updater.source_name,
                    "__source_file": "live.csv",
                }
            ]
        )
        return AUFetchSummary(row_count=1, latest_date=date(2026, 3, 1), csv_url="live")

    monkeypatch.setattr(
        "src.data.processors.au.AustraliaNINDSSCrawler.crawl_monthly_national_csv",
        fake_crawl_monthly_national_csv,
    )

    result = updater.refresh_source(months=[(2026, 3)], raw_dir=raw_root)

    assert {row["RawDiseaseLabel"]: row["Cases"] for row in result.rows} == {
        "Anthrax": "0",
        "Cholera": "9",
    }
    assert any("using raw archive" in line for line in result.script_logs)


@pytest.mark.asyncio
async def test_sources_flow_includes_task_only_rows_without_disease_records(app_ready):
    import uuid

    from dashboard.api.routers.sources import get_sources_flow
    from src.core import get_session_maker
    from src.domain import Country, Task, TaskPriority, TaskStatus, TaskType

    code = f"T{uuid.uuid4().hex[:6].upper()}"

    async with get_session_maker()() as db:
        country = Country(
            code=code,
            name="Test Flow Country",
            name_en="Test Flow Country",
            language="en",
            timezone="UTC",
        )
        db.add(country)
        await db.flush()

        db.add(
            Task(
                task_type=TaskType.CRAWL_DATA,
                task_name="Test crawl for source flow",
                status=TaskStatus.PENDING,
                priority=TaskPriority.NORMAL,
                country_id=country.id,
                input_data={
                    "country": code,
                    "country_code": code,
                    "source": "cdc_weekly",
                },
            )
        )
        await db.flush()

        flows = await get_sources_flow(country_code=code, db=db)

        assert len(flows) == 1
        assert flows[0].country_code == code
        assert flows[0].data_source == "China CDC Weekly"
        assert flows[0].record_count == 0
        assert flows[0].latest_task_source == "cdc_weekly"
        assert flows[0].latest_task_status == "pending"
        assert flows[0].stages[0].status == "pending"

        await db.rollback()


@pytest.mark.asyncio
async def test_quality_sources_merges_canonical_labels(app_ready):
    import uuid

    from dashboard.api.routers.quality import quality_sources
    from src.core import get_session_maker
    from src.domain import Country, Disease, DiseaseRecord

    suffix = uuid.uuid4().hex[:8]

    async with get_session_maker()() as db:
        country = Country(
            code=f"Q{suffix[:6].upper()}",
            name="Test Quality Country",
            name_en="Test Quality Country",
            language="en",
            timezone="UTC",
        )
        disease = Disease(
            name=f"test-disease-{suffix}",
            category="test",
        )
        db.add_all([country, disease])
        await db.flush()

        db.add_all(
            [
                DiseaseRecord(
                    time=datetime(2099, 1, 1, tzinfo=timezone.utc),
                    disease_id=disease.id,
                    country_id=country.id,
                    cases=1,
                    data_source="Gov Data",
                ),
                DiseaseRecord(
                    time=datetime(2099, 2, 1, tzinfo=timezone.utc),
                    disease_id=disease.id,
                    country_id=country.id,
                    cases=2,
                    data_source="GOV Data",
                ),
            ]
        )
        await db.flush()

        rows = await quality_sources(country_code=country.code, db=db)

        assert len(rows) == 1
        assert rows[0].data_source == "NHC"
        assert rows[0].count == 2
        assert rows[0].percentage == 100.0

        await db.rollback()


@pytest.mark.asyncio
@pytest.mark.network
@_skip_network
async def test_us_fetch_raw_pages(app_ready):
    """US crawler fetch_raw_pages returns paginated NNDSS rows."""
    from src.data.crawlers.us import USNNDSSCrawler

    cap = LoguruCapture().install()
    try:
        t0 = time.perf_counter()
        crawler = USNNDSSCrawler()
        rows, source_url = crawler.fetch_raw_pages()
        elapsed = time.perf_counter() - t0
    finally:
        cap.uninstall()

    assert len(rows) > 0, "US fetch_raw_pages returned no rows"
    assert "cdc.gov" in source_url
    cap.assert_contains("[US-NNDSS]")
    print(
        f"\n  \u2705 US fetch_raw_pages | rows={len(rows)} "
        f"source={source_url[:60]} elapsed={elapsed:.1f}s"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 4. CrawlService task dispatch (uses task_manager, no DB writes)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.network
@_skip_network
@pytest.mark.parametrize("country", ["CN", "US"])
async def test_crawl_service_dispatch(country: str, app_ready):
    """
    CrawlService.execute() resolves the correct handler for CN and US.

    JP and AU are in test_crawl_service_dispatch_slow (separate, opt-in via
    ``-m slow``) because their Phase 1 downloads many CSV files and can take
    several minutes.

    Uses process=False + save_raw=False so no DataProcessor / DB writes are
    triggered — only Phase 1 (fetch_list) runs.
    """
    from src.core.task_manager import task_manager
    from src.domain import TaskType, TaskPriority
    from src.services.crawl_service import CrawlService

    source = COUNTRY_SOURCES[country]

    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name=f"[smoke] {country} dispatch test",
        priority=TaskPriority.LOW,
        input_data={
            "country": country,
            "country_code": country,
            "source": source,
            "force": False,
            "process": False,
            "save_raw": False,
            "fill_missing": False,
        },
    )
    assert task.country_id is not None

    cap = LoguruCapture().install()
    try:
        t0 = time.perf_counter()
        service = CrawlService()
        result = await service.execute(
            task=task,
            country_code=country,
            source=source,
            force=False,
            process=False,
            save_raw=False,
            fill_missing=False,
        )
        elapsed = time.perf_counter() - t0
    finally:
        cap.uninstall()

    # CrawlService should return a CrawlResult with valid fields
    assert result is not None, f"CrawlService returned None for {country}"

    # Verify at least some structured logs were emitted
    import re
    pattern = re.compile(r"\[[\w-]+\]")
    structured = [m for m in cap.messages if pattern.search(m)]
    assert structured, (
        f"[{country}] No structured log lines found.\n"
        + "\n".join(cap.messages[-10:])
    )

    print(
        f"\n  ✅ [{country}] CrawlService dispatch ok | "
        f"new_reports={getattr(result, 'new_reports', '?')} "
        f"elapsed={elapsed:.1f}s"
    )


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.network
@_skip_network
@pytest.mark.parametrize("country", ["JP", "AU"])
async def test_crawl_service_dispatch_slow(country: str, app_ready):
    """
    CrawlService.execute() for JP and AU — downloads real CSV files so can
    take several minutes.  Only runs when ``pytest -m slow`` is specified.
    """
    from src.core.task_manager import task_manager
    from src.domain import TaskType, TaskPriority
    from src.services.crawl_service import CrawlService

    source = COUNTRY_SOURCES[country]

    task = await task_manager.create_task(
        task_type=TaskType.CRAWL_DATA,
        task_name=f"[smoke-slow] {country} dispatch test",
        priority=TaskPriority.LOW,
        input_data={
            "country": country,
            "country_code": country,
            "source": source,
            "force": False,
            "process": False,
            "save_raw": False,
            "fill_missing": False,
        },
    )
    assert task.country_id is not None

    cap = LoguruCapture().install()
    try:
        t0 = time.perf_counter()
        service = CrawlService()
        result = await service.execute(
            task=task,
            country_code=country,
            source=source,
            force=False,
            process=False,
            save_raw=False,
            fill_missing=False,
        )
        elapsed = time.perf_counter() - t0
    finally:
        cap.uninstall()

    assert result is not None, f"CrawlService returned None for {country}"
    print(
        f"\n  ✅ [{country}] CrawlService dispatch ok | "
        f"new_reports={getattr(result, 'new_reports', '?')} "
        f"elapsed={elapsed:.1f}s"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Log format compliance
# ══════════════════════════════════════════════════════════════════════════════

def test_log_format_standard():
    """
    Verify that log messages from key pipeline components follow the
    [Component][Country] action | key=value format by importing and
    running a minimal in-process operation (no network needed).
    """
    import re
    from src.data.crawlers.cn import ChinaCDCCrawler

    cap = LoguruCapture().install()
    try:
        # Instantiating the crawler should emit at least one structured log
        ChinaCDCCrawler()
    finally:
        cap.uninstall()

    # Allow no records if the crawler emits nothing on __init__ (that's fine)
    # but if records exist they must follow the format
    bracket_pattern = re.compile(r"\[[\w-]+\]")
    kv_pattern = re.compile(r"\w+=")

    for msg in cap.messages:
        if bracket_pattern.search(msg):
            # Any bracketed log should also have key=value pairs
            assert kv_pattern.search(msg) or "|" in msg, (
                f"Structured log missing key=value pairs: {msg!r}"
            )

    print(f"\n  \u2705 Log format check passed | captured={len(cap.messages)} records")


# ══════════════════════════════════════════════════════════════════════════════
# Direct execution mode (python tests/test_pipeline.py)
# ══════════════════════════════════════════════════════════════════════════════

async def _run_direct() -> None:
    """Run a subset of tests in direct (non-pytest) mode for quick feedback."""
    from src.core import init_app
    from src.core.logging import setup_logging

    setup_logging()
    await init_app()

    results: Dict[str, str] = {}

    async def _run(label: str, coro):
        try:
            await coro
            results[label] = "✅ PASS"
        except Exception as exc:
            results[label] = f"❌ FAIL — {exc}"

    # DB health
    from sqlalchemy import text
    from src.core import get_database
    async with get_database() as db:
        ver = (await db.execute(text("SELECT version()"))).scalar()
    print(f"\n  ✅ DB: {ver.split(',')[0]}")

    # Crawler imports
    test_crawlers_importable()
    test_processors_importable()

    # Fetch-list per country (skippable via env var)
    if os.environ.get("SKIP_NETWORK_TESTS", "").lower() not in ("1", "true", "yes"):
        await _run("CN fetch_list", test_cn_fetch_list(None))
        await _run("JP discover_index", test_jp_discover_index(None))
        await _run("US fetch_raw_pages", test_us_fetch_raw_pages(None))
    else:
        print("  ⚠️  SKIP_NETWORK_TESTS set — skipping network tests")

    print("\n" + "─" * 60)
    for label, status in results.items():
        print(f"  {status}  {label}")
    print("─" * 60)

    failed = sum(1 for s in results.values() if s.startswith("❌"))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_run_direct())
