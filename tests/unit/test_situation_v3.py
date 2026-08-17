from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from src.services.situation_v3.contracts import PublicHealthRisk, SituationReportV3
from src.services.situation_v3.backtest import simulate_weekly_batch
from src.services.situation_v3 import model as situation_model
from src.services.situation_v3.model import benjamini_hochberg, evaluate_frame_v3
from src.services.situation_v3.reporting import (
    build_daily_report_v3,
    build_period_report_v3,
    cluster_official_events,
)
from src.services.situation_v3 import persistence
from src.services.situation_v3 import pipeline as situation_pipeline
from src.services.situation_v3 import source_adapters


def _config() -> dict:
    return {
        "public_enabled": True,
        "v3": {"alert_q": 0.05, "strong_q": 0.01},
        "cadences": {
            "weekly": {
                "window_periods": 4,
                "periods_per_year": 52,
                "freshness_days": 35,
                "label": "Last 4 weeks",
            }
        },
        "data_latency": {
            "minimum_source_period_coverage": 0.8,
            "minimum_maturity_days": {"weekly": 7},
            "delay_warning_days": {"weekly": 14},
        },
        "thresholds": {
            "minimum_observations": {"weekly": 120},
            "minimum_current_cases": 20,
            "minimum_absolute_increase": 10,
            "minimum_relative_increase_pct": 25,
        },
        "quality": {
            "minimum_window_completeness": 0.8,
            "required_analyzed_source_systems": [],
            "required_context_source_systems": [],
        },
    }


def _guarded_auto_config() -> dict:
    config = _config()
    config["publication"] = {
        "mode": "guarded_auto",
        "require_verified_signals": True,
        "auto_verification": {
            "enabled": True,
            "policy_version": "guarded_auto_v1",
            "calibration_decision": "supported",
            "maximum_q": 0.01,
            "minimum_completeness": 0.95,
            "require_current": True,
            "require_evidence_link": True,
            "allowed_fit_statuses": ["completed"],
            "allowed_detector_tiers": ["common_count", "rare_count"],
        },
    }
    return config


def test_production_config_keeps_statistical_auto_publication_disabled() -> None:
    config = json.loads(
        (Path(__file__).resolve().parents[2] / "configs" / "situation_room.json").read_text(
            encoding="utf-8"
        )
    )
    publication = config["publication"]
    assert publication["mode"] == "exception_review"
    assert publication["require_verified_signals"] is True
    assert publication["automatic_triage"]["enabled"] is True
    assert publication["automatic_triage"][
        "rare_count_requires_official_match_for_queue"
    ] is True
    assert publication["automatic_triage"][
        "fallback_requires_official_match_for_queue"
    ] is True
    assert publication["auto_verification"]["enabled"] is False
    assert publication["auto_verification"]["calibration_decision"] == "not_yet_supported"


def _exception_review_config() -> dict:
    config = _guarded_auto_config()
    config["publication"]["mode"] = "exception_review"
    config["publication"]["auto_verification"]["enabled"] = False
    config["publication"]["auto_verification"]["calibration_decision"] = (
        "not_yet_supported"
    )
    config["publication"]["automatic_triage"] = {
        "enabled": True,
        "policy_version": "exception_review_v1",
        "queue_states": ["alert", "strong"],
        "rare_count_requires_official_match_for_queue": True,
        "fallback_requires_official_match_for_queue": True,
    }
    return config


def _weekly_frame(*, aliases: bool = False, unit: str = "count") -> pd.DataFrame:
    dates = pd.date_range("2022-08-07", periods=210, freq="7D", tz="UTC")
    index = np.arange(len(dates))
    values = 40 + 7 * np.sin(index * 2 * np.pi / 52) + index * 0.02
    values[-4:] = [105, 112, 118, 124]
    rows: list[dict] = []
    geographies = ["national", "source:SRC_TEST:reporting-area:total"] if aliases else ["national"]
    for geography in geographies:
        for stamp, value in zip(dates, values, strict=True):
            rows.append(
                {
                    "time": stamp,
                    "value": float(value if unit == "count" else value / 2),
                    "geography_key": geography,
                    "dimension_key": "all",
                    "dimensions": {},
                    "series_code": "SER_TEST",
                    "disease_id": "D_TEST",
                    "disease_name": "Test disease",
                    "disease_slug": "test-disease",
                    "country_code": "US",
                    "country_name": "United States",
                    "source_system": "SRC_TEST",
                    "source_label": "Test source",
                    "source_url": "https://example.test/source",
                    "metric_type": "case_notifications" if unit == "count" else "test_positivity",
                    "temporal_granularity": "weekly",
                    "unit": unit,
                }
            )
    return pd.DataFrame(rows)


