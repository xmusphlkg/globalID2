from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.situation_room import load_config
from src.services.situation_v3 import backtest, persistence, reporting
from src.services.situation_v3.configuration import (
    calibration_definition_hash,
    validate_v32_config,
)
from src.services.situation_v3.labels import (
    EventLabelMatch,
    assign_temporal_splits,
    authoritative_source_url,
    match_signal_to_labels,
    summarize_locked_event_replay,
)
from scripts import import_situation_v3_event_labels
from scripts import register_situation_v3_calibration


def _monthly_signals():
    batch = backtest.simulate_batch(
        seed=321,
        cadence="monthly",
        periods=72,
        series_per_class=2,
        anomaly_factor=2.0,
        anomaly_duration_source_periods=2,
    )
    config = deepcopy(load_config())
    config["v3"]["maximum_analysis_workers"] = 1
    config["v3"]["detectors"]["multi_horizon"]["production_draws"] = 512
    as_of = backtest._simulation_as_of(batch.second_cycle, "monthly")
    _, signals = backtest._v3_analysis(batch.second_cycle, config, as_of)
    return batch, config, signals, as_of


def test_production_v32_config_is_strict_and_definition_hash_is_stable() -> None:
    config = deepcopy(load_config())
    validate_v32_config(config)
    assert calibration_definition_hash(config) == calibration_definition_hash(
        deepcopy(config)
    )

    legacy = deepcopy(config)
    legacy["v3"]["predictive_variance_inflation"] = 2.0
    with pytest.raises(ValueError, match="Unknown v3 configuration keys"):
        validate_v32_config(legacy)

    unsafe = deepcopy(config)
    unsafe["publication"]["auto_verification"].update(
        {
            "enabled": True,
            "mode": "live",
            "kill_switch": False,
            "calibration_hash": "artifact",
            "calibration_definition_hash": "definition",
        }
    )
    unsafe["publication"]["auto_verification"]["groups"][
        "weekly.common_count"
    ].update({"enabled": True, "maximum_q": 0.01})
    with pytest.raises(ValueError, match="allowed_source_systems"):
        validate_v32_config(unsafe)


def test_monthly_multi_horizon_simulation_is_reproducible() -> None:
    batch, config, first, as_of = _monthly_signals()
    _, second = backtest._v3_analysis(batch.second_cycle, config, as_of)
    first_by_id = {signal.identity.signal_id: signal for signal in first}
    second_by_id = {signal.identity.signal_id: signal for signal in second}

    assert first_by_id
    assert first_by_id.keys() == second_by_id.keys()
    for signal_id, signal in first_by_id.items():
        repeated = second_by_id[signal_id]
        if signal.anomaly.detector_tier != "common_count":
            continue
        assert signal.anomaly.model == "multi_horizon_gamma_poisson_v1"
        assert signal.anomaly.raw_p_value == repeated.anomaly.raw_p_value
        assert signal.anomaly.diagnostics["multi_horizon"] == repeated.anomaly.diagnostics[
            "multi_horizon"
        ]
        assert signal.observation.window_periods in {1, 2}


def test_future_weekly_rows_are_held_back_without_changing_inference() -> None:
    batch = backtest.simulate_weekly_batch(
        seed=77,
        periods=208,
        series_per_class=1,
    )
    config = deepcopy(load_config())
    config["v3"]["maximum_analysis_workers"] = 1
    config["v3"]["detectors"]["multi_horizon"]["production_draws"] = 512
    as_of = batch.null["time"].max().date()
    _, baseline = backtest._v3_analysis(batch.null, config, as_of)
    future = batch.null.groupby("series_code", sort=True).tail(1).copy()
    future["time"] = future["time"] + pd.Timedelta(days=7)
    future["value"] = 1_000_000.0
    with_future = pd.concat([batch.null, future], ignore_index=True)
    _, repeated = backtest._v3_analysis(with_future, config, as_of)

    baseline_by_id = {signal.identity.signal_id: signal for signal in baseline}
    repeated_by_id = {signal.identity.signal_id: signal for signal in repeated}
    assert baseline_by_id.keys() == repeated_by_id.keys()
    for signal_id, signal in baseline_by_id.items():
        held_back = repeated_by_id[signal_id]
        assert held_back.anomaly.raw_p_value == signal.anomaly.raw_p_value
        assert held_back.observation.current == signal.observation.current
        assert held_back.observation.data_status == "held_back"


