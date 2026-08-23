#!/usr/bin/env python3
"""Run and persist the deterministic Situation Room v3 calibration report."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_room import load_config  # noqa: E402
from src.services.situation_v3.backtest import (  # noqa: E402
    DEFAULT_SCENARIOS,
    run_backtest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batches",
        type=int,
        default=128,
        help="Independent batches per scenario (default: 128; 384 common families per cadence).",
    )
    parser.add_argument(
        "--series-per-class",
        type=int,
        default=16,
        help="Null and anomaly series per batch (default: 16 each).",
    )
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario.key for scenario in DEFAULT_SCENARIOS],
        help="Run only a named scenario; repeat to select more than one.",
    )
    parser.add_argument(
        "--minimum-complete-null-families",
        type=int,
        default=768,
        help="Minimum independent complete-null families required for a conclusive run.",
    )
    parser.add_argument(
        "--minimum-complete-null-families-per-cadence",
        type=int,
        default=384,
        help="Minimum independent common-count null families per cadence; values below 384 are rejected.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "validation" / "situation-v3-backtest.json",
    )
    args = parser.parse_args()
    selected_scenarios = tuple(
        scenario
        for scenario in DEFAULT_SCENARIOS
        if args.scenario is None or scenario.key in args.scenario
    )
    started_at = time.perf_counter()
    result = run_backtest(
        load_config(),
        batches=args.batches,
        series_per_class=args.series_per_class,
        seed=args.seed,
        scenarios=selected_scenarios,
        minimum_complete_null_families=args.minimum_complete_null_families,
        minimum_complete_null_families_per_cadence=(
            args.minimum_complete_null_families_per_cadence
        ),
    )
    elapsed_seconds = time.perf_counter() - started_at
    calls_per_batch = sum(
        (2 if scenario.anomaly_duration_source_periods == 1 else 3)
        + (1 if "common_" in scenario.key else 0)
        + (2 if scenario.key == "weekly_common_sustained_2x" else 0)
        for scenario in selected_scenarios
    )
    diagnostic_calls = int(result.get("diagnostic_model_evaluation_calls", 0))
    diagnostic_calls_per_batch = (
        diagnostic_calls / args.batches if args.batches else 0.0
    )
    observed_evaluation_calls = calls_per_batch * args.batches + 2 + diagnostic_calls
    projected_families = result["fdr_assessment"][
        "projected_family_trials_for_upper_95_lte_nominal"
    ]
    projected_batches = result["fdr_assessment"]["projected_batches_per_scenario"]
    guarded_auto_projected_families = result["guarded_auto"][
        "projected_family_trials_for_upper_95_lte_nominal"
    ]
    estimation_precision_families = result["fdr_assessment"][
        "estimation_precision_plan"
    ]["required_family_trials"]
    estimation_precision_batches = (
        None
        if estimation_precision_families is None
        else (
            estimation_precision_families + len(selected_scenarios) - 1
        )
        // len(selected_scenarios)
    )
    projected_full_calls = (
        None
        if projected_batches is None
        else round(
            (calls_per_batch + diagnostic_calls_per_batch) * projected_batches + 2
        )
    )
    result["execution"] = {
        "observed_wall_clock_seconds": round(elapsed_seconds, 3),
        "observed_model_evaluation_calls": observed_evaluation_calls,
        "projected_family_trials": projected_families,
        "projected_full_protocol_wall_clock_minutes": (
            None
            if projected_full_calls is None
            else round(
                elapsed_seconds
                * projected_full_calls
                / observed_evaluation_calls
                / 60.0,
                2,
            )
        ),
        "projected_null_only_wall_clock_minutes": (
            None
            if projected_families is None
            else round(
                elapsed_seconds
                * projected_families
                / observed_evaluation_calls
                / 60.0,
                2,
            )
        ),
        "guarded_auto_projected_null_only_wall_clock_minutes": (
            None
            if guarded_auto_projected_families is None
            else round(
                elapsed_seconds
                * guarded_auto_projected_families
                / observed_evaluation_calls
                / 60.0,
                2,
            )
        ),
        "estimation_precision_projected_full_wall_clock_minutes": (
            None
            if estimation_precision_batches is None
            else round(
                elapsed_seconds
                * (
                    (calls_per_batch + diagnostic_calls_per_batch)
                    * estimation_precision_batches
                    + 2
                )
                / observed_evaluation_calls
                / 60.0,
                2,
            )
        ),
        "estimation_precision_projected_null_only_wall_clock_minutes": (
            None
            if estimation_precision_families is None
            else round(
                elapsed_seconds
                * estimation_precision_families
                / observed_evaluation_calls
                / 60.0,
                2,
            )
        ),
        "runtime_projection_note": (
            "Hardware-specific linear scaling from this run's model-evaluation count; "
            "the null-only estimate excludes anomaly and v2 comparator evaluations."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
