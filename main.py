"""
GlobalID V2 Main Entry Point

主入口：运行完整的数据爬取 → 分析 → 报告生成流程
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress
from sqlalchemy import select

from src.core import get_config, get_database, get_logger, init_app
from src.domain import Country, Disease, DiseaseRecord, ReportType, CrawlRun
from src.data.crawlers import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.generation import ReportGenerator

app = typer.Typer(help="GlobalID V2 - Global Infectious Disease Monitoring System")
console = Console()
logger = get_logger(__name__)

@app.command()
def crawl(
    country: str = typer.Option("CN", help="Country code"),
    source: str = typer.Option("all", help="Data source (cdc_weekly/nhc/pubmed/all)"),
    process: bool = typer.Option(True, help="Process and store data"),
    save_raw: bool = typer.Option(True, help="Save raw pages as plain text"),
    force: bool = typer.Option(False, help="Force crawl all data (ignore database check)"),
):
    """
    智能爬取疾病数据
    
    工作流程：
    1. 获取数据列表（轻量级）
    2. 与数据库对比，识别新数据
    3. 只爬取新数据的详细内容（重量级）
    """
    run_id = None
    raw_dir = None

    async def _crawl():
        nonlocal run_id, raw_dir
        await init_app()
        
        # 标准化国家代码为大写
        country_code = country.upper()
        
        console.print(f"[bold blue]🚀 Starting intelligent data crawl for {country_code}...[/bold blue]")
        if force:
            console.print("[yellow]⚠️  Force mode: will crawl all data (ignoring database)[/yellow]")
        
        # 获取爬虫
        if country_code == "CN":
            crawler = ChinaCDCCrawler()
        else:
            console.print(f"[red]Unsupported country: {country_code}[/red]")
            console.print(f"[yellow]Available countries: CN[/yellow]")
            return
        
        run_id = None
        raw_dir = None
        try:
            raw_dir = Path("data/raw") / country_code.lower()
            async with get_database() as db:
                run = CrawlRun(
                    country_code=country_code,
                    source=source,
                    status="running",
                    started_at=datetime.now(),
                    raw_dir=str(raw_dir) if save_raw else None,
                    metadata_={"force": force, "process": process},
                )
                db.add(run)
                await db.flush()
                run_id = run.id
        except Exception as e:
            logger.warning(f"无法创建爬取运行记录: {e}")

        # 智能爬取（三阶段）
        console.print(f"\n[bold cyan]Phase 1/3: Fetching data list...[/bold cyan]")
        results = await crawler.crawl(source=source, force=force)
        
        if not results:
            if run_id:
                async with get_database() as db:
                    run = await db.get(CrawlRun, run_id)
                    if run:
                        run.status = "completed"
                        run.finished_at = datetime.now()
                        run.new_reports = 0
                        run.processed_reports = 0
                        run.total_records = 0
            console.print(f"[yellow]✓ No new data to crawl[/yellow]")
            return
        
        console.print(f"[green]✓ Found {len(results)} new reports to process[/green]")
        
        # 显示预览
        console.print(f"\n[bold]New reports:[/bold]")
        for i, result in enumerate(results[:10], 1):
            date_str = result.date.strftime("%Y-%m") if result.date else "Unknown"
            console.print(f"  {i}. {result.year_month} - {result.title[:80]}...")
        
        if len(results) > 10:
            console.print(f"  ... and {len(results) - 10} more")
        
        # 处理数据
        total_records = 0
        processed = []
        if process and results:
            console.print(f"\n[bold cyan]Phase 2/3: Processing new data...[/bold cyan]")
            
            from src.data.processors import DataProcessor
            
            processor = DataProcessor(
                output_dir=Path("data/processed") / country_code.lower(),
                country_code=country_code.lower()
            )
            
            with Progress() as progress:
                task = progress.add_task("[cyan]Processing...", total=len(results))
                
                processed = await processor.process_crawler_results(
                    results,
                    save_to_file=True,
                    save_raw=save_raw,
                    crawl_run_id=run_id,
                    raw_dir=raw_dir,
                )
                progress.update(task, advance=len(results))
            
            console.print(f"[green]✓ Processed {len(processed)}/{len(results)} datasets[/green]")
            
            # 统计信息
            if processed:
                total_records = sum(len(df) for df in processed)
                console.print(f"[green]✓ Total records: {total_records}[/green]")
        
        # 仅保存原始文本（不处理数据）
        if save_raw and results and not process:
            console.print(f"\n[bold cyan]Phase 3/3: Saving raw data...[/bold cyan]")
            from src.data.processors import DataProcessor

            raw_dir = raw_dir or Path("data/raw") / country_code.lower()
            processor = DataProcessor(
                output_dir=Path("data/processed") / country_code.lower(),
                country_code=country_code.lower(),
            )
            saved = await processor.save_raw_pages(
                results,
                crawl_run_id=run_id,
                raw_dir=raw_dir,
            )
            console.print(f"[green]✓ Saved {saved} raw pages to {raw_dir}[/green]")

        if run_id:
            async with get_database() as db:
                run = await db.get(CrawlRun, run_id)
                if run:
                    run.status = "completed"
                    run.finished_at = datetime.now()
                    run.new_reports = len(results)
                    run.processed_reports = len(processed) if process else 0
                    run.total_records = total_records if process and processed else 0
        
        console.print(f"\n[bold green]✨ Crawl completed successfully![/bold green]")
    
    async def _crawl_with_error_handling():
        try:
            await _crawl()
        except Exception as e:
            if run_id:
                async with get_database() as db:
                    run = await db.get(CrawlRun, run_id)
                    if run:
                        run.status = "failed"
                        run.finished_at = datetime.now()
                        run.error_message = str(e)
            raise

    asyncio.run(_crawl_with_error_handling())


@app.command()
def generate_report(
    country: str = typer.Option("CN", help="Country code"),
    report_type: str = typer.Option("weekly", help="Report type (daily/weekly/monthly)"),
    days: int = typer.Option(7, help="Number of days to include"),
    send_email: bool = typer.Option(False, help="Send report via email"),
):
    """
    生成疾病监测报告
    """
    async def _generate():
        await init_app()
        
        console.print(f"[bold blue]Generating {report_type} report for {country}...[/bold blue]")
        
        async with get_database() as db:
            # 获取国家
            country_query = select(Country).where(Country.code == country)
            country_result = await db.execute(country_query)
            country_obj = country_result.scalar_one_or_none()
            
            if not country_obj:
                console.print(f"[red]Country not found: {country}[/red]")
                return
            
            # 设置时间范围
            period_end = datetime.now()
            period_start = period_end - timedelta(days=days)
            
            # 获取报告类型
            report_type_enum = ReportType[report_type.upper()]
            
            # 生成报告
            generator = ReportGenerator()
            
            with Progress() as progress:
                task = progress.add_task("[cyan]Generating report...", total=100)
                
                report = await generator.generate(
                    country_id=country_obj.id,
                    report_type=report_type_enum,
                    period_start=period_start,
                    period_end=period_end,
                    send_email=send_email,
                )
                
                progress.update(task, advance=100)
            
            console.print(f"[green]✓ Report generated successfully![/green]")
            console.print(f"  ID: {report.id}")
            console.print(f"  Status: {report.status}")
            
            if report.markdown_path:
                console.print(f"  Markdown: {report.markdown_path}")
            if report.html_path:
                console.print(f"  HTML: {report.html_path}")
            if report.pdf_path:
                console.print(f"  PDF: {report.pdf_path}")
    
    asyncio.run(_generate())


@app.command()
def init_database():
    """
    初始化数据库
    """
    async def _init():
        await init_app()
        console.print("[bold blue]Initializing database...[/bold blue]")
        
        from src.domain import Base
        from src.core import get_engine
        
        engine = get_engine()
        
        # 创建所有表
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        console.print("[green]✓ Database initialized[/green]")
        
        # 创建初始国家数据（不创建疾病测试数据）
        console.print("[blue]Creating initial country data...[/blue]")
        
        # 创建初始数据（使用会话上下文）
        async with get_database() as db:
            # 创建中国
            country_query = select(Country).where(Country.code == "CN")
            country_result = await db.execute(country_query)
            country = country_result.scalar_one_or_none()

            if not country:
                country = Country(
                    code="CN",
                    name="China",
                    name_en="China",
                    language="zh",
                    timezone="Asia/Shanghai",
                    data_source_url="http://weekly.chinacdc.cn",
                    crawler_config={
                        "sources": ["cdc_weekly", "nhc", "pubmed_rss"],
                    },
                )
                db.add(country)
                await db.commit()
                console.print("  ✓ Created country: China")
            else:
                console.print("  ✓ Country China already exists")

            await db.commit()
            console.print("[green]✓ Initial country data ready[/green]")
    
    asyncio.run(_init())


@app.command()
def export_data(
    country: str = typer.Option("CN", help="Country code"),
    output_format: str = typer.Option("csv", help="Output format (csv/excel/json/all)"),
    period: str = typer.Option("latest", help="Period (latest/all/YYYY-MM)"),
    package: bool = typer.Option(False, help="Create ZIP package"),
):
    """
    导出整理好的数据文件
    """
    async def _export():
        await init_app()
        
        from src.generation import DataExporter
        
        console.print(f"[bold blue]Exporting data for {country}...[/bold blue]")
        
        exporter = DataExporter()
        
        # 解析格式
        if output_format == 'all':
            formats = ['csv', 'excel', 'json']
        else:
            formats = [output_format]
        
        # 导出数据
        if package:
            # 创建数据包
            zip_path = await exporter.create_data_package(
                country_code=country,
                include_all=(period == 'all'),
                include_latest=True,
            )
            console.print(f"[green]✓ Data package created: {zip_path}[/green]")
        else:
            if period == 'latest':
                files = await exporter.export_latest(country, formats=formats)
            elif period == 'all':
                files = await exporter.export_all(country, formats=formats)
            else:
                # 解析YYYY-MM
                try:
                    year, month = map(int, period.split('-'))
                    files = await exporter.export_monthly(country, year, month, formats=formats)
                except:
                    console.print(f"[red]Invalid period format: {period}[/red]")
                    return
            
            console.print(f"[green]✓ Exported {len(files)} files:[/green]")
            for fmt, path in files.items():
                console.print(f"  - {fmt.upper()}: {path}")
    
    asyncio.run(_export())


@app.command()
def test():
    """
    运行集成测试
    """
    console.print("[bold blue]Running integration tests...[/bold blue]")
    
    # 运行测试
    from tests.test_integration import main as test_main
    
    exit_code = asyncio.run(test_main())
    
    if exit_code == 0:
        console.print("[green]✓ All tests passed![/green]")
    else:
        console.print("[red]✗ Some tests failed[/red]")
    
    sys.exit(exit_code)


@app.command()
def run(
    full: bool = typer.Option(False, help="Run full pipeline (crawl + generate)"),
    force: bool = typer.Option(False, help="Skip data update and use latest available data"),
):
    """
    运行完整流程
    """
    async def _run():
        await init_app()
        
        if full:
            console.print("[bold blue]Running full pipeline...[/bold blue]")
            
            # 1. 爬取数据（除非force=True）
            period_start = None
            period_end = None
            
            if not force:
                console.print("\n[cyan]Step 1: Crawling data[/cyan]")
                period_start, period_end = await _crawl()
            else:
                console.print("\n[yellow]Step 1: Skipping data crawl (force mode)[/yellow]")
                # 获取数据库中最新的数据时间
                period_start, period_end = await _get_latest_data_period()
                if period_start and period_end:
                    console.print(f"  Using latest data period: {period_start.date()} to {period_end.date()}")
                else:
                    console.print("[red]No data found in database. Please run without --force first.[/red]")
                    return
            
            # 2. 生成报告（基于爬取到的数据时间范围）
            console.print("\n[cyan]Step 2: Generating report[/cyan]")
            await _generate(period_start, period_end)
            
            console.print("\n[green]✓ Pipeline completed![/green]")
        else:
            console.print("[yellow]Use --full to run the complete pipeline[/yellow]")
    
    async def _get_latest_data_period():
        """从数据库获取最新的数据时间范围"""
        from sqlalchemy import text
        async with get_database() as db:
            result = await db.execute(text("""
                SELECT MIN(time) as min_time, MAX(time) as max_time
                FROM disease_records
                WHERE country_id = (SELECT id FROM countries WHERE code = 'CN')
            """))
            row = result.fetchone()
            if row and row[0] and row[1]:
                return row[0], row[1]
        return None, None
    
    async def _crawl():
        """爬取新数据、处理并保存到数据库，返回数据时间范围"""
        from src.data.processors import DataProcessor
        
        crawler = ChinaCDCCrawler()
        # First try normal crawl
        results = await crawler.crawl(source="all", force=False)
        
        # If no new data found, force crawl for pipeline testing
        if not results:
            console.print("   No new data found, using force mode for testing...")
            results = await crawler.crawl(source="all", force=True)
        
        console.print(f"  Fetched {len(results)} results")
        
        # 处理数据并保存到数据库
        if results:
            processor = DataProcessor(
                output_dir=Path("data/processed") / "cn",
                country_code="cn"
            )
            
            processed = await processor.process_crawler_results(
                results,
                save_to_file=True,
                save_raw=True,
                crawl_run_id=None,  # No need for crawl run tracking in auto mode
                raw_dir=Path("data/raw/cn"),
            )
            console.print(f"  Processed {len(processed)} datasets with {sum(len(df) for df in processed)} total records")
        
        # 提取数据时间范围
        dates = [r.date for r in results if r.date]
        if dates:
            min_date = min(dates)
            max_date = max(dates)
            return min_date, max_date
        return None, None
    
    async def _generate(period_start=None, period_end=None):
        """生成报告，如果没有指定时间范围，则使用最近90天"""
        async with get_database() as db:
            country_query = select(Country).where(Country.code == "CN")
            country_result = await db.execute(country_query)
            country = country_result.scalar_one()

            # 如果没有指定时间范围，使用最近90天的数据
            if period_start is None or period_end is None:
                period_end = datetime.now()
                period_start = period_end - timedelta(days=90)
                console.print(f"  Using default time range: last 90 days")
            else:
                console.print(f"  Using data time range: {period_start.date()} to {period_end.date()}")

            generator = ReportGenerator()
            report = await generator.generate(
                country_id=country.id,
                report_type=ReportType.WEEKLY,
                period_start=period_start,
                period_end=period_end,
            )

            console.print(f"  Report generated: {report.id}")
    
    asyncio.run(_run())


if __name__ == "__main__":
    app()
