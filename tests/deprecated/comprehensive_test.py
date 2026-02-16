#!/usr/bin/env python3
"""
comprehensive china crawler test

完整的中国爬虫管道测试 - 测试所有数据源和映射器
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.logging import setup_logging, get_logger
from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.data.normalizers.english_mapper import create_disease_mapper
from rich import print as rprint
from rich.table import Table
from rich.panel import Panel

logger = get_logger(__name__)


async def test_all_sources():
    """测试所有数据源"""
    
    rprint(Panel(
        "🚀 Complete China Crawler Pipeline Test\n"
        "Testing all data sources with appropriate mappers",
        title="Comprehensive Test",
        border_style="blue"
    ))
    
    # 初始化组件
    crawler = ChinaCDCCrawler()
    processor = DataProcessor(country_code="cn")
    
    sources = [
        ("cdc_weekly", "英文数据源 (CDC Weekly)", "en"),
        ("nhc", "中文数据源 (国家疾控局)", "zh"),
        ("pubmed", "英文数据源 (PubMed RSS)", "en"),
    ]
    
    results = {}
    
    for source, description, language in sources:
        rprint(f"\n[bold cyan]📡 Testing: {source} - {description}[/bold cyan]")
        
        try:
            # 获取数据列表
            list_results = await crawler.fetch_list(source=source)
            discovered = len(list_results)
            
            if discovered == 0:
                rprint(f"  ⚠️ No data found for {source}")
                results[source] = {"discovered": 0, "processed": 0, "status": "NO_DATA"}
                continue
            
            # 强制获取1条数据进行测试
            crawl_results = await crawler.crawl(source=source, force=True)
            if crawl_results:
                test_data = crawl_results[:1]
                
                # 处理数据
                processed = await processor.process_crawler_results(
                    test_data, 
                    save_to_file=False
                )
                
                processed_count = len(processed)
                if processed_count > 0:
                    df = processed[0]
                    diseases_found = len(df) if not df.empty else 0
                    rprint(f"  ✅ Success: {diseases_found} disease records processed")
                    results[source] = {"discovered": discovered, "processed": processed_count, "status": "PASS", "diseases": diseases_found}
                else:
                    rprint(f"  ❌ Processing failed")
                    results[source] = {"discovered": discovered, "processed": 0, "status": "PROCESS_FAIL"}
            else:
                rprint(f"  ⚠️ No crawl results")
                results[source] = {"discovered": discovered, "processed": 0, "status": "CRAWL_FAIL"}
                
        except Exception as e:
            rprint(f"  ❌ Error: {e}")
            logger.exception(f"Test failed for {source}")
            results[source] = {"discovered": 0, "processed": 0, "status": "ERROR"}
    
    # 显示结果表格
    rprint("\n[bold blue]📋 Test Summary[/bold blue]")
    
    table = Table(title="Data Source Test Results")
    table.add_column("Source", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Discovered", style="blue")
    table.add_column("Processed", style="green")
    table.add_column("Status", style="bold")
    
    passed = 0
    total = len(sources)
    
    for (source, description, language), result in zip(sources, results.values()):
        mapper_type = "英文映射器" if language == "en" else "中文映射器"
        discovered = result["discovered"]
        processed = result["processed"]
        status = result["status"]
        
        if status == "PASS":
            status_display = "✅ PASS"
            passed += 1
        elif status == "NO_DATA":
            status_display = "ℹ️ NO_DATA"
        else:
            status_display = "❌ FAIL"
        
        table.add_row(
            source,
            mapper_type,
            str(discovered),
            str(processed),
            status_display
        )
    
    rprint(table)
    
    # 总体结果
    pass_rate = (passed / total) * 100
    
    if passed == total:
        panel_style = "green"
        panel_title = "✅ All Tests Passed"
    elif passed > 0:
        panel_style = "yellow"
        panel_title = "⚠️ Partial Success"  
    else:
        panel_style = "red"
        panel_title = "❌ All Tests Failed"
    
    summary = f"""
🔍 Sources Tested: {total}
✅ Sources Passed: {passed} ({pass_rate:.1f}%)
📊 Total Reports: {sum(r['discovered'] for r in results.values())}
⚙️ Total Processed: {sum(r['processed'] for r in results.values())}

💡 Both Chinese and English disease mappers are working!
    """
    
    rprint(Panel(
        summary.strip(),
        title=panel_title,
        border_style=panel_style
    ))
    
    return passed == total


async def test_mapper_statistics():
    """测试映射器统计信息"""
    rprint("\n[bold blue]📊 Disease Mapper Statistics[/bold blue]")
    
    try:
        # 中文映射器
        zh_mapper = await create_disease_mapper(country_code="CN", language="zh")
        zh_stats = await zh_mapper.get_statistics()
        
        # 英文映射器  
        en_mapper = await create_disease_mapper(country_code="CN", language="en")
        en_stats = await en_mapper.get_statistics()
        
        table = Table(title="Disease Mapping Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Chinese (ZH)", style="green")
        table.add_column("English (EN)", style="blue")
        
        table.add_row("Standard Diseases", str(zh_stats['standard_diseases']), str(en_stats['standard_diseases']))
        table.add_row("Total Mappings", str(zh_stats['total_mappings']), str(en_stats['total_mappings']))
        table.add_row("Primary Mappings", str(zh_stats['primary_mappings']), str(en_stats['primary_mappings']))
        table.add_row("Alias Mappings", str(zh_stats['alias_mappings']), str(en_stats['alias_mappings']))
        
        rprint(table)
        
        total_pending = zh_stats.get('pending_suggestions', 0) + en_stats.get('pending_suggestions', 0)
        if total_pending > 0:
            rprint(Panel(
                f"💡 Found {total_pending} pending suggestions across both mappers.\n"
                "Run: python scripts/disease_cli.py suggestions",
                title="Action Needed",
                border_style="yellow"
            ))
        
    except Exception as e:
        rprint(f"[red]⚠️ Could not fetch mapper statistics: {e}[/red]")


async def main():
    """主函数"""
    setup_logging()
    
    try:
        # 测试所有数据源
        all_passed = await test_all_sources()
        
        # 显示映射器统计
        await test_mapper_statistics()
        
        if all_passed:
            rprint("\n[green]🎉 All systems operational![/green]")
            return 0
        else:
            rprint("\n[yellow]⚠️ Some issues detected[/yellow]")
            return 1
            
    except Exception as e:
        rprint(f"\n[red]❌ Test execution failed: {e}[/red]")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)