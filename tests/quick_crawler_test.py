#!/usr/bin/env python3
"""
Quick Crawler Test - Minimalist Version

Ultra-minimal test output showing only essential results with no verbose logging.
Designed for rapid validation during development.

Usage:
    python tests/quick_crawler_test.py
    
Output Example:
    🧪 GlobalID Crawler Tests
    ==================================================
    
    📡 Data Sources:
      ✅ CDC Weekly (EN)      - 46 diseases
      ✅ NHC (ZH)             - 56 diseases
      ✅ PubMed (EN)          - 46 diseases
    
    🗺️  Disease Mappers:
      ✅ Chinese Mapper      - 121 mappings
      ✅ English Mapper      - 151 mappings
    
    ==================================================
    ✅ ALL PASSED (5/5) - 100%

Features:
    - Minimal output (18 lines total)
    - Fast execution (~8 seconds)
    - Clear visual indicators (✅/❌)
    - ERROR-level logging only
    - No database INFO logs
"""
import asyncio
import sys
import logging
from pathlib import Path

# Add project root to Python path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Disable all INFO-level logging for clean output
logging.basicConfig(level=logging.ERROR)
for logger_name in ['src', '__main__', 'sqlalchemy']:
    logging.getLogger(logger_name).setLevel(logging.ERROR)

from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.data.normalizers.english_mapper import create_disease_mapper


async def test_source(source_name: str, crawler, processor) -> dict:
    """
    Test a single data source.
    
    Args:
        source_name: Source identifier ('cdc_weekly', 'nhc', 'pubmed')
        crawler: ChinaCDCCrawler instance
        processor: DataProcessor instance
    
    Returns:
        dict: Test result with status and disease count
            - status: 'PASS', 'NO_DATA', 'CRAWL_FAIL', 'PROCESS_FAIL', 'ERROR'
            - diseases: Number of diseases extracted (0 if failed)
            - error: Error message (only if status='ERROR')
    """
    try:
        # Step 1: Discover available reports
        list_results = await crawler.fetch_list(source=source_name)
        if len(list_results) == 0:
            return {"status": "NO_DATA", "diseases": 0}
        
        # Step 2: Force crawl one report (bypass date check)
        crawl_results = await crawler.crawl(source=source_name, force=True)
        if not crawl_results:
            return {"status": "CRAWL_FAIL", "diseases": 0}
        
        # Step 3: Process into structured DataFrame
        processed = await processor.process_crawler_results(
            crawl_results[:1], 
            save_to_file=False
        )
        
        # Step 4: Validate data extraction
        if processed and not processed[0].empty:
            diseases = len(processed[0])
            return {"status": "PASS", "diseases": diseases}
        else:
            return {"status": "PROCESS_FAIL", "diseases": 0}
            
    except Exception as e:
        return {"status": "ERROR", "diseases": 0, "error": str(e)}


async def main():
    """
    Main test execution flow.
    
    Tests all three data sources and both disease mappers,
    displaying results in a clean, minimal format.
    
    Process:
        1. Initialize crawler and processor
        2. Test each data source (CDC Weekly, NHC, PubMed)
        3. Validate disease mappers (Chinese, English)
        4. Display summary with pass rate
    
    Returns:
        int: Exit code (0=success, 1=partial failure)
    """
    print("🧪 GlobalID Crawler Tests")
    print("=" * 50)
    
    # Initialize components
    crawler = ChinaCDCCrawler()
    processor = DataProcessor(country_code="cn")
    
    # Define data sources to test
    sources = [
        ("cdc_weekly", "CDC Weekly (EN)"),
        ("nhc", "NHC (ZH)"),
        ("pubmed", "PubMed (EN)"),
    ]
    
    passed = 0
    total = len(sources)
    
    # Test all data sources
    print("\n📡 Data Sources:")
    for source, desc in sources:
        result = await test_source(source, crawler, processor)
        status = result["status"]
        diseases = result["diseases"]
        
        if status == "PASS":
            icon = "✅"
            passed += 1
            print(f"  {icon} {desc:20} - {diseases} diseases")
        else:
            icon = "❌"
            print(f"  {icon} {desc:20} - {status}")
    
    # Test disease mappers
    print("\n🗺️  Disease Mappers:")
    
    # Test Chinese mapper
    try:
        zh_mapper = await create_disease_mapper(country_code="CN", language="zh")
        zh_stats = await zh_mapper.get_statistics()
        print(f"  ✅ Chinese Mapper      - {zh_stats['total_mappings']} mappings")
        passed += 1
        total += 1
    except Exception as e:
        print(f"  ❌ Chinese Mapper      - ERROR")
        total += 1
    
    # Test English mapper
    try:
        en_mapper = await create_disease_mapper(country_code="CN", language="en")
        en_stats = await en_mapper.get_statistics()
        print(f"  ✅ English Mapper      - {en_stats['total_mappings']} mappings")
        passed += 1
        total += 1
    except Exception as e:
        print(f"  ❌ English Mapper      - ERROR")
        total += 1
    
    # Display summary
    print("\n" + "=" * 50)
    pass_rate = (passed / total) * 100
    
    if passed == total:
        print(f"✅ ALL PASSED ({passed}/{total}) - {pass_rate:.0f}%")
        return 0
    else:
        print(f"⚠️  PARTIAL  ({passed}/{total}) - {pass_rate:.0f}%")
        return 1


if __name__ == "__main__":
    """
    Entry point for direct script execution.
    
    Handles graceful shutdown on keyboard interrupt (Ctrl+C).
    Exits with appropriate status code for CI/CD integration.
    """
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted")
        sys.exit(1)