def _daily_report(signals, checked_at: datetime) -> SituationReportV3:
    ledger = [
        {
            "status": "modeled",
            "source_system": signal.identity.source_system,
            "signal_id": signal.identity.signal_id,
            "fit_status": signal.anomaly.fit_status,
        }
        for signal in signals
    ]
    if not ledger:
        ledger = [
            {
                "status": "modeled",
                "source_system": "SRC_TEST",
                "fit_status": "completed",
            }
        ]
    return build_daily_report_v3(
        signals=[signal.model_copy(deep=True) for signal in signals],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_config(),
        checked_at=checked_at,
        registered_series_count=max(1, len(signals)),
    )


def test_benjamini_hochberg_is_monotonic_in_rank() -> None:
    p_values = [0.04, 0.001, 0.02, 0.8]
    q_values = benjamini_hochberg(p_values)
    ranked = sorted(zip(p_values, q_values), key=lambda item: item[0])
    assert [q for _, q in ranked] == sorted(q for _, q in ranked)
    assert q_values[1] == pytest.approx(0.004)
    assert all(q >= p for p, q in zip(p_values, q_values, strict=True))


def test_geography_aliases_collapse_to_one_source_native_signal() -> None:
    frame = _weekly_frame(aliases=True)
    signals, rejected, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert rejected == {}
    assert len(ledger) == 1
    assert len(signals) == 1
    assert signals[0].identity.canonical_geography_key == "country:US:national"
    assert signals[0].identity.source_geography_keys == [
        "national",
        "source:SRC_TEST:reporting-area:total",
    ]
    assert signals[0].anomaly.state in {"alert", "strong"}


def test_percentage_without_denominator_is_context_only() -> None:
    frame = _weekly_frame(unit="percent")
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert len(signals) == 1
    assert signals[0].anomaly.state == "not_modeled"
    assert signals[0].anomaly.fit_status == "context_only_missing_denominator"
    assert signals[0].assessment.public_health_risk.status == "not_assessed"
    assert ledger[0]["status"] == "context_only"


def test_percentage_with_numerator_and_denominator_is_formally_modeled() -> None:
    frame = _weekly_frame(unit="percent")
    frame["denominator"] = 1000.0
    frame["numerator"] = frame["value"] / 100.0 * frame["denominator"]
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert len(signals) == 1
    assert signals[0].anomaly.model == "robust_quasi_poisson_offset_v1"
    assert signals[0].anomaly.fit_status == "completed"
    assert signals[0].anomaly.q_value is not None
    # Non-count metrics cannot alert until that metric has an explicit effect gate.
    assert signals[0].anomaly.state == "routine"
    assert signals[0].anomaly.effect_threshold_passed is False
    assert ledger[0]["status"] == "modeled"


def test_disease_specific_effect_threshold_overrides_generic_count_gate() -> None:
    frame = _weekly_frame()
    config = _config()
    config["thresholds"]["special_thresholds"] = [
        {
            "disease_id": "D_TEST",
            "source_system": "SRC_TEST",
            "effect": {"minimum_current_cases": 10_000},
        }
    ]
    signals, _, _ = evaluate_frame_v3(
        frame,
        config,
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert signals[0].anomaly.q_value is not None
    assert signals[0].anomaly.effect_threshold_passed is False
    assert signals[0].anomaly.state == "routine"


def test_provisional_source_period_is_held_back_before_modeling() -> None:
    frame = _weekly_frame()
    raw_latest = pd.Timestamp(frame["time"].max())
    cutoff = raw_latest - pd.Timedelta(days=14)
    frame["latest_available_time"] = raw_latest
    frame["analysis_cutoff"] = cutoff
    frame["source_period_coverage"] = 0.82
    signals, rejected, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=raw_latest.date(),
    )
    assert rejected == {}
    assert signals[0].observation.data_status == "held_back"
    assert signals[0].observation.latest_available_period == raw_latest.date()
    assert signals[0].observation.data_through == cutoff.date()
    assert max(point.period for point in signals[0].recent_points) <= cutoff.date()
    assert ledger[0]["source_period_coverage"] == pytest.approx(0.82)


