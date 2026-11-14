"""Typed Ontario report discovery and collection results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass(frozen=True)
class EmbedContext:
    token: str = field(repr=False)
    cluster_url: str
    expires_at: int
    activity_id: str
    report_id: str


@dataclass(frozen=True)
class MonthlyVisual:
    model_id: int
    dataset_id: str
    query: dict[str, Any]
    page_name: str
    visual_name: str
    title: str
    model_refresh_time: str


@dataclass(frozen=True)
class CAOntarioFetchSummary:
    row_count: int
    disease_count: int
    latest_date: Optional[date]
    reporting_year: int
    source_url: str
    acquisition_mode: str
    content_sha256: str
    dataset_timestamp: str = ""
    model_refresh_time: str = ""
    populated_month_count: int = 0
    unpublished_month_slots: int = 0
    source_artifact_sha256: str = ""
    source_file_mtime: str = ""
