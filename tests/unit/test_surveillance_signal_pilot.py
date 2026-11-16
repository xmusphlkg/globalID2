from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.services.surveillance_signal_pilot import SignalPolicy, evaluate_snapshot


def snapshot() -> dict:
    dates = [f"{year}-{month:02d}-01" for year in range(2020, 2027) for month in range(1, 13)]
    values = [100 if month in (6, 7, 8) else 10 for year in range(2020, 2027) for month in range(1, 13)]
    series = {
        "series_code": "source-a", "source_system": "fixture", "definition_version": "v1",
        "geography_key": "country:XX:national", "temporal_granularity": "monthly",
        "metric_type": "case_notifications", "unit": "count", "dates": dates,
        "values": values, "point_quality_statuses": ["final"] * len(dates),
    }
    return {"country_code": "XX", "generated_at": "2026-11-15T00:00:00Z",
            "disease_series": {"D001": {"source_series": [series]}}}


def series(data: dict) -> dict:
    return data["disease_series"]["D001"]["source_series"][0]


def latest(data: dict, **kwargs) -> dict:
    # Future rows are ignored; October is selected on November 1 only if the
    # incomplete November row is removed from the fixture.
    source = series(data)
    end = source["dates"].index("2026-11-01")
    for key in ("dates", "values", "point_quality_statuses"):
        source[key] = source[key][:end]
    return evaluate_snapshot(data, as_of=date(2026, 11, 15), **kwargs)["results"][0]


def test_signal_uses_same_month_and_requires_all_increase_gates():
    data = snapshot()
    series(data)["values"][series(data)["dates"].index("2026-10-01")] = 40
    result = latest(data)
    assert result["is_signal"] is True
    assert result["baseline_mean"] == 10
    assert result["threshold"] == 20
    assert result["baseline_count"] == 5
    assert all(value.endswith("-10-01") for value in result["baseline_periods"])


def test_seasonal_high_and_exact_threshold_are_not_signals():
    data = snapshot()
    source = series(data)
    end = source["dates"].index("2026-09-01")
    for key in ("dates", "values", "point_quality_statuses"):
        source[key] = source[key][:end]
    report = evaluate_snapshot(data, as_of=date(2026, 9, 15))
    assert report["results"][0]["observed_value"] == 100
    assert report["results"][0]["is_signal"] is False
    data = snapshot()
    source = series(data)
    source["values"][source["dates"].index("2026-10-01")] = 20
    assert latest(data)["is_signal"] is False


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True, 1.5, "40"])
def test_missing_invalid_or_non_count_observation_does_not_become_zero(value):
    data = snapshot()
    source = series(data)
    source["values"][source["dates"].index("2026-10-01")] = value
    result = latest(data)
    assert result["is_signal"] is None
    assert result["reason"] == "missing_or_unaccepted_quality"


@pytest.mark.parametrize("quality", ["provisional", "suppressed", "rejected", "", None])
def test_unaccepted_quality_cannot_trigger_signal(quality):
    data = snapshot()
    source = series(data)
    source["point_quality_statuses"][source["dates"].index("2026-10-01")] = quality
    assert latest(data)["is_signal"] is None


def test_missing_baseline_is_not_filled_and_sparse_history_is_rejected():
    data = snapshot()
    source = series(data)
    for year in (2021, 2022, 2023):
        source["values"][source["dates"].index(f"{year}-10-01")] = None
    result = latest(data)
    assert result["reason"] == "insufficient_same_month_history"
    assert result["baseline_count"] == 2
    data = snapshot()
    source = series(data)
    for month in (1, 2, 3):
        source["values"][source["dates"].index(f"2026-{month:02d}-01")] = None
    assert latest(data)["reason"] == "incomplete_recent_history"


def test_incomplete_and_stale_periods_are_explicit():
    data = snapshot()
    result = evaluate_snapshot(data, as_of=date(2026, 11, 15))["results"][0]
    assert result["period"] == "2026-11-01"
    assert result["reason"] == "incomplete_period"
    data = snapshot()
    source = series(data)
    end = source["dates"].index("2026-07-01")
    for key in ("dates", "values", "point_quality_statuses"):
        source[key] = source[key][:end]
    assert evaluate_snapshot(data, as_of=date(2026, 11, 15))["results"][0]["reason"] == "stale_observation"


