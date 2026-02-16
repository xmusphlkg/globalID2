#!/usr/bin/env python3
"""
China Crawler and Data Processing Pipeline Test

测试中国爬虫和数据清洗过程，但不写入数据库
功能：
1. 检查是否有新数据需要爬取
2. 如果没有新数据，使用最新日期强制爬取进行测试
3. 完整的数据处理流程（解析、标准化、验证）
4. 详细的过程展示和统计信息
5. 可选择保存处理后的数据到文件

使用方法:
  # 常规测试（检查新数据）
  python tests/test_crawler_pipeline.py
  
  # 强制爬取测试（忽略数据库检查）
  python tests/test_crawler_pipeline.py --force
  
  # 只测试特定数据源
  python tests/test_crawler_pipeline.py --source cdc_weekly
  
  # 保存处理后的数据到文件
  python tests/test_crawler_pipeline.py --save-output
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
import pandas as pd

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.logging import setup_logging, get_logger
from src.core.database import get_db
from src.data.crawlers.cn_cdc import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.data.normalizers.disease_mapper_db import DiseaseMapperDB

console = Console()
logger = get_logger(__name__)


class CrawlerPipelineTester:
    """爬虫管道测试器"""
    
    def __init__(self, save_output: bool = False, output_dir: Path = None):
        """
        初始化测试器
        
        Args:
            save_output: 是否保存输出文件
            output_dir: 输出目录
        """
        self.crawler = ChinaCDCCrawler()
        self.processor = DataProcessor(
            output_dir=output_dir or Path("data/test_output"),
            country_code="cn"
        )
        self.save_output = save_output
        self.output_dir = output_dir or Path("data/test_output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 统计信息
        self.stats = {
            'discovered_total': 0,
            'discovered_new': 0,
            'processed_success': 0,
            'processed_failed': 0,
            'diseases_found': set(),
            'diseases_mapped': set(),
            'diseases_unmapped': set(),
            'data_sources': set(),
        }
    
    async def run_test(
        self,
        source: str = "all",
        force: bool = False,
        limit: int = 2
    ) -> Dict[str, Any]:
        """
        运行完整的爬虫管道测试
        
        Args:
            source: 数据源选择
            force: 是否强制爬取
            limit: 限制处理的记录数（避免测试时间过长）
        
        Returns:
            测试结果统计
        """
        console.print(Panel(
            f"🚀 China Crawler Pipeline Test\n\n"
            f"📍 Source: {source}\n"
            f"⚡ Force Mode: {'ON' if force else 'OFF'}\n"
            f"📊 Processing Limit: {limit} records per source\n"
            f"💾 Save Output: {'ON' if self.save_output else 'OFF'}",
            title="Test Configuration",
            border_style="blue"
        ))
        
        try:
            if source == "all":
                # 测试所有数据源
                sources_to_test = ["cdc_weekly", "nhc", "pubmed"]
                overall_results = {
                    'sources_tested': 0,
                    'sources_passed': 0,
                    'total_discovered': 0,
                    'total_processed': 0,
                    'source_results': {}
                }
                
                for test_source in sources_to_test:
                    console.print(f"\n[bold cyan]🔄 Testing Source: {test_source}[/bold cyan]")
                    
                    source_result = await self._test_single_source(test_source, force, limit)
                    overall_results['source_results'][test_source] = source_result
                    overall_results['sources_tested'] += 1
                    overall_results['total_discovered'] += source_result['discovered_total']
                    overall_results['total_processed'] += source_result['processed_success']
                    
                    if source_result['processed_success'] > 0:
                        overall_results['sources_passed'] += 1
                
                # 显示总体结果
                await self._show_overall_test_summary(overall_results)
                return overall_results
            else:
                # 测试单个数据源
                return await self._test_single_source(source, force, limit)
            
        except Exception as e:
            console.print(f"[bold red]❌ Test failed: {e}[/bold red]")
            logger.exception("Test execution failed")
            raise
    
    async def _test_single_source(self, source: str, force: bool, limit: int) -> Dict[str, Any]:
        """测试单个数据源"""
        # 重置统计信息
        self.stats = {
            'discovered_total': 0,
            'discovered_new': 0,
            'processed_success': 0,
            'processed_failed': 0,
            'diseases_found': set(),
            'diseases_mapped': set(),
            'diseases_unmapped': set(),
            'data_sources': set(),
        }
        
        # 第一步：数据发现阶段
        await self._test_data_discovery(source, force)
        
        # 第二步：数据处理阶段
        crawl_results = await self._get_test_data(source, force, limit)
        if crawl_results:
            await self._test_data_processing(crawl_results)
        
        return dict(self.stats)
    
    async def _test_data_discovery(self, source: str, force: bool):
        """测试数据发现阶段"""
        console.print("\n[bold blue]🔍 Phase 1: Data Discovery[/bold blue]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # 获取数据列表
            task1 = progress.add_task("Fetching data lists...", total=None)
            list_results = await self.crawler.fetch_list(source=source)
            self.stats['discovered_total'] = len(list_results)
            progress.update(task1, completed=1, description="✓ Data lists fetched")
            
            if not force:
                # 检查新数据
                task2 = progress.add_task("Checking for new data...", total=None)
                check_result = await self.crawler.check_new_data(list_results)
                new_results = check_result['new']
                existing_results = check_result['existing']
                self.stats['discovered_new'] = len(new_results)
                progress.update(task2, completed=1, description="✓ New data checked")
            else:
                new_results = list_results
                existing_results = []
                self.stats['discovered_new'] = len(new_results)
        
        # 显示发现结果
        discovery_table = Table(title="Data Discovery Results")
        discovery_table.add_column("Metric", style="cyan")
        discovery_table.add_column("Value", style="green")
        
        discovery_table.add_row("Total Reports Found", str(self.stats['discovered_total']))
        discovery_table.add_row("New Reports", str(self.stats['discovered_new']))
        discovery_table.add_row("Existing Reports", str(len(existing_results)))
        
        # 按数据源分组
        source_counts = {}
        for result in list_results:
            src = result.metadata.get('source', 'unknown')
            source_counts[src] = source_counts.get(src, 0) + 1
        
        for src, count in source_counts.items():
            discovery_table.add_row(f"  └─ {src}", str(count))
        
        console.print(discovery_table)
        
        # 如果没有新数据且不是强制模式，提供建议
        if self.stats['discovered_new'] == 0 and not force:
            console.print(Panel(
                "ℹ️  No new data found in database. Consider:\n"
                "• Use --force to test with latest available data\n"
                "• Check if crawler sources are working correctly",
                title="No New Data",
                border_style="yellow"
            ))
    
    async def _get_test_data(self, source: str, force: bool, limit: int):
        """获取测试数据"""
        # 获取爬虫结果
        results = await self.crawler.crawl(source=source, force=force)
        
        if not results:
            console.print("[yellow]⚠️  No data to process[/yellow]")
            return []
        
        # 限制测试数据量
        if len(results) > limit:
            console.print(f"[yellow]📊 Limiting test to {limit} records (found {len(results)})[/yellow]")
            results = results[:limit]
        
        return results
    
    async def _test_data_processing(self, crawl_results: List):
        """测试数据处理阶段"""
        console.print(f"\n[bold blue]⚙️  Phase 2: Data Processing ({len(crawl_results)} records)[/bold blue]")
        
        # 处理数据
        processed_data = await self.processor.process_crawler_results(
            crawl_results,
            save_to_file=self.save_output,
            save_raw=False,
        )
        
        self.stats['processed_success'] = len(processed_data)
        self.stats['processed_failed'] = len(crawl_results) - len(processed_data)
        
        # 分析处理后的数据
        for i, df in enumerate(processed_data):
            await self._analyze_processed_data(df, crawl_results[i])
        
        # 显示处理结果
        processing_table = Table(title="Data Processing Results")
        processing_table.add_column("Metric", style="cyan")
        processing_table.add_column("Value", style="green")
        
        processing_table.add_row("Input Records", str(len(crawl_results)))
        processing_table.add_row("Successfully Processed", str(self.stats['processed_success']))
        processing_table.add_row("Processing Failures", str(self.stats['processed_failed']))
        processing_table.add_row("Total Disease Names Found", str(len(self.stats['diseases_found'])))
        processing_table.add_row("Successfully Mapped", str(len(self.stats['diseases_mapped'])))
        processing_table.add_row("Unmapped Diseases", str(len(self.stats['diseases_unmapped'])))
        
        console.print(processing_table)
        
        # 显示数据源分布
        if self.stats['data_sources']:
            console.print(f"\n[bold]📊 Data Sources:[/bold] {', '.join(self.stats['data_sources'])}")
        
        # 显示未映射的疾病
        if self.stats['diseases_unmapped']:
            unmapped_table = Table(title="Unmapped Diseases", show_header=True)
            unmapped_table.add_column("Disease Name", style="red")
            
            for disease in sorted(self.stats['diseases_unmapped']):
                unmapped_table.add_row(disease)
            
            console.print(unmapped_table)
    
    async def _analyze_processed_data(self, df: pd.DataFrame, original_result):
        """分析处理后的数据"""
        # 记录数据源
        source = original_result.metadata.get('source', 'unknown')
        self.stats['data_sources'].add(source)
        
        # 分析疾病名称映射情况
        if 'DiseasesCN' in df.columns or 'Diseases' in df.columns:
            disease_col = 'DiseasesCN' if 'DiseasesCN' in df.columns else 'Diseases'
            diseases = df[disease_col].dropna().unique()
            
            for disease in diseases:
                self.stats['diseases_found'].add(disease)
                
                # 检查是否有映射
                if 'disease_id' in df.columns:
                    mapped_rows = df[df[disease_col] == disease]['disease_id'].notna()
                    if mapped_rows.any():
                        self.stats['diseases_mapped'].add(disease)
                    else:
                        self.stats['diseases_unmapped'].add(disease)
    
    async def _show_overall_test_summary(self, results: Dict[str, Any]):
        """显示所有数据源的测试总结"""
        console.print(f"\n[bold blue]📋 Overall Test Summary[/bold blue]")
        
        # 创建结果表格
        results_table = Table(title="Data Source Test Results")
        results_table.add_column("Source", style="cyan")
        results_table.add_column("Discovered", style="yellow")
        results_table.add_column("Processed", style="green")
        results_table.add_column("Status", style="bold")
        
        for source, source_result in results['source_results'].items():
            discovered = source_result['discovered_total']
            processed = source_result['processed_success']
            
            if processed > 0:
                status = "✅ PASS"
                status_style = "green"
            elif discovered > 0:
                status = "⚠️ PARTIAL"
                status_style = "yellow"
            else:
                status = "❌ FAIL"
                status_style = "red"
            
            results_table.add_row(
                source,
                str(discovered),
                str(processed),
                f"[{status_style}]{status}[/{status_style}]"
            )
        
        console.print(results_table)
        
        # 总体统计
        pass_rate = (results['sources_passed'] / results['sources_tested']) * 100
        
        summary_text = f"""