def test_label_matching_uses_exact_disease_geography_and_signed_time() -> None:
    signal = SimpleNamespace(
        identity=SimpleNamespace(
            disease_id="D_TEST",
            country_code="US",
            canonical_geography_key="country:US:national",
            source_geography_keys=["national"],
            cadence="weekly",
        ),
        observation=SimpleNamespace(data_through=backtest.date(2026, 8, 10)),
    )
    label = {
        "label_id": "label-1",
        "disease_id": "D_TEST",
        "geographies": [{"code": "US"}],
        "first_official_published_at": "2026-08-17",
        "source_url": "https://www.who.int/event/1",
        "adjudication": "positive",
        "split": "locked_test",
    }
    matches = match_signal_to_labels(signal, [label], maximum_period_distance=2)

    assert [(match.relation, match.period_distance) for match in matches] == [
        ("lead", -1)
    ]
    assert not match_signal_to_labels(
        signal,
        [{**label, "disease_id": "D_OTHER"}],
        maximum_period_distance=2,
    )
    assert not match_signal_to_labels(
        signal,
        [{**label, "geographies": [{"code": "CA"}]}],
        maximum_period_distance=2,
    )
    assert authoritative_source_url(
        "https://sub.who.int/event/1", ["who.int"]
    )
    assert not authoritative_source_url(
        "https://who.int.attacker.example/event/1", ["who.int"]
    )


def test_temporal_split_has_two_period_embargo_and_locked_summary_excludes_unknowns() -> None:
    labels = [
        {
            "label_id": f"label-{index:02d}",
            "first_official_published_at": (
                backtest.date(2025, 1, 6) + timedelta(weeks=index)
            ),
            "adjudication": "positive",
        }
        for index in range(60)
    ]
    assignments = assign_temporal_splits(labels, cadence="weekly")
    assert set(assignments.values()) == {
        "development",
        "tuning",
        "locked_test",
        "unassigned",
    }
    assert assignments["label-42"] == "unassigned"
    assert assignments["label-51"] == "unassigned"

    replay_labels = [
        {**labels[55], "split": "locked_test"},
        {**labels[56], "split": "locked_test", "adjudication": "indeterminate"},
    ]
    match = EventLabelMatch(
        label_id="label-55",
        relation="lead",
        period_distance=-1,
        reference_date=labels[55]["first_official_published_at"],
        source_url="https://www.who.int/event/55",
    )
    summary = summarize_locked_event_replay(
        replay_labels,
        challenger_matches=[match],
        champion_matches=[],
    )
    assert summary["locked_positive_event_trials"] == 1
    assert summary["event_detection_rate"] == 1.0
    assert summary["leading_at_least_one_period_rate"] == 1.0


def test_calibration_registration_rejects_failed_artifact_even_when_groups_pass() -> None:
    stresses = {
        key: {"family_trials": 1}
        for key in (
            "zero_inflation",
            "correlated_series",
            "missing_periods",
            "revisions",
            "structural_break",
            "delayed_data",
        )
    }
    group = {
        "status": "supported",
        "maximum_q": 0.01,
        "thresholds": {
            "0.01": {
                "complete_null_family_trials": 384,
                "false_publication_rate_ci_95": {"upper": 0.02},
                "sustained_2x_sensitivity": 0.8,
                "median_detection_delay_periods": 1.0,
            }
        },
        "locked_real_event_metrics": {
            "event_detection_rate": 0.8,
            "champion_event_detection_rate": 0.8,
        },
        "weak_signal_improvement_vs_champion_pp": {"weekly.common_count": 15.0},
        "null_stress_strata": stresses,
    }
    artifact = {
        "method": register_situation_v3_calibration.EXPECTED_METHOD,
        "passed": False,
        "config_hash": "config-hash",
        "calibration_definition_hash": "definition-hash",
        "simulation_protocol_hash": "protocol-hash",
        "fdr_assessment": {"overall_calibration_decision": "passed"},
        "calibration_groups": {
            "weekly.common_count": group,
            "monthly.common_count": group,
        },
    }

    reasons = register_situation_v3_calibration._acceptance_failure_reasons(
        artifact,
        current_config_hash="config-hash",
        current_definition_hash="definition-hash",
    )

    assert reasons == ["artifact_failed"]


def test_event_label_import_recomputes_splits_with_existing_population() -> None:
    existing = [
        {
            "label_id": f"label-{index:02d}",
            "disease_id": "D_TEST",
            "geographies": [{"code": "US"}],
            "event_started_at": None,
            "first_official_published_at": backtest.date(2025, 1, 6)
            + timedelta(weeks=index),
            "authoritative_source": "WHO",
            "source_url": f"https://www.who.int/event/{index}",
            "confidence": "medium",
            "adjudication": "positive",
            "split": "development",
            "created_by": "seed",
            "evidence": {},
        }
        for index in range(10)
    ]
    incoming = [
        {
            **existing[0],
            "label_id": "label-new",
            "first_official_published_at": backtest.date(2025, 3, 17),
            "source_url": "https://www.who.int/event/new",
        }
    ]

    all_labels, imported = import_situation_v3_event_labels._assign_import_splits(
        imported_labels=incoming,
        existing_labels=existing,
        cadence="weekly",
        created_by="importer",
    )

    assert len(all_labels) == 11
    assert imported[0]["split"] == "unassigned"
    assert imported[0]["created_by"] == "importer"