def test_delayed_feed_is_isolated_from_current_fdr_family() -> None:
    frame = _weekly_frame()
    latest = pd.Timestamp(frame["time"].max())
    signals, rejected, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=(latest + pd.Timedelta(days=21)).date(),
    )
    assert rejected == {}
    assert signals[0].observation.data_status == "delayed"
    assert signals[0].anomaly.state == "watch"
    assert signals[0].anomaly.q_value == 1.0
    assert "source_reporting_delayed" in signals[0].assessment.evidence_gaps


def test_held_back_analysis_period_becomes_delayed_when_cutoff_is_too_old() -> None:
    frame = _weekly_frame()
    raw_latest = pd.Timestamp(frame["time"].max())
    cutoff = raw_latest - pd.Timedelta(days=21)
    frame["latest_available_time"] = raw_latest
    frame["analysis_cutoff"] = cutoff
    signals, rejected, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=raw_latest.date(),
    )
    assert rejected == {}
    assert signals[0].observation.reporting_lag_days == 0
    assert signals[0].observation.analysis_lag_days == 21
    assert signals[0].observation.data_status == "delayed"
    assert signals[0].anomaly.state == "watch"
    assert signals[0].anomaly.q_value == 1.0
    assert "analysis_period_delayed" in signals[0].assessment.evidence_gaps


