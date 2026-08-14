from __future__ import annotations

from src.services.situation_history_service import _signal_record


def test_signal_projection_keeps_detector_and_risk_evidence() -> None:
    item = {
        "id": "signal:test",
        "disease_id": "D001",
        "disease_name": "Test disease",
        "country_code": "ZZ",
        "country_name": "Test jurisdiction",
        "series_code": "SER_TEST",
        "source_system": "official_test",
        "metric_type": "case_notifications",
        "unit": "count",
        "cadence": "weekly",
        "window": {"label": "Last 4 weeks", "current": 42, "previous": 21, "change_pct": 100},
        "statistics": {
            "z_score": 2.3,
            "robust_z": 2.8,
            "ewma_residual": 0.7,
            "bayesian_change_probability": 0.81,
            "detectors": {"ewma": True},
            "detector_votes": 3,
        },
        "risk": {
            "score": 61,
            "level": "high",
            "confidence": "medium",
            "dimensions": {"trend": 70, "official_concern": 40},
            "missing_dimensions": ["severity"],
        },
        "source_url": "https://example.test/evidence",
    }

    row = _signal_record(7, "increasing", item, "2026-08-09")

    assert row.history_snapshot_id == 7
    assert row.comparison_window == "Last 4 weeks"
    assert row.standard_z == 2.3
    assert row.ewma_alarm == 1
    assert row.bayesian_change_probability == 0.81
    assert row.detector_votes == 3
    assert row.risk_level == "high"
    assert row.missing_dimensions == ["severity"]
    assert row.evidence_url == "https://example.test/evidence"
