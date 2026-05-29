from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.ai.agents.deep_analyst import DeepAnalystAgent
from src.generation.evidence import EvidenceAnalyzer, ReportFactChecker
from src.generation.generator import ReportGenerator
from src.generation.report_figures import ReportFigureLibrary


def _country() -> dict:
    return {"id": 1, "code": "TST", "name": "Testland"}


def _diseases() -> dict[int, dict]:
    return {
        1: {"code": "flu", "name_en": "Influenza", "name_zh": "流感", "category": "respiratory"},
        2: {"code": "measles", "name_en": "Measles", "name_zh": "麻疹", "category": "vaccine-preventable"},
    }


def _period() -> tuple[datetime, datetime]:
    return (
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 5, 31, tzinfo=timezone.utc),
    )


def test_evidence_packet_infers_monthly_with_duplicate_disease_dates_and_flags_anomaly() -> None:
    period_start, period_end = _period()
    dates = pd.date_range("2025-01-01", periods=5, freq="MS", tz="UTC")
    rows = []
    for cases, when in zip([10, 12, 9, 11, 100], dates):
        rows.append({"disease_id": 1, "time": when, "cases": cases, "deaths": 1, "data_source": "fixture"})
    for cases, when in zip([3, 4, 3, 4, 5], dates):
        rows.append({"disease_id": 2, "time": when, "cases": cases, "deaths": 0, "data_source": "fixture"})

    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(rows),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    flu = next(item for item in packet["diseases"] if item["disease_id"] == "flu")
    assert packet["reporting_cadence"] == "monthly"
    assert packet["summary_metrics"]["total_cases"] == 161
    assert flu["metrics"]["latest_cases"] == 100
    assert flu["metrics"]["previous_cases"] == 11
    assert flu["metrics"]["change_pct"] == 809.09
    assert flu["metrics"]["rolling_mean_cases"] == 40.0
    assert flu["anomaly"]["is_anomaly"] is True
    assert packet["risk_ranking"][0]["disease_id"] == "flu"
    assert packet["data_quality"]["score"] == 1.0
    assert packet["data_signature"]


def test_evidence_packet_handles_weekly_series() -> None:
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 2, 28, tzinfo=timezone.utc)
    dates = pd.date_range("2026-01-05", periods=6, freq="W-MON", tz="UTC")
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {"disease_id": 1, "time": when, "cases": cases, "deaths": 0, "data_source": "fixture"}
                for when, cases in zip(dates, [4, 5, 6, 7, 8, 9])
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    disease = packet["diseases"][0]
    assert packet["reporting_cadence"] == "weekly"
    assert disease["observation_count"] == 6
    assert disease["metrics"]["latest_cases"] == 9
    assert disease["metrics"]["previous_cases"] == 8


def test_evidence_packet_handles_single_zero_point_without_crashing() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {
                    "disease_id": 1,
                    "time": "2025-05-01",
                    "cases": 0,
                    "deaths": 0,
                    "data_source": "fixture",
                }
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    disease = packet["diseases"][0]
    assert packet["summary_metrics"]["total_cases"] == 0
    assert disease["metrics"]["previous_cases"] is None
    assert disease["metrics"]["change_pct"] is None
    assert disease["anomaly"]["is_anomaly"] is False
    assert disease["risk"]["level"] == "low"
    assert disease["data_quality"]["confidence"] == "high"


def test_evidence_packet_handles_empty_data() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    assert packet["summary_metrics"]["record_count"] == 0
    assert packet["diseases"] == []
    assert packet["data_quality"]["score"] == 0.0
    assert "No records available" in packet["data_quality"]["issues"]