🔍 Sources Tested: {results['sources_tested']}
✅ Sources Passed: {results['sources_passed']} ({pass_rate:.1f}%)
📊 Total Reports: {results['total_discovered']}
⚙️ Total Processed: {results['total_processed']}
        """
        
        # 选择面板颜色
        if results['sources_passed'] == results['sources_tested']:
            border_style = "green"
            title = "✅ All Tests Passed"
        elif results['sources_passed'] > 0:
            border_style = "yellow" 
            title = "⚠️ Partial Success"
        else:
            border_style = "red"
            title = "❌ All Tests Failed"
        
        console.print(Panel(
            summary_text.strip(),
            title=title,
            border_style=border_style
        ))
    
    async def _show_test_summary(self):
        """显示单个测试总结"""
        console.print(f"\n[bold blue]📋 Test Summary[/bold blue]")
        
        # 总体成功率
        if self.stats['discovered_total'] > 0:
            discovery_rate = (self.stats['discovered_new'] / self.stats['discovered_total']) * 100
        else:
            discovery_rate = 0
        
        if len([r for r in [self.stats['processed_success'], self.stats['processed_failed']] if r > 0]) > 0:
            processing_rate = (self.stats['processed_success'] / 
                             (self.stats['processed_success'] + self.stats['processed_failed'])) * 100
        else:
            processing_rate = 0
        
        if self.stats['diseases_found']:
            mapping_rate = (len(self.stats['diseases_mapped']) / len(self.stats['diseases_found'])) * 100
        else:
            mapping_rate = 0
        
        # 创建总结面板
        summary_text = f"""