def test_duplicates_and_inconsistent_arrays_are_not_aggregated():
    data = snapshot()
    source = series(data)
    source["dates"][1] = "2020-01-31"
    assert latest(data)["reason"] == "invalid_observations"
    data = snapshot()
    source = series(data)
    source["point_quality_statuses"].pop()
    result = evaluate_snapshot(data, as_of=date(2026, 11, 15))["results"][0]
    assert result["reason"] == "invalid_observations"


@pytest.mark.parametrize("field,value,reason", [
    ("temporal_granularity", "weekly", "unsupported_frequency"),
    ("metric_type", "laboratory_detections", "unsupported_metric"),
    ("unit", "percent", "unsupported_metric"),
    ("definition_version", None, "incomplete_source_identity"),
    ("definition_version", "source-current", "unversioned_definition"),
    ("comparability_break", "definition changed", "comparability_break"),
])
def test_ineligible_source_contracts_are_skipped(field, value, reason):
    data = snapshot()
    series(data)[field] = value
    assert latest(data)["reason"] == reason


def test_replay_has_no_future_value_leakage_and_does_not_claim_warning_accuracy():
    data = snapshot()
    future = deepcopy(data)
    source = series(future)
    for index, value in enumerate(source["dates"]):
        if value >= "2025-01-01":
            source["values"][index] = 100000
    earlier = evaluate_snapshot(data, as_of=date(2024, 12, 15), replay=True)
    assert earlier == evaluate_snapshot(future, as_of=date(2024, 12, 15), replay=True)
    assert earlier["mode"] == "retrospective_current_snapshot"
    assert earlier["official_confirmation"] is False
    assert "false_positive_rate" not in earlier["summary"]


def test_source_series_are_independent_and_input_is_unchanged():
    data = snapshot()
    second = deepcopy(series(data))
    second["series_code"] = "source-b"
    second["values"] = [100000] * len(second["values"])
    data["disease_series"]["D001"]["source_series"].append(second)
    original = deepcopy(data)
    report = evaluate_snapshot(data, as_of=date(2026, 11, 15), replay=True)
    assert data == original
    a = next(row for row in report["results"] if row.get("series_code") == "source-a" and row.get("status") == "within_threshold")
    assert a["baseline_mean"] in (10, 100)
    assert {row["series_code"] for row in report["results"]} == {"source-a", "source-b"}


def test_same_series_different_population_dimensions_remain_identifiable():
    data = snapshot()
    first = series(data)
    first.update(selection_key="source-a:male", dimension_key="sex:male", dimensions={"sex": "male"})
    end = first["dates"].index("2026-11-01")
    for key in ("dates", "values", "point_quality_statuses"):
        first[key] = first[key][:end]
    second = deepcopy(first)
    second.update(selection_key="source-a:female", dimension_key="sex:female", dimensions={"sex": "female"})
    first["values"][-1] = 40
    data["disease_series"]["D001"]["source_series"].append(second)
    rows = evaluate_snapshot(data, as_of=date(2026, 11, 15))["results"]
    assert {(row["selection_key"], row["dimension_key"], row["dimensions"]["sex"], row["is_signal"])
            for row in rows} == {("source-a:male", "sex:male", "male", True),
                                ("source-a:female", "sex:female", "female", False)}


def test_cli_reads_local_snapshot_and_returns_json(tmp_path: Path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot()))
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/evaluate_surveillance_signals.py"),
                             str(path), "--as-of", "2026-11-15"], cwd=tmp_path,
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["summary"]["skip_reasons"] == {"incomplete_period": 1}
    assert len(json.loads(result.stdout)["snapshot_sha256"]) == 64


@pytest.mark.parametrize("data", [None, [], {"country_code": "XX", "disease_series": {"D001": None}}])
def test_malformed_snapshot_has_a_clear_input_error(data):
    with pytest.raises(ValueError):
        evaluate_snapshot(data, as_of=date(2026, 11, 15))


@pytest.mark.parametrize("kwargs", [{"sigma": float("nan")}, {"sigma": 0},
                                   {"minimum_baseline_years": 2}, {"minimum_ratio": .5}])
def test_policy_rejects_invalid_thresholds(kwargs):
    with pytest.raises(ValueError):
        SignalPolicy(**kwargs)
