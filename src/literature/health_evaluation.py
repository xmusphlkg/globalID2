"""Aggregate health checks into a stable Research Radar report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from .health_content_checks import _article_checks, _release_checks
from .health_operations_checks import _operations_checks
from .health_pipeline_checks import _backfill_check, _pipeline_checks
from .health_snapshot import (
    REPORT_SCHEMA_VERSION,
    HealthThresholds,
    ResearchRadarSnapshot,
)


def evaluate_health(
    snapshot: ResearchRadarSnapshot,
    thresholds: HealthThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate a snapshot into a deterministic, safe-to-publish JSON object."""

    limits = thresholds or HealthThresholds()
    checks = [
        *_pipeline_checks(snapshot, limits),
        _backfill_check(snapshot, limits),
        *_article_checks(snapshot, limits),
        *_release_checks(snapshot, limits),
        *_operations_checks(snapshot, limits),
    ]
    counts = Counter(check["status"] for check in checks)
    overall = "unhealthy" if counts["critical"] else (
        "degraded" if counts["warning"] else "healthy"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "service": "research-radar",
        "status": overall,
        "generated_at": snapshot.collected_at.isoformat(),
        "summary": {
            "check_count": len(checks),
            "passed": counts["pass"],
            "warnings": counts["warning"],
            "critical": counts["critical"],
        },
        "checks": checks,
    }


def exit_code_for(report: Mapping[str, Any], *, fail_on: str = "warning") -> int:
    """Return 0/1/2 for healthy/degraded/unhealthy; 3 is reserved for CLI errors."""

    status = str(report.get("status") or "unhealthy")
    if status == "unhealthy":
        return 2
    if status == "degraded" and fail_on == "warning":
        return 1
    return 0

__all__ = ["evaluate_health", "exit_code_for"]