🔍 Data Discovery: {discovery_rate:.1f}% new data rate
⚙️ Data Processing: {processing_rate:.1f}% success rate  
🗺️ Disease Mapping: {mapping_rate:.1f}% mapping rate

📊 Key Metrics:
  • Reports discovered: {self.stats['discovered_total']}
  • New reports: {self.stats['discovered_new']}
  • Successfully processed: {self.stats['processed_success']}
  • Unique diseases: {len(self.stats['diseases_found'])}
  • Mapped diseases: {len(self.stats['diseases_mapped'])}
        """
        
        if self.save_output:
            summary_text += f"\n💾 Output saved to: {self.output_dir}"
        
        # 选择面板颜色
        if processing_rate >= 80 and mapping_rate >= 70:
            border_style = "green"
            title = "✅ Test Completed Successfully"
        elif processing_rate >= 60:
            border_style = "yellow" 
            title = "⚠️ Test Completed with Warnings"
        else:
            border_style = "red"
            title = "❌ Test Completed with Issues"
        
        console.print(Panel(
            summary_text.strip(),
            title=title,
            border_style=border_style
        ))

        # 数据库映射器统计
        try:
            from src.data.normalizers.english_mapper import create_disease_mapper
            
            # 显示中文映射器统计
            zh_mapper = await create_disease_mapper(language="zh")
            zh_stats = await zh_mapper.get_statistics()
            
            # 显示英文映射器统计
            en_mapper = await create_disease_mapper(language="en")
            en_stats = await en_mapper.get_statistics()
            
            mapping_table = Table(title="Disease Mapping Database Status")
            mapping_table.add_column("Metric", style="cyan")
            mapping_table.add_column("Chinese (ZH)", style="green")
            mapping_table.add_column("English (EN)", style="blue")
            
            mapping_table.add_row("Standard Diseases", str(zh_stats['standard_diseases']), str(en_stats['standard_diseases']))
            mapping_table.add_row("Total Mappings", str(zh_stats['total_mappings']), str(en_stats['total_mappings']))
            mapping_table.add_row("Primary Mappings", str(zh_stats['primary_mappings']), str(en_stats['primary_mappings']))
            mapping_table.add_row("Alias Mappings", str(zh_stats['alias_mappings']), str(en_stats['alias_mappings']))
            mapping_table.add_row("Pending Suggestions", str(zh_stats['pending_suggestions']), str(en_stats['pending_suggestions']))
            
            console.print(mapping_table)
            
            total_pending = zh_stats['pending_suggestions'] + en_stats['pending_suggestions']
            if total_pending > 0:
                console.print(Panel(
                    f"💡 Found {total_pending} pending disease mapping suggestions.\n"
                    "Run: python scripts/disease_cli.py suggestions",
                    title="Action Needed",
                    border_style="yellow"
                ))
        except Exception as e:
            console.print(f"[red]⚠️ Could not fetch mapping statistics: {e}[/red]")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Test China Crawler and Data Processing Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--source",
        choices=["all", "cdc_weekly", "nhc", "pubmed"],
        default="all",
        help="Data source to test (default: all)"
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force crawl mode (ignore database check)"
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=2,
        help="Limit number of records to process (default: 2)"
    )
    
    parser.add_argument(
        "--save-output",
        action="store_true",
        help="Save processed data to files"
    )
    
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/test_output"),
        help="Output directory for saved files (default: data/test_output)"
    )
    
    args = parser.parse_args()
    
    # 设置日志
    setup_logging()
    
    # 运行测试
    tester = CrawlerPipelineTester(
        save_output=args.save_output,
        output_dir=args.output_dir
    )
    
    try:
        results = await tester.run_test(
            source=args.source,
            force=args.force,
            limit=args.limit
        )
        
        # 判断测试结果
        if args.source == "all":
            # 测试所有数据源的情况
            if results['sources_passed'] == 0:
                console.print("[red]❌ No data sources passed testing[/red]")
                return 1
            elif results['sources_passed'] < results['sources_tested']:
                console.print("[yellow]⚠️ Some data sources failed testing[/yellow]")
                return 0  # 部分成功，仍然返回0
            else:
                console.print("[green]✅ All data sources passed testing[/green]")
                return 0
        else:
            # 测试单个数据源的情况
            if results['processed_success'] == 0:
                console.print("[red]❌ No data was successfully processed[/red]")
                return 1
            else:
                return 0
        
    except Exception as e:
        console.print(f"[bold red]❌ Test failed: {e}[/bold red]")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)