def test_non_converged_model_is_excluded_from_inference(monkeypatch) -> None:
    frame = _weekly_frame()
    original_fit = situation_model._fit_robust_quasi_poisson

    def non_converged_fit(*args, **kwargs):
        result = original_fit(*args, **kwargs)
        assert result is not None
        return {**result, "converged": False}

    monkeypatch.setattr(situation_model, "_fit_robust_quasi_poisson", non_converged_fit)
    config = _config()
    config["v3"]["detector_tiers"] = {"enable_empirical_fallback": False}
    signals, rejected, ledger = evaluate_frame_v3(
        frame,
        config,
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert rejected == {}
    assert signals[0].anomaly.fit_status == "non_converged"
    assert signals[0].anomaly.state == "not_modeled"
    assert signals[0].anomaly.raw_p_value is None
    assert signals[0].anomaly.q_value is None
    assert signals[0].anomaly.diagnostics["excluded_from_inference"] is True
    assert ledger[0]["status"] == "context_only"


def test_non_converged_count_model_uses_auditable_empirical_fallback(monkeypatch) -> None:
    frame = _weekly_frame()
    original_fit = situation_model._fit_robust_quasi_poisson

    def non_converged_fit(*args, **kwargs):
        result = original_fit(*args, **kwargs)
        assert result is not None
        return {**result, "converged": False}

    monkeypatch.setattr(situation_model, "_fit_robust_quasi_poisson", non_converged_fit)
    signals, rejected, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert rejected == {}
    assert signals[0].anomaly.model == "seasonal_empirical_fallback_v1"
    assert signals[0].anomaly.fit_status == "fallback_completed"
    assert signals[0].anomaly.raw_p_value is not None
    assert signals[0].anomaly.diagnostics["fallback"] == "seasonal_empirical_v1"
    assert signals[0].anomaly.diagnostics["primary_fit_status"] == "non_converged"
    assert ledger[0]["status"] == "modeled"


def test_empirical_weekly_fallback_handles_iso_week_53_as_adjacent_not_identical() -> None:
    history_dates: list[datetime] = []
    history_values: list[float] = []
    for year in range(2012, 2020):
        for week, value in ((3, 100.0), (51, 10.0), (52, 10.0)):
            history_dates.append(
                datetime.fromisocalendar(year, week, 1).replace(tzinfo=timezone.utc)
            )
            history_values.append(value)
    result = situation_model._fit_seasonal_empirical_baseline(
        np.asarray(history_values),
        pd.DatetimeIndex(history_dates),
        pd.DatetimeIndex(
            [datetime.fromisocalendar(2020, 53, 1).replace(tzinfo=timezone.utc)]
        ),
        "weekly",
    )
    assert result is not None
    assert result["seasonal_sample_counts"] == [16]
    assert result["expected_points"][0] == pytest.approx(10.0)


def test_nan_source_url_is_not_published_as_evidence() -> None:
    frame = _weekly_frame()
    frame["source_url"] = np.nan
    signals, rejected, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert rejected == {}
    assert signals[0].evidence_links == []
    assert "source_evidence_url_missing" in signals[0].assessment.evidence_gaps


def test_configured_source_url_fills_missing_row_evidence_only() -> None:
    configured = {"SRC_TEST": "https://example.test/source-registry"}
    assert source_adapters._source_evidence_url(
        None, "SRC_TEST", configured
    ) == "https://example.test/source-registry"
    assert source_adapters._source_evidence_url(
        "https://example.test/row", "SRC_TEST", configured
    ) == "https://example.test/row"
    assert source_adapters._source_evidence_url(
        None, "SRC_UNKNOWN", configured
    ) is None


def test_public_signal_without_evidence_fails_daily_publication_gate() -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    signals[0].evidence_links = []
    report = _daily_report(signals, datetime(2026, 8, 17, tzinfo=timezone.utc))
    assert report.quality_gate.passed is False
    assert "public_signal_evidence_links" in report.quality_gate.failed_checks
    assert report.report.status == "gate_failed"


def test_failed_optional_event_source_marks_report_degraded_not_blocked() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    report = build_daily_report_v3(
        signals=signals,
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={
            "paho_alerts": {
                "status": "failed",
                "error": "403 Forbidden",
            }
        },
        config=_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert report.quality_gate.passed is True
    assert report.quality_gate.status == "degraded"
    assert report.quality_gate.failed_checks == []
    assert "source_acquisition_paho_alerts" in report.quality_gate.warning_checks
    assert report.report.status == "published"


def test_explicitly_skipped_source_marks_report_degraded_not_fresh() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    report = build_daily_report_v3(
        signals=signals,
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={
            "who_don": {
                "status": "not_checked",
                "error": "External acquisition was disabled for this analysis run",
            }
        },
        config=_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert report.quality_gate.passed is True
    assert report.quality_gate.status == "degraded"
    assert "source_acquisition_who_don" in report.quality_gate.warning_checks
    assert report.sources[0].status == "not_checked"


def test_rare_count_tier_uses_exact_tail_and_supporting_cusum() -> None:
    frame = _weekly_frame()
    seasonal = 0.8 + 0.25 * np.sin(np.arange(len(frame)) * 2 * np.pi / 52)
    frame["value"] = np.maximum(0, np.round(seasonal)).astype(float)
    frame.loc[frame.index[-4:], "value"] = [4.0, 5.0, 6.0, 7.0]
    config = _config()
    config["v3"]["detector_tiers"] = {
        "rare_expected_max": 20,
        "rare_count_effect": {
            "minimum_current_cases": 5,
            "minimum_absolute_increase": 3,
            "minimum_relative_increase_pct": 100,
        },
        "cusum_reference": 0.5,
    }
    signals, rejected, _ = evaluate_frame_v3(
        frame,
        config,
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    assert rejected == {}
    signal = signals[0]
    assert signal.anomaly.detector_tier == "rare_count"
    assert signal.anomaly.diagnostics["rare_count_tail"] in {
        "poisson_predictive",
        "negative_binomial_predictive",
    }
    predictive_variance = signal.anomaly.diagnostics[
        "rare_tail_predictive_variance"
    ]
    process_only_variance = (
        signal.observation.expected * signal.anomaly.dispersion
    )
    assert predictive_variance >= process_only_variance
    legacy_plugin_p = situation_model._count_upper_tail_probability(
        signal.observation.current,
        signal.observation.expected,
        process_only_variance,
    )
    # The corrected tail does not condition on fitted parameters as if they
    # were known and does not reuse the robustly trimmed dispersion.
    assert signal.anomaly.raw_p_value >= legacy_plugin_p
    assert signal.anomaly.diagnostics["rare_tail_dispersion"] >= (
        signal.anomaly.dispersion
    )
    assert signal.anomaly.diagnostics[
        "rare_tail_aggregate_parameter_variance"
    ] == pytest.approx(
        signal.anomaly.diagnostics["aggregate_parameter_variance"]
        * signal.anomaly.diagnostics["rare_tail_dispersion"]
        / signal.anomaly.dispersion
    )
    assert signal.anomaly.diagnostics["rare_tail_parameter_uncertainty"] == (
        "delta_method_moment_matched"
    )
    assert signal.anomaly.diagnostics["supporting_cusum"]["decision_role"] == (
        "supporting_only"
    )
    assert signal.anomaly.effect_threshold_passed is True


def test_predictive_count_tail_matches_poisson_limit_and_expands_for_uncertainty() -> None:
    poisson_tail = situation_model._count_upper_tail_probability(15, 5, 5)
    predictive_tail = situation_model._count_upper_tail_probability(15, 5, 20)

    assert 0.0 < poisson_tail < predictive_tail < 1.0


def test_shadow_publication_requires_audited_signal_verification() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    config = _config()
    config["publication"] = {"mode": "shadow", "require_verified_signals": True}
    checked_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    unreviewed = build_daily_report_v3(
        signals=[signals[0].model_copy(deep=True)],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=config,
        checked_at=checked_at,
        registered_series_count=1,
    )
    assert unreviewed.signals == []
    verified = build_daily_report_v3(
        signals=[signals[0].model_copy(deep=True)],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=config,
        checked_at=checked_at,
        registered_series_count=1,
        signal_reviews={
            signals[0].identity.signal_id: {
                "action": "verify",
                "actor": "analyst@example.test",
                "note": "Source and identity verified",
                "created_at": checked_at,
                "payload": {},
            }
        },
    )
    assert len(verified.signals) == 1
    assert verified.signals[0].assessment.verification_status == "verified"
    assert verified.signals[0].assessment.verified_by.startswith("reviewer:")
    assert "@" not in verified.signals[0].assessment.verified_by
    assert verified.signals[0].assessment.public_health_risk.status == "not_assessed"


def test_guarded_auto_publishes_only_strict_primary_fit_signal() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    signal = signals[0]
    assert signal.anomaly.state == "strong"
    report = build_daily_report_v3(
        signals=[signal.model_copy(deep=True)],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_guarded_auto_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert len(report.signals) == 1
    assessment = report.signals[0].assessment
    assert assessment.verification_status == "verified"
    assert assessment.verification_basis == "automated_policy"
    assert assessment.verification_policy_version == "guarded_auto_v1"
    assert assessment.verified_by == "policy:guarded_auto_v1"
    assert assessment.public_health_risk.status == "not_assessed"


def test_guarded_auto_excludes_fallback_and_human_rejection_overrides_policy() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    fallback = signals[0].model_copy(deep=True)
    fallback.anomaly.fit_status = "fallback_completed"
    fallback.anomaly.model = "seasonal_empirical_fallback_v1"
    excluded = build_daily_report_v3(
        signals=[fallback],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_guarded_auto_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert excluded.signals == []

    signal = signals[0].model_copy(deep=True)
    rejected = build_daily_report_v3(
        signals=[signal],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_guarded_auto_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
        signal_reviews={
            signal.identity.signal_id: {
                "action": "reject",
                "actor": "analyst@example.test",
                "note": "Known source revision artifact",
                "created_at": datetime(2026, 8, 17, tzinfo=timezone.utc),
                "payload": {},
            }
        },
    )
    assert rejected.signals == []


def test_negative_calibration_queues_candidate_but_never_publishes_it() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    candidate = signals[0].model_copy(deep=True)
    report = build_daily_report_v3(
        signals=[candidate],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_exception_review_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert report.signals == []
    assert candidate.assessment.verification_status == "under_review"
    assert candidate.assessment.verification_basis == "not_verified"


@pytest.mark.parametrize(
    ("detector_tier", "fit_status", "expected_reason"),
    [
        ("rare_count", "completed", "rare_count_requires_official_match"),
        ("common_count", "fallback_completed", "fallback_fit_requires_official_match"),
    ],
)
def test_uncalibrated_exception_candidates_without_official_match_are_not_queued(
    detector_tier: str,
    fit_status: str,
    expected_reason: str,
) -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    candidate = signals[0].model_copy(deep=True)
    candidate.anomaly.detector_tier = detector_tier
    candidate.anomaly.fit_status = fit_status

    report = build_daily_report_v3(
        signals=[candidate],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=_exception_review_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )

    assert report.signals == []
    assert candidate.assessment.verification_status == "unreviewed"
    assert expected_reason in (candidate.assessment.verification_note or "")


def test_enabling_auto_verification_cannot_override_negative_calibration() -> None:
    config = _exception_review_config()
    config["publication"]["auto_verification"]["enabled"] = True
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    candidate = signals[0].model_copy(deep=True)
    report = build_daily_report_v3(
        signals=[candidate],
        ledger=ledger,
        rejected_reasons={},
        events=[],
        respiratory_cards=[],
        freshness={},
        config=config,
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
    )
    assert report.signals == []
    assert candidate.assessment.verification_status == "under_review"
    assert candidate.assessment.verification_basis == "not_verified"
    assert "disabled by calibration" in (candidate.assessment.verification_note or "")


def test_audited_expert_risk_is_final_after_official_event_context() -> None:
    frame = _weekly_frame()
    signals, _, ledger = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    checked_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    event = {
        "id": "official-update",
        "disease_id": "D_TEST",
        "disease_name": "Test disease",
        "published_at": "2026-08-12",
        "source": "WHO",
        "source_url": "https://example.test/official-update",
        "title": "Official update",
        "geographies": [{"code": "US", "name": "United States"}],
        "agency_risk": "moderate",
    }
    report = build_daily_report_v3(
        signals=[signals[0].model_copy(deep=True)],
        ledger=ledger,
        rejected_reasons={},
        events=[event],
        respiratory_cards=[],
        freshness={},
        config=_config(),
        checked_at=checked_at,
        registered_series_count=1,
        signal_reviews={
            signals[0].identity.signal_id: {
                "action": "verify",
                "actor": "analyst@example.test",
                "note": "Audited against current local evidence",
                "created_at": checked_at,
                "payload": {
                    "risk_level": "high",
                    "risk_rationale": "Audited expert assessment",
                    "evidence_url": "https://example.test/expert-assessment",
                },
            }
        },
    )
    assert len(report.signals) == 1
    risk = report.signals[0].assessment.public_health_risk
    assert risk.level == "high"
    assert risk.source == "audited_expert"
    assert risk.evidence_url == "https://example.test/expert-assessment"


def test_stable_event_cluster_id_is_applied_before_review_state() -> None:
    event = {
        "id": "official-update",
        "disease_id": "D_TEST",
        "disease_name": "Test disease",
        "published_at": "2026-08-12",
        "source": "WHO",
        "source_url": "https://example.test/official-update",
        "title": "Official update",
        "geographies": [{"code": "US", "name": "United States"}],
    }
    provisional_id = cluster_official_events([event])[0].cluster_id
    stable_id = "event-cluster:persisted"
    report = build_daily_report_v3(
        signals=[],
        ledger=[
            {
                "status": "modeled",
                "source_system": "SRC_TEST",
                "fit_status": "completed",
            }
        ],
        rejected_reasons={},
        events=[event],
        respiratory_cards=[],
        freshness={},
        config=_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=1,
        event_cluster_ids={provisional_id: stable_id},
        event_reviews={stable_id: {"review_state": "suppress"}},
    )
    assert report.events == []


def test_public_health_risk_cannot_be_inferred_without_attribution() -> None:
    with pytest.raises(ValidationError):
        PublicHealthRisk(status="not_assessed", level="high")
    risk = PublicHealthRisk(
        status="assessed",
        level="high",
        source="official_agency",
        rationale="Agency statement",
    )
    assert risk.level == "high"


def test_event_updates_cluster_on_disease_time_and_overlapping_geography() -> None:
    events = [
        {
            "id": "one",
            "disease_id": "D_EBOLA",
            "disease_name": "Ebola",
            "published_at": "2026-07-01",
            "source": "WHO",
            "source_url": "https://example.test/one",
            "title": "First update",
            "geographies": [{"code": "CD", "name": "DR Congo"}],
        },
        {
            "id": "two",
            "disease_id": "D_EBOLA",
            "disease_name": "Ebola",
            "published_at": "2026-07-28",
            "source": "WHO",
            "source_url": "https://example.test/two",
            "title": "Cross-border update",
            "geographies": [
                {"code": "CD", "name": "DR Congo"},
                {"code": "UG", "name": "Uganda"},
            ],
        },
    ]
    clusters = cluster_official_events(events)
    assert len(clusters) == 1
    assert {row["code"] for row in clusters[0].geographies} == {"CD", "UG"}
    assert len(clusters[0].updates) == 2


def test_suppressed_event_review_survives_acquisition_and_does_not_match_signal() -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    event = {
        "id": "official-update",
        "disease_id": "D_TEST",
        "disease_name": "Test disease",
        "published_at": "2026-08-12",
        "source": "WHO",
        "source_url": "https://example.test/official-update",
        "title": "Official update",
        "geographies": [{"code": "US", "name": "United States"}],
    }
    cluster_id = cluster_official_events([event])[0].cluster_id
    report = build_daily_report_v3(
        signals=[signal.model_copy(deep=True) for signal in signals],
        ledger=[
            {
                "status": "modeled",
                "source_system": signal.identity.source_system,
                "signal_id": signal.identity.signal_id,
            }
            for signal in signals
        ],
        rejected_reasons={},
        events=[event],
        respiratory_cards=[],
        freshness={},
        config=_config(),
        checked_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        registered_series_count=len(signals),
        event_reviews={cluster_id: {"review_state": "suppress"}},
    )
    assert report.events == []
    assert all("official_match" not in signal.tags for signal in report.signals)


def test_period_report_tracks_persistent_and_resolved_lifecycle() -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    active = [signal for signal in signals if signal.anomaly.state in {"alert", "strong"}]
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    first = _daily_report(active, start)
    second = _daily_report(active, start + timedelta(days=1))
    persistent = build_period_report_v3(
        [first, second],
        kind="weekly",
        period_key="2026-W33",
        period_start=date(2026, 8, 10),
        period_end=date(2026, 8, 16),
        as_of=start + timedelta(days=7),
        previous_active_signal_ids={active[0].identity.signal_id},
    )
    assert persistent.summary.persistent_count == 1
    assert persistent.summary.active_at_period_end_count == 1
    assert persistent.quality_gate.passed is False
    assert "daily_members_complete" in persistent.quality_gate.failed_checks
    empty = _daily_report([], start + timedelta(days=2))
    resolved = build_period_report_v3(
        [first, second, empty],
        kind="weekly",
        period_key="2026-W33",
        period_start=date(2026, 8, 10),
        period_end=date(2026, 8, 16),
        as_of=start + timedelta(days=7),
    )
    assert resolved.summary.resolved_count == 1
    assert resolved.summary.active_at_period_end_count == 0


def test_contract_rejects_duplicate_signal_ids() -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    report = _daily_report(signals, datetime(2026, 8, 17, tzinfo=timezone.utc))
    payload = report.model_dump(mode="json")
    payload["signals"] = [payload["signals"][0], payload["signals"][0]]
    payload["summary"]["unique_signal_count"] = 2
    with pytest.raises(ValidationError, match="unique signal_id"):
        SituationReportV3.model_validate(payload)


def test_backtest_simulation_is_reproducible_and_has_two_cycle_anomaly() -> None:
    first = simulate_weekly_batch(seed=7, series_per_class=2, periods=208)
    second = simulate_weekly_batch(seed=7, series_per_class=2, periods=208)
    pd.testing.assert_frame_equal(first.second_cycle, second.second_cycle)
    true_code = sorted(first.true_series)[0]
    null_values = first.null[first.null["series_code"] == true_code]["value"].to_numpy()
    outbreak_values = first.second_cycle[
        first.second_cycle["series_code"] == true_code
    ]["value"].to_numpy()
    assert not np.array_equal(null_values[-2:], outbreak_values[-2:])


def test_report_hash_includes_public_switch_and_method_config() -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    report = _daily_report(signals, datetime(2026, 8, 17, tzinfo=timezone.utc))
    original = persistence.report_content_hash(report)
    private = report.model_copy(deep=True)
    private.public_enabled = False
    assert persistence.report_content_hash(private) != original
    reconfigured = report.model_copy(deep=True)
    reconfigured.method.config_hash = "different-config"
    assert persistence.report_content_hash(reconfigured) != original


def test_analysis_run_id_is_idempotent_per_input_and_distinct_within_same_second() -> None:
    checked_at = datetime(2026, 8, 17, 7, 35, 53, tzinfo=timezone.utc)
    first = situation_pipeline._analysis_run_id(checked_at, "a" * 64)
    assert situation_pipeline._analysis_run_id(checked_at, "a" * 64) == first
    assert situation_pipeline._analysis_run_id(checked_at, "b" * 64) != first
    assert first == "situation-v3-run-20260817T073553Z-aaaaaaaaaa"


@pytest.mark.asyncio
async def test_history_archive_failure_stops_before_primary_publication(monkeypatch) -> None:
    frame = _weekly_frame()
    signals, _, _ = evaluate_frame_v3(
        frame,
        _config(),
        as_of=pd.Timestamp(frame["time"].max()).date(),
    )
    report = _daily_report(signals, datetime(2026, 8, 17, tzinfo=timezone.utc))
    report.report.status = "published"
    report.quality_gate.status = "passed"
    report.quality_gate.passed = True

    async def prepared(_report):
        return _report, "content-hash", True

    async def archive_failed(_report, _input_hash):
        raise RuntimeError("history unavailable")

    def primary_db_must_not_open():
        raise AssertionError("primary transaction opened before durable archive")

    monkeypatch.setattr(persistence, "prepare_report_revision_v3", prepared)
    monkeypatch.setattr(persistence, "archive_report_v3", archive_failed)
    monkeypatch.setattr(persistence, "get_db", primary_db_must_not_open)
    with pytest.raises(RuntimeError, match="history unavailable"):
        await persistence.publish_report_v3(report, run_ids=["run-1"], channel="latest")


@pytest.mark.asyncio
async def test_period_gate_cannot_overwrite_historical_member_run_status(monkeypatch) -> None:
    report = _daily_report([], datetime(2026, 8, 17, tzinfo=timezone.utc))
    report.report.status = "gate_failed"
    report.quality_gate.status = "failed"
    report.quality_gate.passed = False
    report.quality_gate.failed_checks = ["daily_members_complete"]
    historical = SimpleNamespace(status="published", quality_gate={"status": "passed"})
    staged = SimpleNamespace(status="staged", quality_gate={})

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class DB:
        def __init__(self):
            self.results = [historical, staged]

        async def execute(self, _statement):
            return Result(self.results.pop(0))

    @asynccontextmanager
    async def fake_get_db():
        yield DB()

    async def prepared(_report):
        return _report, "content-hash", True

    monkeypatch.setattr(persistence, "prepare_report_revision_v3", prepared)
    monkeypatch.setattr(persistence, "get_db", fake_get_db)
    _published, changed = await persistence.publish_report_v3(
        report,
        run_ids=["historical-run", "current-staged-run"],
        channel="weekly-latest",
    )

    assert changed is False
    assert historical.status == "published"
    assert historical.quality_gate == {"status": "passed"}
    assert staged.status == "gate_failed"
    assert staged.quality_gate["status"] == "failed"
