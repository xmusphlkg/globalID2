"""Services package — business-logic layer between CLI and domain."""

from .crawl_service import CrawlResult, CrawlService
from .report_service import ReportResult, ReportService
from .task_executor import execute_task, execute_task_background
from ._lifecycle import task_lifecycle

__all__ = [
    "CrawlResult",
    "CrawlService",
    "ReportResult",
    "ReportService",
    "execute_task",
    "execute_task_background",
    "task_lifecycle",
]