@pytest.mark.asyncio
async def test_negative_event_label_requires_existing_review_or_two_adjudicators() -> None:
    with pytest.raises(ValueError, match="two distinct adjudicators"):
        await persistence.upsert_event_label_v3(
            label_id="negative-1",
            disease_id="D_TEST",
            geographies=[{"code": "US"}],
            first_official_published_at=backtest.date(2026, 8, 17),
            authoritative_source="adjudication",
            source_url="https://example.test/review",
            confidence="medium",
            adjudication="negative",
            evidence={"adjudicators": ["reviewer-one"]},
        )


def test_v32_shadow_canary_and_definition_drift_fail_closed() -> None:
    _, config, signals, as_of = _monthly_signals()
    signal = next(
        signal
        for signal in signals
        if signal.anomaly.detector_tier == "common_count"
    ).model_copy(deep=True)
    signal.anomaly.state = "strong"
    signal.anomaly.q_value = 0.001
    signal.anomaly.effect_threshold_passed = True
    signal.observation.completeness = 1.0
    signal.observation.data_status = "current"
    source = signal.identity.source_system
    source_url = signal.evidence_links[0].url
    policy = config["publication"]["auto_verification"]
    policy.update(
        {
            "enabled": True,
            "mode": "shadow",
            "kill_switch": False,
            "calibration_hash": "artifact-hash",
        }
    )
    group = policy["groups"]["monthly.common_count"]
    group.update(
        {
            "enabled": True,
            "maximum_q": 0.01,
            "complete_null_family_trials": 384,
            "false_publication_upper_95": 0.02,
            "sensitivity": 0.85,
            "median_detection_delay_periods": 1.0,
            "allowed_source_systems": [source],
            "canary_source_systems": [source],
            "authoritative_source_domains": ["example.invalid"],
        }
    )
    config["quality"]["source_evidence_urls"][source] = source_url
    policy["calibration_definition_hash"] = calibration_definition_hash(config)

    shadow = signal.model_copy(deep=True)
    reporting._apply_automatic_signal_verification(
        [shadow], config, reporting.datetime.combine(as_of, reporting.datetime.min.time(), tzinfo=reporting.timezone.utc)
    )
    assert shadow.assessment.automation_decision.status == "shadow"
    assert shadow.assessment.verification_status != "verified"

    config["publication"]["auto_verification"]["mode"] = "canary"
    canary = signal.model_copy(deep=True)
    reporting._apply_automatic_signal_verification(
        [canary], config, reporting.datetime.combine(as_of, reporting.datetime.min.time(), tzinfo=reporting.timezone.utc)
    )
    assert canary.assessment.automation_decision.status == "auto_verified"
    assert canary.assessment.verification_status == "verified"

    config["quality"]["source_evidence_urls"][source] = source_url + "/changed"
    drifted = signal.model_copy(deep=True)
    reporting._apply_automatic_signal_verification(
        [drifted], config, reporting.datetime.combine(as_of, reporting.datetime.min.time(), tzinfo=reporting.timezone.utc)
    )
    assert drifted.assessment.automation_decision.status == "blocked"
    assert "calibration_definition_hash_mismatch" in (
        drifted.assessment.automation_decision.gate_reasons
    )


def test_official_corroboration_requires_authoritative_domain_and_current_window() -> None:
    _, config, signals, _ = _monthly_signals()
    signal = signals[0].model_copy(deep=True)
    event_month = signal.observation.data_through.month % 12 + 1
    event_year = signal.observation.data_through.year + (
        signal.observation.data_through.month // 12
    )
    event = {
        "id": "official-1",
        "disease_id": signal.identity.disease_id,
        "disease_name": signal.identity.disease_name,
        "published_at": f"{event_year:04d}-{event_month:02d}-01",
        "source": "WHO",
        "source_url": "https://www.who.int/emergencies/event-1",
        "title": "Official update",
        "geographies": [{"code": signal.identity.country_code}],
    }
    config["publication"]["official_evidence"] = {
        "match_window_periods": 2,
        "authoritative_domains": ["who.int"],
    }
    clusters = reporting.cluster_official_events([event])
    reporting._apply_event_evidence([signal], [event], clusters, config)

    details = signal.anomaly.diagnostics["official_event_matches"]
    assert details[0]["role"] == "lead"
    assert details[0]["period_distance"] == -1
    assert signal.assessment.automation_decision.matched_event_ids

    bad_domain = {**event, "id": "bad-domain", "source_url": "https://who.int.attacker.test/event"}
    unmatched = signals[0].model_copy(deep=True)
    reporting._apply_event_evidence(
        [unmatched],
        [bad_domain],
        reporting.cluster_official_events([bad_domain]),
        config,
    )
    assert not unmatched.assessment.automation_decision.matched_event_ids

    stale = {**event, "id": "stale", "published_at": f"{event_year + 1:04d}-{event_month:02d}-01"}
    unmatched = signals[0].model_copy(deep=True)
    reporting._apply_event_evidence(
        [unmatched],
        [stale],
        reporting.cluster_official_events([stale]),
        config,
    )
    assert not unmatched.assessment.automation_decision.matched_event_ids
