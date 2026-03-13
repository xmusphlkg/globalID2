"""
GlobalID V2 Main Entry Point

主入口：运行完整的数据爬取 → 分析 → 报告生成流程
"""
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress
from sqlalchemy import select, func

from src.core import get_config, get_database, get_logger, init_app
from src.core.task_manager import task_manager
from src.domain import Country, Disease, DiseaseRecord, ReportType, ReportStatus, CrawlRun, TaskType, TaskPriority, TaskStatus, Task
from src.data.crawlers import ChinaCDCCrawler
from src.data.processors import DataProcessor
from src.generation import ReportGenerator

app = typer.Typer(help="GlobalID V2 - Global Infectious Disease Monitoring System")
console = Console()
logger = get_logger(__name__)

DEBUG_LOG_PATH = Path(".cursor/debug-1117ae.log")


def _agent_debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    """
    Lightweight NDJSON logger for this debug session.
    Writes to .cursor/debug-1117ae.log and never raises.
    """
    payload = {
        "sessionId": "1117ae",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Never let debug logging break the main flow
        return


@app.command()
def crawl(
    country: str = typer.Option("CN", help="Country code"),
    source: str = typer.Option("all", help="Data source (cdc_weekly/nhc/pubmed/all)"),
    process: bool = typer.Option(True, help="Process and store data"),
    save_raw: bool = typer.Option(True, help="Save raw pages as plain text"),
    force: bool = typer.Option(False, help="Force crawl all data (ignore database check)"),
    fill_missing: bool = typer.Option(
        True,
        help="Also backfill missing months found in the source list (non-force).",
    ),
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
    task = None

    async def _crawl():
        nonlocal run_id, raw_dir, task
        await init_app()
        
        # 标准化国家代码为大写
        country_code = country.upper()
        
        # Create task record
        task_name = f"Crawl {country_code} Data ({source})"
        task_description = f"Source: {source}, Force: {'Yes' if force else 'No'}, Process: {'Yes' if process else 'No'}"
        
        task = await task_manager.create_task(
            task_type=TaskType.CRAWL_DATA,
            task_name=task_name,
            priority=TaskPriority.HIGH if force else TaskPriority.NORMAL,
            description=task_description,
            input_data={
                "country": country_code,
                "source": source,
                "force": force,
                "process": process,
                "save_raw": save_raw,
            }
        )
        
        console.print(f"[bold blue]🚀 Starting intelligent data crawl for {country_code}...[/bold blue]")
        console.print(f"[dim]Task UUID: {task.task_uuid}[/dim]")
        if force:
            console.print("[yellow]⚠️  Force mode: will crawl all data (ignoring database)[/yellow]")
        
        # 更新任务状态为运行中
        await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
        
        # Add startup log
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Crawl Task Started",
            content=f"Country: {country_code}\nSource: {source}\nForce Mode: {force}\nProcess Data: {process}",
            content_type="text"
        )
        
        # 获取爬虫
        if country_code == "CN":
            crawler = ChinaCDCCrawler()
        else:
            console.print(f"[red]Unsupported country: {country_code}[/red]")
            console.print(f"[yellow]Available countries: CN[/yellow]")
            await task_manager.update_task_status(
                task.task_uuid,
                TaskStatus.FAILED,
                error_message=f"Unsupported country: {country_code}"
            )
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
                    started_at=datetime.now(timezone.utc),
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
        
        # Log phase 1
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Phase 1/3: Fetching Data List",
            content="Starting to fetch available data list...",
            content_type="text"
        )
        
        # Update progress: 10%
        await task_manager.update_task_progress(task.task_uuid, 1, 10)
        
        results = await crawler.crawl(source=source, force=force, fill_missing=fill_missing)
        
        # Update progress: 30%
        await task_manager.update_task_progress(task.task_uuid, 3, 10)
        
        if not results:
            if run_id:
                async with get_database() as db:
                    run = await db.get(CrawlRun, run_id)
                    if run:
                        run.status = "completed"
                        run.finished_at = datetime.now(timezone.utc)
                        run.new_reports = 0
                        run.processed_reports = 0
                        run.total_records = 0
            console.print(f"[yellow]✓ No new data to crawl[/yellow]")
            
            # Log no new data
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Crawl Completed",
                content="No new data found to crawl",
                content_type="text"
            )
            
            # 更新任务输出数据
            async with get_database() as db:
                task_obj = await db.get(Task, task.id)
                if task_obj:
                    task_obj.output_data = {"message": "No new data to crawl"}
                    await db.commit()
            
            # Update progress to 100%
            await task_manager.update_task_progress(task.task_uuid, 10, 10)
            
            await task_manager.update_task_status(
                task.task_uuid,
                TaskStatus.COMPLETED
            )
            return
        
        console.print(f"[green]✓ Found {len(results)} new reports to process[/green]")
        
        # Log discovered data
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Data List Retrieved",
            content=f"Found {len(results)} {'new ' if not force else ''}report(s) to process",
            content_type="text"
        )
        
        # Update progress: 40%
        await task_manager.update_task_progress(task.task_uuid, 4, 10)
        
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
            
            # Log phase 2
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 2/3: Processing Data",
                content=f"Starting to process {len(results)} report(s)...",
                content_type="text"
            )
            
            # Update progress: 50%
            await task_manager.update_task_progress(task.task_uuid, 5, 10)
            
            from src.data.processors import DataProcessor
            
            processor = DataProcessor(
                output_dir=Path("data/processed") / country_code.lower(),
                country_code=country_code.lower()
            )
            
            # Progress callback for workbook logging
            processed_count = 0
            async def progress_callback(current, total, message):
                nonlocal processed_count
                processed_count = current
                # Update progress between 50% and 80%
                progress_pct = 50 + int((current / total) * 30)
                await task_manager.update_task_progress(task.task_uuid, progress_pct // 10, 10)
                
                # Log every 10 reports or at key milestones
                if current % 10 == 0 or current == total:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title="Processing Progress",
                        content=f"{current}/{total} reports processed",
                        content_type="text"
                    )
            
            with Progress() as progress:
                progress_task = progress.add_task("[cyan]Processing...", total=len(results))
                
                processed = await processor.process_crawler_results(
                    results,
                    save_to_file=True,
                    save_raw=save_raw,
                    crawl_run_id=run_id,
                    raw_dir=raw_dir,
                    progress_callback=progress_callback,
                )
                progress.update(progress_task, advance=len(results))
            
            console.print(f"[green]✓ Processed {len(processed)}/{len(results)} datasets[/green]")
            
            # 统计信息
            if processed:
                total_records = sum(len(df) for df in processed)
                console.print(f"[green]✓ Total records: {total_records}[/green]")
                
                # Log processing result
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="success",
                    title="Data Processing Completed",
                    content=f"Successfully processed: {len(processed)}/{len(results)} dataset(s)\nTotal records: {total_records}",
                    content_type="text"
                )
                
                # Update progress: 80%
                await task_manager.update_task_progress(task.task_uuid, 8, 10)
        
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
                    run.finished_at = datetime.now(timezone.utc)
                    run.new_reports = len(results)
                    run.processed_reports = len(processed) if process else 0
                    run.total_records = total_records if process and processed else 0
        
        # Log completion
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Crawl Task Completed",
            content=f"New reports: {len(results)}\nProcessed datasets: {len(processed) if process else 0}\nTotal records: {total_records if process and processed else 0}",
            content_type="text"
        )
        
        # 更新任务输出数据和状态
        async with get_database() as db:
            task_obj = await db.get(Task, task.id)
            if task_obj:
                task_obj.output_data = {
                    "new_reports": len(results),
                    "processed_reports": len(processed) if process else 0,
                    "total_records": total_records if process and processed else 0,
                    "crawl_run_id": run_id,
                }
                await db.commit()
        
        # Update progress to 100%
        await task_manager.update_task_progress(task.task_uuid, 10, 10)
        
        await task_manager.update_task_status(
            task.task_uuid,
            TaskStatus.COMPLETED
        )
        
        console.print(f"\n[bold green]✨ Crawl completed successfully![/bold green]")
    
    # Flag to track if task was cancelled
    task_cancelled = False
    
    def signal_handler(signum, frame):
        """Handle SIGINT (Ctrl+C) and SIGTERM signals"""
        nonlocal task_cancelled
        task_cancelled = True
        console.print("\n[yellow]⚠️  Cancellation requested... cleaning up...[/yellow]")
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    async def _crawl_with_error_handling():
        try:
            await _crawl()
        except KeyboardInterrupt:
            # Handle Ctrl+C interruption
            console.print("\n[yellow]⚠️  Task interrupted by user[/yellow]")
            
            # Update CrawlRun status
            if run_id:
                async with get_database() as db:
                    run = await db.get(CrawlRun, run_id)
                    if run:
                        run.status = "cancelled"
                        run.finished_at = datetime.now(timezone.utc)
                        run.error_message = "Interrupted by user (Ctrl+C)"
                        await db.commit()
            
            # Update task status to CANCELLED
            if task:
                # Add cancellation log
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="warning",
                    title="Task Cancelled",
                    content="Task was interrupted by user (Ctrl+C)",
                    content_type="text"
                )
                
                await task_manager.update_task_status(
                    task.task_uuid,
                    TaskStatus.CANCELLED,
                    error_message="Interrupted by user"
                )
            
            console.print("[yellow]✓ Task cancelled and status updated[/yellow]")
            sys.exit(130)  # Standard exit code for SIGINT
            
        except Exception as e:
            # Handle other errors
            # Update CrawlRun status
            if run_id:
                async with get_database() as db:
                    run = await db.get(CrawlRun, run_id)
                    if run:
                        run.status = "failed"
                        run.finished_at = datetime.now(timezone.utc)
                        run.error_message = str(e)
                        await db.commit()
            
            # Update task status to FAILED
            if task:
                # Add error log
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="error",
                    title="Task Failed",
                    content=f"Error: {str(e)}",
                    content_type="text"
                )
                
                await task_manager.update_task_status(
                    task.task_uuid,
                    TaskStatus.FAILED,
                    error_message=str(e)
                )
            
            logger.error(f"Crawl failed: {e}")
            console.print(f"[red]✗ Task failed: {e}[/red]")
            raise

    asyncio.run(_crawl_with_error_handling())


