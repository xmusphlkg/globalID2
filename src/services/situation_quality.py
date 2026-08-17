"""Publication quality gates for deterministic Situation Room snapshots."""

from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any, Iterable


REQUIRED_TOP_LEVEL = {
    "schema_version",
    "method_version",
    "snapshot_id",
    "checked_at",
    "content_updated_at",
    "data_through",
    "coverage",
    "increasing",
    "respiratory",
    "emerging",
    "unusual",
}


def validate_snapshot_schema(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_TOP_LEVEL - payload.keys())
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if payload.get("schema_version") != "situation_room.v2":
        errors.append("schema_version must be situation_room.v2")
    for section in ("increasing", "respiratory", "emerging", "unusual"):
        if not isinstance(payload.get(section), list):
            errors.append(f"{section} must be an array")
    risk_errors = 0
    for section in ("increasing", "unusual"):
        for row in payload.get(section) or []:
            risk = row.get("risk")
            if not isinstance(risk, dict) or risk.get("level") not in {"low", "moderate", "high", "very_high"}:
                risk_errors += 1
    if risk_errors:
        errors.append(f"{risk_errors} statistical signals have invalid risk objects")
    return errors


def evaluate_quality_gate(
    payload: dict[str, Any],
    *,
    prior_analyzed_counts: Iterable[int] = (),
    require_built_site: bool = False,
    page_built: bool | None = None,
    sitemap_built: bool | None = None,
    require_algorithm_execution: bool = False,
    required_analyzed_sources: Iterable[str] = (),
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    schema_errors = validate_snapshot_schema(payload)
    checks.append({"id": "json_schema", "passed": not schema_errors, "details": schema_errors})

    analyzed = int((payload.get("coverage") or {}).get("analyzed_series_count") or 0)
    checks.append({"id": "analyzed_series_nonzero", "passed": analyzed > 0, "value": analyzed})

    if require_algorithm_execution:
        execution = payload.get("analysis_execution") or {}
        methods = execution.get("methods") or {}
        for method in (
            "seasonal_baseline",
            "standard_z",
            "robust_z",
            "ewma",
            "bayesian_change_point",
        ):
            count = int((methods.get(method) or {}).get("executed_count") or 0)
            checks.append(
                {
                    "id": f"algorithm_{method}",
                    "passed": count > 0,
                    "executed_count": count,
                }
            )
        source_usage = execution.get("source_usage") or {}
        for source in required_analyzed_sources:
            analyzed_count = int((source_usage.get(str(source)) or {}).get("analyzed_count") or 0)
            checks.append(
                {
                    "id": f"analyzed_source_{source}",
                    "passed": analyzed_count > 0,
                    "analyzed_count": analyzed_count,
                }
            )

    data_through = payload.get("data_through")
    valid_date = False
    try:
        parsed_date = date.fromisoformat(str(data_through))
        checked_date = date.fromisoformat(str(payload.get("checked_at", ""))[:10])
        valid_date = parsed_date <= checked_date
    except ValueError:
        pass
    checks.append({"id": "data_through_valid", "passed": valid_date, "value": data_through})

    history = [int(value) for value in prior_analyzed_counts if int(value) > 0][-7:]
    historical_median = median(history) if history else None
    coverage_passed = historical_median is None or analyzed >= historical_median * 0.8
    checks.append(
        {
            "id": "seven_day_coverage",
            "passed": coverage_passed,
            "value": analyzed,
            "historical_median": historical_median,
            "minimum_allowed": round(historical_median * 0.8, 1) if historical_median is not None else None,
            "status": "not_enough_history" if historical_median is None else "evaluated",
        }
    )

    if require_built_site:
        checks.extend(
            [
                {"id": "situation_page_built", "passed": bool(page_built)},
                {"id": "situation_sitemap_built", "passed": bool(sitemap_built)},
            ]
        )
    failed = [check["id"] for check in checks if not check["passed"]]
    return {"status": "passed" if not failed else "failed", "passed": not failed, "failed_checks": failed, "checks": checks}
