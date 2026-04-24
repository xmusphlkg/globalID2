"""Services package — business-logic layer between CLI and domain.

Keep package imports light so utility modules can import submodules such as
``src.services.settings_service`` without triggering the task execution stack.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .crawl_service import CrawlResult, CrawlService
from .report_service import ReportResult, ReportService

__all__ = [
    "CrawlResult",
    "CrawlService",
    "ReportResult",
    "ReportService",
    "execute_task",
    "execute_task_background",
    "task_lifecycle",
]


def __getattr__(name: str) -> Any:
    if name in {"execute_task", "execute_task_background"}:
        module = import_module("src.services.task_executor")
        return getattr(module, name)
    if name == "task_lifecycle":
        module = import_module("src.services._lifecycle")
        return module.task_lifecycle
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
