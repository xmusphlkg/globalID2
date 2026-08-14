from __future__ import annotations

from src.services.situation_quality import evaluate_quality_gate


def _payload(analyzed: int = 100) -> dict:
    return {
        "schema_version": "situation_room.v2",
        "method_version": "v2",
        "snapshot_id": "snapshot-1",
        "checked_at": "2026-08-13T02:00:00+00:00",
        "content_updated_at": "2026-08-13T02:00:00+00:00",
        "data_through": "2026-08-10",
        "coverage": {"analyzed_series_count": analyzed},
        "increasing": [],
        "respiratory": [],
        "emerging": [],
        "unusual": [],
    }


def test_quality_gate_passes_valid_nonzero_snapshot() -> None:
    gate = evaluate_quality_gate(_payload(), prior_analyzed_counts=[100] * 7)
    assert gate["passed"] is True


def test_quality_gate_rejects_large_coverage_drop() -> None:
    gate = evaluate_quality_gate(_payload(79), prior_analyzed_counts=[100] * 7)
    assert gate["passed"] is False
    assert "seven_day_coverage" in gate["failed_checks"]


def test_build_artifacts_can_be_required_at_release_boundary() -> None:
    gate = evaluate_quality_gate(_payload(), require_built_site=True, page_built=True, sitemap_built=False)
    assert gate["passed"] is False
    assert gate["failed_checks"] == ["situation_sitemap_built"]


def test_release_gate_requires_all_algorithms_and_configured_numeric_source() -> None:
    payload = _payload()
    payload["analysis_execution"] = {
        "methods": {
            name: {"executed_count": 10}
            for name in (
                "seasonal_baseline",
                "standard_z",
                "robust_z",
                "ewma",
                "bayesian_change_point",
            )
        },
        "source_usage": {"SRC_REQUIRED": {"analyzed_count": 3}},
    }

    passed = evaluate_quality_gate(
        payload,
        require_algorithm_execution=True,
        required_analyzed_sources=["SRC_REQUIRED"],
    )
    failed = evaluate_quality_gate(
        payload,
        require_algorithm_execution=True,
        required_analyzed_sources=["SRC_MISSING"],
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert "analyzed_source_SRC_MISSING" in failed["failed_checks"]