@app.command()
def generate_report(
    country: str = typer.Option("CN", help="Country code (e.g. CN)"),
    report_type: str = typer.Option("monthly", help="Report type (daily/weekly/monthly)"),
    days: int = typer.Option(365, help="Number of days to include (used when --period-start is not given)"),
    period_start: Optional[str] = typer.Option(None, help="Start date YYYY-MM-DD (overrides --days)"),
    period_end: Optional[str] = typer.Option(None, help="End date YYYY-MM-DD (defaults to today)"),
    send_email: bool = typer.Option(False, help="Send report via email"),
    enable_review: bool = typer.Option(True, help="Enable AI content review"),
):
    """
    Generate disease surveillance report with AI analysis
    """
    # Task variables for signal handling
    task = None
    report_id = None
    task_cancelled = False
    
    def signal_handler(signum, frame):
        """Handle SIGINT (Ctrl+C) and SIGTERM signals"""
        nonlocal task_cancelled
        task_cancelled = True
        console.print("\n[yellow]⚠️  Cancellation requested... cleaning up...[/yellow]")
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    async def _generate():
        nonlocal task, report_id
        
        await init_app()
        
        async with get_database() as db:
            # Normalize country code for lookups and logging
            country_code = country.upper()

            # ========== Phase 0: Try to reuse existing approved report ==========
            # Compute effective period first so reuse and fresh generation share the same range
            if period_end:
                _period_end = datetime.fromisoformat(period_end).replace(tzinfo=timezone.utc)
            else:
                _period_end = datetime.now(timezone.utc)

            if period_start:
                _period_start = datetime.fromisoformat(period_start).replace(tzinfo=timezone.utc)
            else:
                _period_start = _period_end - timedelta(days=days)

            effective_period_start = _period_start
            effective_period_end = _period_end

            reuse_report = None
            try:
                # Resolve country object
                country_query = select(Country).where(Country.code == country_code)
                country_result = await db.execute(country_query)
                country_obj = country_result.scalar_one_or_none()

                if country_obj:
                    reuse_query = (
                        select(Report)
                        .where(
                            Report.country_id == country_obj.id,
                            Report.period_start == effective_period_start,
                            Report.period_end == effective_period_end,
                            Report.report_type == ReportType[report_type.upper()],
                            Report.status == ReportStatus.APPROVED,
                        )
                        .order_by(Report.created_at.desc())
                        .limit(1)
                    )
                    reuse_result = await db.execute(reuse_query)
                    reuse_report = reuse_result.scalar_one_or_none()
            except Exception as e:
                logger.warning(f"Failed to check for reusable report: {e}")

            # Create task (even if we end up reusing an existing report)
            task_name = f"Generate {report_type.upper()} Report for {country_code}"
            period_desc = f"{effective_period_start.date()} → {effective_period_end.date()}"
            task_description = f"Period: {period_desc}, Review: {'Yes' if enable_review else 'No'}, Email: {'Yes' if send_email else 'No'}"
            
            task = await task_manager.create_task(
                task_type=TaskType.GENERATE_REPORT,
                task_name=task_name,
                description=task_description,
                priority=TaskPriority.NORMAL,
                input_data={
                    "country": country_code,
                    "report_type": report_type,
                    "days": days,
                    "period_start": effective_period_start.isoformat(),
                    "period_end": effective_period_end.isoformat(),
                    "send_email": send_email,
                    "enable_review": enable_review,
                    "reused": bool(reuse_report),
                }
            )
            
            console.print(f"[bold blue]🚀 Starting AI report generation for {country_code}...[/bold blue]")
            console.print(f"[dim]Task UUID: {task.task_uuid}[/dim]")
            
            # Log task start
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Report Generation Started",
                content=f"Generating {report_type} report for {country_code} ({days} days)",
                content_type="text"
            )
            
            # If a reusable approved report exists, short-circuit the heavy generation
            if reuse_report:
                output_files = []
                if reuse_report.markdown_path:
                    output_files.append(f"Markdown: {reuse_report.markdown_path}")
                if reuse_report.html_path:
                    output_files.append(f"HTML: {reuse_report.html_path}")
                if reuse_report.pdf_path:
                    output_files.append(f"PDF: {reuse_report.pdf_path}")

                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="success",
                    title="Report Reused",
                    content=(
                        f"Reused approved report #{reuse_report.id} for "
                        f"{country_code} {report_type} "
                        f"({effective_period_start.date()} → {effective_period_end.date()})"
                    ),
                    content_type="text"
                )

                # Update task output and status
                task_obj = await db.get(Task, task.id)
                if task_obj:
                    task_obj.output_data = {
                        "report_id": reuse_report.id,
                        "status": str(reuse_report.status),
                        "files": output_files,
                        "sections_count": len(reuse_report.sections) if hasattr(reuse_report, "sections") else 0,
                        "reused": True,
                    }
                    await db.commit()

                await task_manager.update_task_progress(task.task_uuid, 20, 20)
                await task_manager.update_task_status(task.task_uuid, TaskStatus.COMPLETED)

                console.print(f"[bold green]✨ Reused approved report #{reuse_report.id}![/bold green]")
                console.print(f"  Status: {reuse_report.status}")
                if output_files:
                    for file_info in output_files:
                        console.print(f"  {file_info}")
                return

            # No reusable report found → proceed with normal generation
            await task_manager.update_task_status(task.task_uuid, TaskStatus.RUNNING)
            
            # Update progress: 5%
            await task_manager.update_task_progress(task.task_uuid, 1, 20)
        
            # ========== Phase 1: Data Preparation (5-15%) ==========
            console.print(f"\n[bold cyan]Phase 1/5: Preparing data...[/bold cyan]")
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 1/5: Data Preparation",
                content="Fetching country information and disease records...",
                content_type="text"
            )
            
            # Get country
            country_query = select(Country).where(Country.code == country)
            country_result = await db.execute(country_query)
            country_obj = country_result.scalar_one_or_none()
            
            if not country_obj:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="error",
                    title="Country Not Found",
                    content=f"Country code '{country}' does not exist in database",
                    content_type="text"
                )
                console.print(f"[red]Country not found: {country}[/red]")
                raise ValueError(f"Country not found: {country}")
            
            # Set time range: explicit dates take priority over --days
            if period_end:
                _period_end = datetime.fromisoformat(period_end).replace(tzinfo=timezone.utc)
            else:
                _period_end = datetime.now(timezone.utc)

            if period_start:
                _period_start = datetime.fromisoformat(period_start).replace(tzinfo=timezone.utc)
            else:
                _period_start = _period_end - timedelta(days=days)

            # Use effective_* variables inside this coroutine to avoid shadowing outer parameters
            effective_period_start = _period_start
            effective_period_end = _period_end
            
            # Check if there's any data in the requested time range
            data_check_query = select(func.count()).select_from(DiseaseRecord).where(
                DiseaseRecord.country_id == country_obj.id,
                DiseaseRecord.time >= effective_period_start,
                DiseaseRecord.time <= effective_period_end,
            )
            data_check_result = await db.execute(data_check_query)
            data_count = data_check_result.scalar()
            
            # If no data in range, try to find the actual data range
            if data_count == 0:
                # Get the latest data date
                latest_query = select(func.max(DiseaseRecord.time)).where(
                    DiseaseRecord.country_id == country_obj.id
                )
                latest_result = await db.execute(latest_query)
                latest_date = latest_result.scalar()
                
                if latest_date:
                    if latest_date.tzinfo is None:
                        latest_date = latest_date.replace(tzinfo=timezone.utc)
                    # Adjust period to use the latest available data
                    effective_period_end = latest_date
                    effective_period_start = effective_period_end - timedelta(days=days)
                    
                    warning_msg = (
                        "No data in requested period. Using latest available data: "
                        f"{effective_period_start.date()} to {effective_period_end.date()}"
                    )
                    console.print(f"[yellow]⚠️  {warning_msg}[/yellow]")
                    
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="warning",
                        title="Time Range Adjusted",
                        content=warning_msg,
                        content_type="text"
                    )

                    # region agent log
                    _agent_debug_log(
                        run_id="pre-ai-generate",
                        hypothesis_id="A,B",
                        location="main.py:565",
                        message="Adjusted time range due to no data in requested period",
                        data={
                            "country": country,
                            "original_period_start": _period_start.isoformat() if _period_start else None,
                            "original_period_end": _period_end.isoformat() if _period_end else None,
                            "adjusted_period_start": effective_period_start.isoformat() if effective_period_start else None,
                            "adjusted_period_end": effective_period_end.isoformat() if effective_period_end else None,
                            "requested_data_count": int(data_count or 0),
                        },
                    )
                    # endregion
                else:
                    error_msg = f"No disease data found for country {country_obj.name}. Please run crawl command first."
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="error",
                        title="No Data Available",
                        content=error_msg,
                        content_type="text"
                    )
                    console.print(f"[red]✗ {error_msg}[/red]")

                    # region agent log
                    _agent_debug_log(
                        run_id="pre-ai-generate",
                        hypothesis_id="A",
                        location="main.py:576",
                        message="No disease data found for country",
                        data={
                            "country": country,
                            "period_start": _period_start.isoformat() if _period_start else None,
                            "period_end": _period_end.isoformat() if _period_end else None,
                            "requested_data_count": int(data_count or 0),
                        },
                    )
                    # endregion
                    raise ValueError(error_msg)
            
            # Get report type enum
            report_type_enum = ReportType[report_type.upper()]
            
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Data Preparation Complete",
                content=(
                    f"Country: {country_obj.name}\n"
                    f"Period: {effective_period_start.date()} to {effective_period_end.date()}"
                ),
                content_type="text"
            )
            
            # Update progress: 15%
            await task_manager.update_task_progress(task.task_uuid, 3, 20)
            console.print(f"[green]✓ Data preparation complete[/green]")
            
            # ========== Phase 2: Report Generation with AI Analysis (15-80%) ==========
            console.print(f"\n[bold cyan]Phase 2/5: AI analysis and content generation...[/bold cyan]")
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 2/5: AI Analysis & Generation",
                content="Starting multi-agent AI workflow (Analyst → Writer → Reviewer)...",
                content_type="text"
            )
            
            # Initialize report generator
            from src.generation import ReportGenerator
            generator = ReportGenerator()
            
            # Progress callback for detailed tracking
            generation_step = 0
            async def ai_progress_callback(stage, current, total, message):
                nonlocal generation_step
                generation_step += 1
                
                # Map stages to progress 15-80%
                stage_progress = {
                    "data_extraction": 15,
                    "analysis": 40,
                    "writing": 60,
                    "review": 75,
                }
                base_progress = stage_progress.get(stage, 15)
                progress_pct = base_progress + int((current / total) * 10) if total > 0 else base_progress
                await task_manager.update_task_progress(task.task_uuid, progress_pct // 5, 20)
                
                # Log every major milestone
                if current % 5 == 0 or current == total:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title=f"{stage.replace('_', ' ').title()} Progress",
                        content=f"{message} ({current}/{total})",
                        content_type="text"
                    )
            
            # Generate report with callback

            # region agent log
            _agent_debug_log(
                run_id="pre-ai-generate",
                hypothesis_id="C,D",
                location="main.py:643",
                message="About to call ReportGenerator.generate",
                data={
                    "country_id": country_obj.id,
                    "country_code": country,
                    "report_type": report_type_enum.value,
                    "period_start": effective_period_start.isoformat() if effective_period_start else None,
                    "period_end": effective_period_end.isoformat() if effective_period_end else None,
                    "send_email": send_email,
                    "enable_review": enable_review,
                },
            )
            # endregion

            report = await generator.generate(
                country_id=country_obj.id,
                report_type=report_type_enum,
                period_start=effective_period_start,
                period_end=effective_period_end,
                send_email=send_email,
                enable_review=enable_review,
                progress_callback=ai_progress_callback,
                db=db,
            )
            
            report_id = report.id
            
            if report.status == ReportStatus.FAILED:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="error",
                    title="Report Generation Failed",
                    content=f"Error: {report.error_message or 'Unknown error'}",
                    content_type="text"
                )
                raise Exception(f"Report generation failed: {report.error_message}")
            
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="AI Generation Complete",
                content=f"Report ID: {report.id}\nSections: {len(report.sections) if hasattr(report, 'sections') else 'N/A'}",
                content_type="text"
            )
            
            # Update progress: 80%
            await task_manager.update_task_progress(task.task_uuid, 16, 20)
            console.print(f"[green]✓ AI analysis and generation complete[/green]")
            
            # ========== Phase 3: Formatting & Export (80-90%) ==========
            console.print(f"\n[bold cyan]Phase 3/5: Formatting and exporting...[/bold cyan]")
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 3/5: Format & Export",
                content="Generating Markdown, HTML, and PDF outputs...",
                content_type="text"
            )
            
            # Progress is tracked internally by generator
            # Update progress: 90%
            await task_manager.update_task_progress(task.task_uuid, 18, 20)
            
            output_files = []
            if report.markdown_path:
                output_files.append(f"Markdown: {report.markdown_path}")
            if report.html_path:
                output_files.append(f"HTML: {report.html_path}")
            if report.pdf_path:
                output_files.append(f"PDF: {report.pdf_path}")
            
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Export Complete",
                content="\n".join(output_files) if output_files else "No files exported",
                content_type="text"
            )
            
            console.print(f"[green]✓ Formatting and export complete[/green]")
            
            # ========== Phase 4: Email Notification (90-95%) ==========
            if send_email:
                console.print(f"\n[bold cyan]Phase 4/5: Sending email...[/bold cyan]")
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="info",
                    title="Phase 4/5: Email Notification",
                    content="Sending report via email...",
                    content_type="text"
                )
                
                # Email is sent during generation
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="success",
                    title="Email Sent",
                    content="Report successfully delivered",
                    content_type="text"
                )
                console.print(f"[green]✓ Email sent[/green]")
            
            # Update progress: 95%
            await task_manager.update_task_progress(task.task_uuid, 19, 20)
            
            # ========== Phase 5: Finalization (95-100%) ==========
            console.print(f"\n[bold cyan]Phase 5/5: Finalizing...[/bold cyan]")
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Report Generation Completed",
                content=f"Report ID: {report.id}\nStatus: {report.status}\nFiles: {len(output_files)}",
                content_type="text"
            )
            
            # Update task output
            async with get_database() as db:
                task_obj = await db.get(Task, task.id)
                if task_obj:
                    task_obj.output_data = {
                        "report_id": report.id,
                        "status": str(report.status),
                        "files": output_files,
                        "sections_count": len(report.sections) if hasattr(report, 'sections') else 0,
                    }
                    await db.commit()
            
            # Update progress to 100%
            await task_manager.update_task_progress(task.task_uuid, 20, 20)
            
            await task_manager.update_task_status(
                task.task_uuid,
                TaskStatus.COMPLETED
            )
            
            console.print(f"\n[bold green]✨ Report generation completed successfully![/bold green]")
            console.print(f"  Report ID: {report.id}")
            console.print(f"  Status: {report.status}")
            if output_files:
                for file_info in output_files:
                    console.print(f"  {file_info}")
    
    async def _generate_with_error_handling():
        try:
            await _generate()
        except KeyboardInterrupt:
            # Handle Ctrl+C interruption
            console.print("\n[yellow]⚠️  Report generation interrupted by user[/yellow]")
            
            # Update task status to CANCELLED
            if task:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="warning",
                    title="Task Cancelled",
                    content="Report generation was interrupted by user (Ctrl+C)",
                    content_type="text"
                )
                
                await task_manager.update_task_status(
                    task.task_uuid,
                    TaskStatus.CANCELLED,
                    error_message="Interrupted by user"
                )
            
            # Update report status if created
            if report_id:
                async with get_database() as db:
                    from src.domain import Report, ReportStatus
                    report = await db.get(Report, report_id)
                    if report:
                        report.status = ReportStatus.CANCELLED
                        report.error_message = "Interrupted by user"
                        await db.commit()
            
            console.print("[yellow]✓ Task cancelled and status updated[/yellow]")
            sys.exit(130)
            
        except Exception as e:
            # Handle other errors
            console.print(f"\n[red]✗ Report generation failed: {e}[/red]")
            
            # Update task status to FAILED
            if task:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="error",
                    title="Task Failed",
                    content=f"Error: {str(e)}",
                    content_type="text"
                )
                
                await task_manager.update_task_status(
                    task.task_uuid,
                    TaskStatus.FAILED,
                    error_message=str(e)
                )
            
            # Update report status if created
            if report_id:
                async with get_database() as db:
                    from src.domain import Report, ReportStatus
                    report = await db.get(Report, report_id)
                    if report:
                        report.status = ReportStatus.FAILED
                        report.error_message = str(e)
                        await db.commit()
            
            logger.error(f"Report generation failed: {e}")
            raise

    asyncio.run(_generate_with_error_handling())
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
                period_end = datetime.now(timezone.utc)
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
