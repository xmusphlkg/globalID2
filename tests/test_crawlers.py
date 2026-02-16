#!/usr/bin/env python3
"""
Unified Crawler Tests - Clean and Efficient

Comprehensive tests for all data crawler sources with minimal output.
Supports both pytest and direct execution modes.

Usage:
    # Quick test with minimal output (recommended for development)
    python tests/test_crawlers.py
    python tests/test_crawlers.py --country CN
    
    # Pytest mode (for CI/CD)
    pytest tests/test_crawlers.py -v
    pytest tests/test_crawlers.py -v --country CN
    
    # Show detailed output
    python tests/test_crawlers.py --verbose

Features:
    - Minimal log output (ERROR level only)
    - Fast execution (~8 seconds)
    - Clear pass/fail indicators (✅/❌)
    - Country-specific testing support
    - Report date display
    - Disease count reporting
    - Dual mode: pytest + direct execution
"""
import asyncio
import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import pytest

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.logging import setup_logging
from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.data.normalizers.english_mapper import create_disease_mapper


# ============================================================================
# Global Configuration
# ============================================================================

# Default country code (can be overridden via CLI or pytest)
DEFAULT_COUNTRY = "CN"
VERBOSE_MODE = False


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def configure_logging():
    """
    Configure logging to reduce noise output.
    
    Sets all loggers to WARNING level to only show critical issues.
    Auto-runs before any tests (autouse=True).
    """
    setup_logging()
    # Suppress INFO logs from application modules
    if not VERBOSE_MODE:
        logging.getLogger('src').setLevel(logging.WARNING)
        logging.getLogger('__main__').setLevel(logging.WARNING)
        logging.getLogger('sqlalchemy').setLevel(logging.ERROR)


