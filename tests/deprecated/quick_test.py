#!/usr/bin/env python3
"""
Quick China Crawler Test Script

快速测试中国爬虫管道的简化版本，仅测试核心功能
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
from rich import print as rprint

logger = get_logger(__name__)


async def quick_test():
    """快速测试爬虫管道"""
    
    rprint("[bold blue]🚀 Quick Crawler Test[/bold blue]")
    
    # 初始化组件
    crawler = ChinaCDCCrawler()
    processor = DataProcessor(country_code="cn")
    
    # 测试1: 检查新数据
    rprint("\n[yellow]📡 Checking for new data...[/yellow]")
    list_results = await crawler.fetch_list(source="nhc")
    rprint(f"Found {len(list_results)} reports")
    
    if list_results:
        check_result = await crawler.check_new_data(list_results)
        new_count = len(check_result['new'])
        existing_count = len(check_result['existing'])
        
        rprint(f"  ✓ New: {new_count}")
        rprint(f"  ✓ Existing: {existing_count}")
        
        # 测试2: 处理一条数据（强制模式）
        if new_count > 0 or existing_count > 0:
            rprint(f"\n[yellow]⚙️  Processing 1 record...[/yellow]")
            
            # 获取一条数据进行测试
            test_data = check_result['new'][:1] or check_result['existing'][:1]
            
            # 处理数据
            processed = await processor.process_crawler_results(
                test_data, 
                save_to_file=False
            )
            
            if processed:
                df = processed[0]
                rprint(f"  ✅ Success: {len(df)} rows processed")
                rprint(f"  📊 Columns: {len(df.columns)}")
                
                # 显示前3行数据
                if len(df) > 0:
                    rprint("\n[cyan]Sample data:[/cyan]")
                    for i in range(min(3, len(df))):
                        disease = df.iloc[i].get('DiseasesCN', df.iloc[i].get('Diseases', 'Unknown'))
                        cases = df.iloc[i].get('Cases', 'N/A')
                        rprint(f"  {i+1}. {disease}: {cases} cases")
            else:
                rprint("  ❌ Processing failed")
    else:
        rprint("  ❌ No data found")
    
    rprint("\n[green]✅ Quick test completed[/green]")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(quick_test())