def test_data_signature_is_stable_and_changes_when_input_changes() -> None:
    period_start, period_end = _period()
    rows = [
        {"disease_id": 1, "time": "2025-05-01", "cases": 7, "deaths": 0, "data_source": "fixture"},
        {"disease_id": 2, "time": "2025-05-01", "cases": 3, "deaths": 0, "data_source": "fixture"},
    ]
    analyzer = EvidenceAnalyzer()
    first = analyzer.build_packet(
        data=pd.DataFrame(rows),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )
    second = analyzer.build_packet(
        data=pd.DataFrame(list(reversed(rows))),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )
    changed = analyzer.build_packet(
        data=pd.DataFrame([{**row, "cases": row["cases"] + 1} for row in rows]),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    assert first["data_signature"] == second["data_signature"]
    assert first["data_signature"] != changed["data_signature"]


def test_data_quality_flags_negative_values_and_future_records() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {"disease_id": 1, "time": "2025-06-15", "cases": -10, "deaths": 0, "data_source": "fixture"},
                {"disease_id": 1, "time": "2025-05-01", "cases": 3, "deaths": 0, "data_source": "fixture"},
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    assert packet["data_quality"]["negative_value_count"] == 1
    assert packet["data_quality"]["future_record_count"] == 1
    assert packet["data_quality"]["score"] < 1.0
    assert any("negative" in issue for issue in packet["data_quality"]["issues"])
    assert any("after the report period end" in issue for issue in packet["data_quality"]["issues"])


def test_data_quality_penalizes_unavailable_rate_fields() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {
                    "disease_id": 1,
                    "time": "2025-05-01",
                    "cases": 10,
                    "deaths": 0,
                    "incidence_rate": None,
                    "mortality_rate": None,
                    "data_source": "fixture",
                },
                {
                    "disease_id": 1,
                    "time": "2025-04-01",
                    "cases": 8,
                    "deaths": 0,
                    "incidence_rate": None,
                    "mortality_rate": None,
                    "data_source": "fixture",
                },
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    assert packet["data_quality"]["score"] == 0.9
    assert any("count-based" in issue for issue in packet["data_quality"]["issues"])


def test_evidence_packet_uses_computed_crude_incidence_rates() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {
                    "disease_id": 1,
                    "time": "2025-04-01",
                    "cases": 10,
                    "deaths": 0,
                    "incidence_rate": 1.0,
                    "incidence_rate_source": "wpp_computed_crude",
                    "population_denominator": 1_000_000,
                    "population_year": 2025,
                    "population_source": "WPP",
                    "data_source": "fixture sentinel",
                },
                {
                    "disease_id": 1,
                    "time": "2025-05-01",
                    "cases": 12,
                    "deaths": 0,
                    "incidence_rate": 1.2,
                    "incidence_rate_source": "wpp_computed_crude",
                    "population_denominator": 1_000_000,
                    "population_year": 2025,
                    "population_source": "WPP",
                    "data_source": "fixture sentinel",
                },
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    disease = packet["diseases"][0]
    assert disease["metrics"]["latest_incidence_rate_per_100k"] == 1.2
    assert disease["metrics"]["period_crude_incidence_per_100k"] == 2.2
    assert disease["metrics"]["incidence_rate_sources"] == {"wpp_computed_crude": 2}
    assert packet["evidence_index"]["disease:flu.latest_incidence_rate_per_100k"] == 1.2
    assert any("sentinel per-site rate" in item for item in disease["limitations"])


def test_report_figure_library_plans_and_renders_evidence_bound_figures() -> None:
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 4, 30, tzinfo=timezone.utc)
    dates = pd.date_range("2025-01-01", periods=16, freq="MS", tz="UTC")
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {
                    "disease_id": 1,
                    "time": when,
                    "cases": cases,
                    "deaths": 0,
                    "incidence_rate": cases / 10.0,
                    "incidence_rate_source": "fixture_rate",
                    "data_source": "fixture",
                }
                for when, cases in zip(dates, [4, 5, 6, 8, 9, 11, 13, 16, 20, 24, 30, 36, 43, 52, 64, 78])
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    library = ReportFigureLibrary()
    specs = library.plan_figures(packet=packet, deep_analysis={}, language="en")
    planned_types = {spec.figure_type for spec in specs}
    ai_specs = library.plan_figures(
        packet=packet,
        deep_analysis={
            "figure_plan": [
                {
                    "figure_type": "recent_window_heatmap",
                    "section_type": "trend_anomaly_analysis",
                    "disease_id": "flu",
                    "position": "after_content",
                    "rationale": "Show recent clustering.",
                }
            ]
        },
        language="en",
    )

    assert "epidemic_curve" in planned_types
    assert "signal_context_panel" in planned_types
    assert "seasonal_baseline_band" in planned_types
    assert "anomaly_marker_curve" in planned_types
    assert "recent_window_heatmap" in planned_types
    assert "cases_incidence_panel" in planned_types
    assert "data_quality_timeline" in planned_types
    assert ai_specs[0].source == "ai"
    assert ai_specs[0].figure_type == "recent_window_heatmap"

    figures = library.render_figures(packet=packet, specs=specs, language="en")
    figure_types = {figure["figure_type"] for figure in figures}
    assert {
        "epidemic_curve",
        "signal_context_panel",
        "seasonal_baseline_band",
        "anomaly_marker_curve",
        "recent_window_heatmap",
        "cases_incidence_panel",
        "data_quality_timeline",
    } <= figure_types
    assert all(figure.get("caption") for figure in figures)
    assert all(figure.get("legend") for figure in figures)
    assert all(figure.get("renderer") == "echarts" for figure in figures)
    assert all(figure.get("data_key") for figure in figures)
    assert all("option" not in figure for figure in figures)
    assert all("html" not in figure for figure in figures)
    figure_data = library.build_figure_data(packet=packet, figures=figures, language="en")
    assert "disease:flu" in figure_data["series"]
    assert figure_data["series"]["disease:flu"]["cases"][-1] == 78
    assert figure_data["series"]["disease:flu"]["visual"]["latest_4_period_cases"] == 237
    assert "derived" in figure_data["series"]["disease:flu"]["visual"]
    assert len(figure_data["series"]["disease:flu"]["visual"]["derived"]["baseline_upper"]) == 16
    assert figure_data["series"]["disease:flu"]["visual"]["derived"]["availability"]["incidence_rate_per_100k"][-1] == 1
    assert len(figure_data["series"]) == 1
    assert "html" not in ReportFigureLibrary.strip_html(figures)[0]
    assert "option" not in ReportFigureLibrary.compact_specs(figures)[0]


