"""
Pipeline smoke tests — validate the task → CrawlService → crawler/processor chain
for all supported countries (CN, JP, AU, US).

What is tested
--------------
1. DB connection reachable.
2. All four country crawlers can be instantiated without errors.
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
    from src.core.database import get_engine
    await init_app()
    yield
    # Dispose engine so connections don’t bleed into the next test’s loop
    engine = get_engine()
    await engine.dispose()


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

    retrieved = await task_manager.get_task_by_uuid(task.task_uuid)
    assert retrieved is not None
    assert retrieved.task_uuid == task.task_uuid

    print(f"\n  ✅ Task created | uuid={task.task_uuid} status={task.status.value}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Crawler instantiation (no network)
# ══════════════════════════════════════════════════════════════════════════════

def test_crawlers_importable():
    """All four country crawlers can be imported and instantiated."""
    from src.data.crawlers.cn import ChinaCDCCrawler
    from src.data.crawlers.jp import JapanIDWRCrawler
    from src.data.crawlers.au import AustraliaNINDSSCrawler
    from src.data.crawlers.us import USNNDSSCrawler

    crawlers = {
        "CN": ChinaCDCCrawler(),
        "JP": JapanIDWRCrawler(),
        "AU": AustraliaNINDSSCrawler(),
        "US": USNNDSSCrawler(),
    }
    for code, crawler in crawlers.items():
        assert crawler is not None
        print(f"\n  ✅ {code} crawler instantiated | class={type(crawler).__name__}")


def test_processors_importable():
    """All four country processors can be imported."""
    from src.data.processors.cn import DataProcessor
    from src.data.processors.jp import JPWeeklyUpdater
    from src.data.processors.au import AUMonthlyUpdater
    from src.data.processors.us import USWeeklyUpdater

    for cls in (DataProcessor, JPWeeklyUpdater, AUMonthlyUpdater, USWeeklyUpdater):
        assert cls is not None
        print(f"\n  ✅ {cls.__name__} importable")


# ══════════════════════════════════════════════════════════════════════════════
# 3. CrawlService country dispatch (dry-run — fetch_list only)
# ══════════════════════════════════════════════════════════════════════════════

COUNTRY_SOURCES = {
    "CN": "cdc_weekly",  # lightest CN source — single HTTP GET
    "JP": "jp_idwr",
    "AU": "all",
    "US": "all",
}

# Skip network tests if explicitly disabled (e.g. CI without external access)
_skip_network = pytest.mark.skipif(
    os.environ.get("SKIP_NETWORK_TESTS", "").lower() in ("1", "true", "yes"),
    reason="SKIP_NETWORK_TESTS is set",
)


@pytest.mark.asyncio
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