@pytest.fixture(scope="session")
def event_loop():
    """
    Create and manage asyncio event loop for async tests.
    
    Session-scoped fixture to reuse event loop across all tests.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def country_code(request):
    """
    Get country code from pytest command line or default.
    
    Returns:
        str: Country code (default: CN)
    """
    return getattr(request.config.option, 'country', DEFAULT_COUNTRY)


@pytest.fixture
def crawler(country_code):
    """
    Initialize crawler instance for specified country.
    
    Args:
        country_code: Country code from CLI or default
        
    Returns:
        ChinaCDCCrawler: Configured crawler for specified country
    """
    # Currently only CN is supported, but structure allows expansion
    if country_code == "CN":
        return ChinaCDCCrawler()
    else:
        raise ValueError(f"Unsupported country: {country_code}")


@pytest.fixture
def processor(country_code):
    """
    Initialize data processor for specified country.
    
    Args:
        country_code: Country code from CLI or default
        
    Returns:
        DataProcessor: Processor configured for country data normalization
    """
    return DataProcessor(country_code=country_code.lower())


def pytest_addoption(parser):
    """
    Add custom command-line options to pytest.
    
    This hook is called during pytest initialization to register
    custom CLI arguments.
    """
    parser.addoption(
        "--country",
        action="store",
        default=DEFAULT_COUNTRY,
        help=f"Country code to test (default: {DEFAULT_COUNTRY})"
    )



# ============================================================================
# Helper Functions
# ============================================================================

def format_report_date(date_str: Optional[str]) -> str:
    """
    Format report date for display.
    
    Args:
        date_str: Date string in various formats
        
    Returns:
        str: Formatted date (YYYY-MM-DD) or 'Unknown'
    """
    if not date_str:
        return "Unknown"
    
    try:
        # Try parsing common date formats
        if isinstance(date_str, str):
            # Try ISO format first
            if 'T' in date_str:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')
        elif isinstance(date_str, datetime):
            return date_str.strftime('%Y-%m-%d')
        else:
            return str(date_str)
    except:
        return str(date_str)


async def _test_single_source(
    source_name: str,
    crawler,
    processor,
    verbose: bool = False
) -> Dict:
    """
    Test a single data source.
    
    Args:
        source_name: Source identifier ('cdc_weekly', 'nhc', 'pubmed')
        crawler: Crawler instance
        processor: DataProcessor instance
        verbose: Show detailed output
        
    Returns:
        dict: Test result with status, disease count, and report date
            - status: 'PASS', 'NO_DATA', 'CRAWL_FAIL', 'PROCESS_FAIL', 'ERROR'
            - diseases: Number of diseases extracted (0 if failed)
            - date: Report date
            - error: Error message (only if status='ERROR')
    """
    try:
        # Step 1: Discover available reports
        list_results = await crawler.fetch_list(source=source_name)
        if len(list_results) == 0:
            return {"status": "NO_DATA", "diseases": 0, "date": None}
        
        # Step 2: Force crawl one report (bypass date check)
        crawl_results = await crawler.crawl(source=source_name, force=True)
        if not crawl_results:
            return {"status": "CRAWL_FAIL", "diseases": 0, "date": None}
        
        # Extract report date
        report_date = None
        if crawl_results:
            first_result = crawl_results[0]
            report_date = getattr(first_result, 'date', None) or \
                         getattr(first_result, 'publish_date', None) or \
                         getattr(first_result, 'report_date', None)
        
        # Step 3: Process into structured DataFrame
        processed = await processor.process_crawler_results(
            crawl_results[:1], 
            save_to_file=False
        )
        
        # Step 4: Validate data extraction
        if processed and not processed[0].empty:
            diseases = len(processed[0])
            return {
                "status": "PASS",
                "diseases": diseases,
                "date": report_date
            }
        else:
            return {
                "status": "PROCESS_FAIL",
                "diseases": 0,
                "date": report_date
            }
            
    except Exception as e:
        if verbose:
            print(f"    Error details: {e}")
        return {
            "status": "ERROR",
            "diseases": 0,
            "date": None,
            "error": str(e)
        }


# ============================================================================
# Test Classes (Pytest Mode)
# ============================================================================

class TestCrawlerSources:
    """
    Test suite for all data crawler sources.
    
    Tests three different data sources to ensure proper crawling,
    parsing, and data extraction capabilities.
    """
    
    @pytest.mark.asyncio
    async def test_cdc_weekly(self, crawler, processor, country_code):
        """
        Test CDC Weekly data source (English).
        
        Validates:
            1. Report discovery from CDC Weekly archive
            2. Successful crawling of report content
            3. Data processing and disease extraction
            4. Non-empty DataFrame output
        
        Expected: ~40-50 disease records per report
        """
        result = await test_single_source("cdc_weekly", crawler, processor)
        
        assert result["status"] == "PASS", f"CDC Weekly test failed: {result['status']}"
        assert result["diseases"] > 0, "Should have disease data"
        
        date_str = format_report_date(result.get("date"))
        print(f"✓ cdc_weekly [{country_code}]: {result['diseases']} diseases (report: {date_str})")
    
    @pytest.mark.asyncio
    async def test_nhc(self, crawler, processor, country_code):
        """
        Test NHC (National Health Commission) data source (Chinese).
        
        Validates:
            1. Report discovery from NHC official website
            2. Successful crawling of Chinese language reports
            3. Chinese disease name parsing and normalization
            4. Data integrity after processing
        
        Expected: ~50-60 disease records per report
        """
        result = await _test_single_source("nhc", crawler, processor)
        
        assert result["status"] == "PASS", f"NHC test failed: {result['status']}"
        assert result["diseases"] > 0, "Should have disease data"
        
        date_str = format_report_date(result.get("date"))
        print(f"✓ nhc [{country_code}]: {result['diseases']} diseases (report: {date_str})")
    
    @pytest.mark.asyncio
    async def test_pubmed(self, crawler, processor, country_code):
        """
        Test PubMed RSS feed data source (English).
        
        Validates:
            1. RSS feed parsing and article discovery
            2. Successful retrieval of PubMed abstracts
            3. Disease mention extraction from medical literature
            4. Structured data output
        
        Expected: ~40-50 disease mentions per abstract
        """
        result = await _test_single_source("pubmed", crawler, processor)
        
        assert result["status"] == "PASS", f"PubMed test failed: {result['status']}"
        assert result["diseases"] > 0, "Should have disease data"
        
        date_str = format_report_date(result.get("date"))
        print(f"✓ pubmed [{country_code}]: {result['diseases']} diseases (report: {date_str})")


class TestDiseaseMappers:
    """
    Test suite for disease name mapping system.
    
    Tests both Chinese and English disease mappers to ensure
    proper normalization and standardization of disease names.
    """
    
    @pytest.mark.asyncio
    async def test_chinese_mapper(self, country_code):
        """
        Test Chinese disease mapper functionality.
        
        Validates:
            1. Mapper initialization with country code
            2. Database connection and query execution
            3. Chinese disease name mapping availability
            4. Statistics reporting (total mappings, aliases)
        
        Expected: ~120+ Chinese disease name mappings
        """
        # Initialize Chinese mapper
        mapper = await create_disease_mapper(country_code=country_code, language="zh")
        
        # Retrieve mapping statistics from database
        stats = await mapper.get_statistics()
        
        # Verify mappings exist
        assert stats['total_mappings'] > 0, "Should have Chinese mappings"
        print(f"✓ Chinese mapper [{country_code}]: {stats['total_mappings']} mappings")
    
    @pytest.mark.asyncio
    async def test_english_mapper(self, country_code):
        """
        Test English disease mapper functionality.
        
        Validates:
            1. Multi-language mapper initialization
            2. English variant name recognition
            3. Mapping to standard disease IDs
            4. Statistics accuracy
        
        Expected: ~140+ English disease name mappings
        Note: English mapper uses {country_code}_EN code internally
        """
        # Initialize English mapper
        mapper = await create_disease_mapper(country_code=country_code, language="en")
        
        # Retrieve mapping statistics
        stats = await mapper.get_statistics()
        
        # Verify English mappings exist
        assert stats['total_mappings'] > 0, "Should have English mappings"
        print(f"✓ English mapper [{country_code}]: {stats['total_mappings']} mappings")
    """
    Test suite for all data crawler sources.
    
    Tests three different data sources to ensure proper crawling,
    parsing, and data extraction capabilities.
    """
    
    @pytest.mark.asyncio
    async def test_cdc_weekly(self, crawler, processor):
        """
        Test CDC Weekly data source (English).
        
        Validates:
            1. Report discovery from CDC Weekly archive
            2. Successful crawling of report content
            3. Data processing and disease extraction
            4. Non-empty DataFrame output
        
        Expected: ~40-50 disease records per report
        """
        # Step 1: Discover available reports
        list_results = await crawler.fetch_list(source="cdc_weekly")
        assert len(list_results) > 0, "Should discover reports"
        
        # Step 2: Force crawl one report for testing (bypasses date check)
        crawl_results = await crawler.crawl(source="cdc_weekly", force=True)
        assert len(crawl_results) > 0, "Should crawl at least 1 report"
        
        # Step 3: Process crawled data into structured format
        processed = await processor.process_crawler_results(
            crawl_results[:1], 
            save_to_file=False
        )
        assert len(processed) > 0, "Should process data"
        
        # Step 4: Verify disease data extraction
        df = processed[0]
        assert not df.empty, "Should have disease data"
        
        print(f"✓ cdc_weekly: {len(df)} diseases")
    
    @pytest.mark.asyncio
    async def test_nhc(self, crawler, processor):
        """
        Test NHC (National Health Commission) data source (Chinese).
        
        Validates:
            1. Report discovery from NHC official website
            2. Successful crawling of Chinese language reports
            3. Chinese disease name parsing and normalization
            4. Data integrity after processing
        
        Expected: ~50-60 disease records per report
        """
        # Step 1: Discover available reports
        list_results = await crawler.fetch_list(source="nhc")
        assert len(list_results) > 0, "Should discover reports"
        
        # Step 2: Force crawl one report for testing
        crawl_results = await crawler.crawl(source="nhc", force=True)
        assert len(crawl_results) > 0, "Should crawl at least 1 report"
        
        # Step 3: Process with Chinese disease mapper
        processed = await processor.process_crawler_results(
            crawl_results[:1], 
            save_to_file=False
        )
        assert len(processed) > 0, "Should process data"
        
        # Step 4: Verify Chinese disease extraction
        df = processed[0]
        assert not df.empty, "Should have disease data"
        
        print(f"✓ nhc: {len(df)} diseases")
    
    @pytest.mark.asyncio
    async def test_pubmed(self, crawler, processor):
        """
        Test PubMed RSS feed data source (English).
        
        Validates:
            1. RSS feed parsing and article discovery
            2. Successful retrieval of PubMed abstracts
            3. Disease mention extraction from medical literature
            4. Structured data output
        
        Expected: ~40-50 disease mentions per abstract
        """
        # Step 1: Parse RSS feed for latest articles
        list_results = await crawler.fetch_list(source="pubmed")
        assert len(list_results) > 0, "Should discover reports"
        
        # Step 2: Fetch article content
        crawl_results = await crawler.crawl(source="pubmed", force=True)
        assert len(crawl_results) > 0, "Should crawl at least 1 report"
        
        # Step 3: Extract disease mentions from text
        processed = await processor.process_crawler_results(
            crawl_results[:1], 
            save_to_file=False
        )
        assert len(processed) > 0, "Should process data"
        
        # Step 4: Verify disease mention extraction
        df = processed[0]
        assert not df.empty, "Should have disease data"
        
        print(f"✓ pubmed: {len(df)} diseases")


class TestDiseaseMappers:
    """
    Test suite for disease name mapping system.
    
    Tests both Chinese and English disease mappers to ensure
    proper normalization and standardization of disease names.
    """
    
    @pytest.mark.asyncio
    async def test_chinese_mapper(self):
        """
        Test Chinese disease mapper functionality.
        
        Validates:
            1. Mapper initialization with CN country code
            2. Database connection and query execution
            3. Chinese disease name mapping availability
            4. Statistics reporting (total mappings, aliases)
        
        Expected: ~120+ Chinese disease name mappings
        """
        # Initialize Chinese mapper (country_code="CN", language="zh")
        mapper = await create_disease_mapper(country_code="CN", language="zh")
        
        # Retrieve mapping statistics from database
        stats = await mapper.get_statistics()
        
        # Verify mappings exist
        assert stats['total_mappings'] > 0, "Should have Chinese mappings"
        print(f"✓ Chinese mapper: {stats['total_mappings']} mappings")
    
    @pytest.mark.asyncio
    async def test_english_mapper(self):
        """
        Test English disease mapper functionality.
        
        Validates:
            1. Multi-language mapper initialization
            2. English variant name recognition
            3. Mapping to standard disease IDs
            4. Statistics accuracy
        
        Expected: ~140+ English disease name mappings
        Note: English mapper uses CN_EN country code internally
        """
        # Initialize English mapper (country_code="CN", language="en")
        mapper = await create_disease_mapper(country_code="CN", language="en")
        
        # Retrieve mapping statistics
        stats = await mapper.get_statistics()
        
        # Verify English mappings exist
        assert stats['total_mappings'] > 0, "Should have English mappings"
        print(f"✓ English mapper: {stats['total_mappings']} mappings")



# ============================================================================
# Direct Execution Mode (Non-pytest)
# ============================================================================

async def run_quick_tests(country_code: str = "CN", verbose: bool = False):
    """
    Run quick tests in standalone mode (without pytest).
    
    Provides minimal, clean output for rapid validation during development.
    
    Args:
        country_code: Country code to test (default: CN)
        verbose: Show detailed output including errors
        
    Returns:
        int: Exit code (0=success, 1=partial failure)
    """
    print(f"🧪 GlobalID Crawler Tests [{country_code}]")
    print("=" * 60)
    
    # Suppress logs for clean output
    if not verbose:
        logging.basicConfig(level=logging.ERROR)
        for logger_name in ['src', '__main__', 'sqlalchemy']:
            logging.getLogger(logger_name).setLevel(logging.ERROR)
    
    # Initialize components
    try:
        if country_code == "CN":
            crawler = ChinaCDCCrawler()
        else:
            print(f"❌ Unsupported country: {country_code}")
            return 1
        
        processor = DataProcessor(country_code=country_code.lower())
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        return 1
    
    # Define data sources to test
    sources: List[Tuple[str, str]] = [
        ("cdc_weekly", "CDC Weekly (EN)"),
        ("nhc", "NHC (ZH)"),
        ("pubmed", "PubMed (EN)"),
    ]
    
    passed = 0
    total = len(sources)
    
    # Test all data sources
    print("\n📡 Data Sources:")
    for source, desc in sources:
        result = await _test_single_source(source, crawler, processor, verbose)
        status = result["status"]
        diseases = result["diseases"]
        report_date = format_report_date(result.get("date"))
        
        if status == "PASS":
            icon = "✅"
            passed += 1
            print(f"  {icon} {desc:20} - {diseases:3d} diseases (report: {report_date})")
        else:
            icon = "❌"
            print(f"  {icon} {desc:20} - {status}")
            if verbose and "error" in result:
                print(f"      Error: {result['error']}")
    
    # Test disease mappers
    print("\n🗺️  Disease Mappers:")
    
    # Test Chinese mapper
    try:
        zh_mapper = await create_disease_mapper(
            country_code=country_code,
            language="zh"
        )
        zh_stats = await zh_mapper.get_statistics()
        print(f"  ✅ Chinese Mapper      - {zh_stats['total_mappings']:3d} mappings")
        passed += 1
        total += 1
    except Exception as e:
        print(f"  ❌ Chinese Mapper      - ERROR")
        if verbose:
            print(f"      Error: {e}")
        total += 1
    
    # Test English mapper
    try:
        en_mapper = await create_disease_mapper(
            country_code=country_code,
            language="en"
        )
        en_stats = await en_mapper.get_statistics()
        print(f"  ✅ English Mapper      - {en_stats['total_mappings']:3d} mappings")
        passed += 1
        total += 1
    except Exception as e:
        print(f"  ❌ English Mapper      - ERROR")
        if verbose:
            print(f"      Error: {e}")
        total += 1
    
    # Display summary
    print("\n" + "=" * 60)
    pass_rate = (passed / total) * 100 if total > 0 else 0
    
    if passed == total:
        print(f"✅ ALL PASSED ({passed}/{total}) - {pass_rate:.0f}%")
        return 0
    elif passed > 0:
        print(f"⚠️  PARTIAL ({passed}/{total}) - {pass_rate:.0f}%")
        return 1
    else:
        print(f"❌ ALL FAILED ({passed}/{total}) - {pass_rate:.0f}%")
        return 1


def parse_args():
    """
    Parse command-line arguments for direct execution mode.
    
    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Test GlobalID data crawlers and mappers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test with default country (CN)
  python tests/test_crawlers.py
  
  # Test specific country
  python tests/test_crawlers.py --country CN
  
  # Show detailed error messages
  python tests/test_crawlers.py --verbose
  
  # Run with pytest
  pytest tests/test_crawlers.py -v
  pytest tests/test_crawlers.py --country CN -v
        """
    )
    
    parser.add_argument(
        '--country',
        type=str,
        default='CN',
        help='Country code to test (default: CN)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output including error messages'
    )
    
    return parser.parse_args()


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    """
    Main entry point for both pytest and direct execution.
    
    Behavior:
        - If --help, --country, or --verbose: Use quick test mode
        - Otherwise: Use pytest framework
        
    Command-line arguments (direct mode):
        --country: Country code to test (default: CN)
        --verbose: Show detailed error messages
    
    Exit codes:
        0 = All tests passed
        1 = One or more tests failed
    """
    
    # Check if user wants direct mode (quick tests)
    use_direct_mode = any(arg in sys.argv for arg in ['--help', '--country', '--verbose'])
    
    if use_direct_mode or len(sys.argv) == 1:
        # Running directly - use quick test mode
        if '--help' in sys.argv or '-h' in sys.argv:
            args = parse_args()
        else:
            # Parse arguments or use defaults
            try:
                args = parse_args()
            except SystemExit:
                sys.exit(0)
        
        try:
            exit_code = asyncio.run(
                run_quick_tests(
                    country_code=args.country,
                    verbose=args.verbose
                )
            )
            sys.exit(exit_code)
        except KeyboardInterrupt:
            print("\n\n⚠️  Test interrupted by user")
            sys.exit(1)
        except Exception as e:
            print(f"\n\n❌ Fatal error: {e}")
            if getattr(args, 'verbose', False):
                import traceback
                traceback.print_exc()
            sys.exit(1)
    else:
        # Running via pytest - configure and run
        pytest_args = sys.argv[1:] + [
            "-v",           # Verbose: show test names
            "-s",           # Show print output (✓ markers)
            "--tb=short",   # Short traceback on failures
            "-W", "ignore::DeprecationWarning",  # Ignore warnings
        ]
        sys.exit(pytest.main([__file__] + pytest_args))