def test_fact_checker_blocks_wrong_numbers_future_dates_and_unsupported_causality() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {"disease_id": 1, "time": "2025-05-01", "cases": 100, "deaths": 2, "data_source": "fixture"},
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    gate = ReportFactChecker().check_report(
        sections=[
            {
                "title": "Executive Brief",
                "content": "Influenza has 100 cases, 99999 extra cases, and a signal on 2026-01-01 caused by mobility.",
                "metadata": {"evidence_refs": ["disease:flu.latest_cases"]},
            }
        ],
        evidence_packet=packet,
        quality_threshold=0.85,
    )

    codes = {issue["code"] for issue in gate["issues"]}
    assert gate["passed"] is False
    assert "number_not_in_evidence" in codes
    assert "date_after_report_period" in codes
    assert "unsupported_causal_language" in codes


def test_fact_checker_requires_section_evidence_refs() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {"disease_id": 1, "time": "2025-05-01", "cases": 7, "deaths": 0, "data_source": "fixture"},
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )

    issues = ReportFactChecker().check_section(
        {"title": "No refs", "content": "Influenza has 7 cases.", "metadata": {}},
        packet,
    )

    assert any(issue["code"] == "missing_evidence_refs" for issue in issues)


def test_rendered_v3_sections_pass_fact_checker_with_fallback_analysis() -> None:
    period_start, period_end = _period()
    packet = EvidenceAnalyzer().build_packet(
        data=pd.DataFrame(
            [
                {"disease_id": 1, "time": "2025-03-01", "cases": 10, "deaths": 0, "data_source": "fixture"},
                {"disease_id": 1, "time": "2025-04-01", "cases": 12, "deaths": 0, "data_source": "fixture"},
                {"disease_id": 1, "time": "2025-05-01", "cases": 11, "deaths": 0, "data_source": "fixture"},
            ]
        ),
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
    )
    deep_analysis = DeepAnalystAgent.fallback(packet, language="en")
    sections = ReportGenerator()._build_analytical_v3_section_payloads(
        evidence_packet=packet,
        deep_analysis=deep_analysis,
        language="en",
    )

    gate = ReportFactChecker().check_report(
        sections=sections,
        evidence_packet=packet,
        quality_threshold=0.85,
        deep_confidence=deep_analysis["confidence"],
    )

    assert len(sections) == 7
    assert gate["passed"] is True
    assert gate["issues"] == []
    assert all(not section["content"].lstrip().startswith("## ") for section in sections)
