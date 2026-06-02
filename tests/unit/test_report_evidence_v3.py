from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.generation.report_v4.composer import _trend_signal, compose_report_document
from src.generation.report_v4.dataset import DatasetBuilder, SourcePolicy
from src.generation.report_v4.evidence import build_evidence_packet
from src.generation.report_v4.quality import ReportV4QualityGate


def _period() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 28, tzinfo=timezone.utc),
    )


def _country() -> dict:
    return {
        "id": 1,
        "code": "JP",
        "name": "日本",
        "name_zh": "日本",
        "name_en": "Japan",
        "name_local": "日本",
    }


def _diseases() -> dict[int, dict]:
    return {
        1: {
            "code": "D048",
            "name": "D048",
            "name_en": "Hand, foot and mouth disease",
            "name_zh": "手足口病",
            "category": "Viral",
        }
    }


def test_report_v4_treats_case_only_zero_deaths_as_not_reported() -> None:
    period_start, period_end = _period()
    data = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-05", "cases": 12, "deaths": 0, "data_source": "Japan NIID Weekly"},
            {"disease_id": 1, "time": "2026-01-12", "cases": 18, "deaths": 0, "data_source": "Japan NIID Weekly"},
        ]
    )

    normalized, _, policy = DatasetBuilder().normalize(data, country_code="JP")
    packet = build_evidence_packet(
        data=normalized,
        historical_data=normalized,
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
        source_policy=policy,
    )

    reporting = packet["death_reporting"]
    assert reporting["status"] == "not_reported"
    assert reporting["total_deaths"] is None
    assert "未提供死亡数字段" in reporting["display_note"]["zh"]


def test_report_v4_preserves_true_reported_zero_deaths() -> None:
    period_start, period_end = _period()
    data = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-01", "cases": 10, "deaths": 0, "data_source": "fixture"},
            {"disease_id": 1, "time": "2026-02-01", "cases": 8, "deaths": 0, "data_source": "fixture"},
        ]
    )

    packet = build_evidence_packet(
        data=data,
        historical_data=data,
        country={**_country(), "code": "CN"},
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
        source_policy=SourcePolicy(death_counts="reported", case_scope="national", rate_basis="source_rate"),
    )

    reporting = packet["death_reporting"]
    assert reporting["status"] == "reported_zero"
    assert reporting["total_deaths"] == 0


def test_report_v4_quality_gate_rejects_mixed_language_and_bad_death_claims() -> None:
    document = {
        "schema_version": "report_v4.0",
        "default_locale": "zh",
        "locales": ["zh", "en"],
        "title": {"zh": "日本监测简报 / Situation Brief", "en": "Japan decision brief"},
        "summary": {
            "zh": "本段是中文，但错误声称无死亡。",
            "en": "Japan report summary.",
        },
        "key_findings": {"zh": ["无死亡"], "en": ["No deaths"]},
        "sections": [
            {
                "id": "decision_summary",
                "type": "decision_summary",
                "order": 1,
                "title": {"zh": "当前判断", "en": "Current judgement"},
                "body": {"zh": "- 无死亡", "en": "- No deaths"},
            }
        ],
        "metrics": {},
        "death_reporting": {"status": "not_reported"},
        "data_quality": {},
    }

    gate = ReportV4QualityGate().check(document)
    assert gate["passed"] is False
    assert {issue["code"] for issue in gate["issues"]} >= {"locale_contract", "death_scope"}


def test_report_v4_composer_outputs_locale_first_contract_without_english_marker() -> None:
    period_start, period_end = _period()
    data = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-05", "cases": 12, "deaths": pd.NA, "data_source": "Japan NIID Weekly"},
            {"disease_id": 1, "time": "2026-01-12", "cases": 24, "deaths": pd.NA, "data_source": "Japan NIID Weekly"},
        ]
    )
    packet = build_evidence_packet(
        data=data,
        historical_data=data,
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
        source_policy=SourcePolicy(death_counts="not_reported", case_scope="sentinel", rate_basis="unavailable"),
    )
    document = compose_report_document(
        evidence_packet=packet,
        country=_country(),
        period_start=period_start,
        period_end=period_end,
    ).to_dict()

    assert document["schema_version"] == "report_v4.0"
    assert document["default_locale"] == "zh"
    assert document["title"]["zh"]
    assert document["title"]["en"]
    assert document["disease_directory"]
    assert document["disease_directory"][0]["disease_id"] == "D048"
    assert document["disease_directory"][0]["slug"] == "hand-foot-and-mouth-disease"
    assert document["disease_directory"][0]["current_movement"]
    assert document["disease_directory"][0]["seasonal_position"]
    assert document["disease_directory"][0]["trend_basis"]
    assert document["disease_directory"][0]["analysis_sections"]
    assert "最新一期报告" in document["disease_directory"][0]["analysis_sections"][0]["content_i18n"]["zh"]
    rendered_zh = "\n".join(
        [document["summary"]["zh"], *document["key_findings"]["zh"]]
        + [section["body"]["zh"] for section in document["sections"]]
    )
    assert "### English" not in rendered_zh
    assert " / Situation Brief" not in rendered_zh
    assert "数据置信度：medium" not in rendered_zh
    assert "病例口径：sentinel" not in rendered_zh
    assert "发病率口径：unavailable" not in rendered_zh
    assert "报告频率：weekly" not in rendered_zh
    assert "无死亡" not in rendered_zh
    ReportV4QualityGate().ensure_passed(document)


def test_report_v4_quality_gate_passes_valid_document() -> None:
    period_start, period_end = _period()
    data = pd.DataFrame(
        [{"disease_id": 1, "time": "2026-01-05", "cases": 12, "deaths": pd.NA, "data_source": "fixture"}]
    )
    packet = build_evidence_packet(
        data=data,
        historical_data=data,
        country=_country(),
        diseases=_diseases(),
        period_start=period_start,
        period_end=period_end,
        source_policy=SourcePolicy(death_counts="not_reported", case_scope="sentinel", rate_basis="unavailable"),
    )
    document = compose_report_document(
        evidence_packet=packet,
        country=_country(),
        period_start=period_start,
        period_end=period_end,
    ).to_dict()

    result = ReportV4QualityGate().ensure_passed(document)
    assert result["passed"] is True
    assert result["schema_version"] == "report_v4.0"


def test_report_v4_trend_signal_separates_low_base_and_rebound() -> None:
    low_base = _trend_signal(
        latest_cases=7,
        previous_cases=2,
        mom_change_pct=250.0,
        recent_change_pct=-48.0,
        yoy_change_pct=600.0,
        long_window_change_pct=26.0,
        historical_context={"observation_count": 100, "same_season_baseline_count": 5},
    )
    assert low_base["trend"]["status"] == "low_base_fluctuation"
    assert low_base["trend"]["direction"] == "watch"

    rebound = _trend_signal(
        latest_cases=133,
        previous_cases=39,
        mom_change_pct=241.03,
        recent_change_pct=3.13,
        yoy_change_pct=-94.29,
        long_window_change_pct=-63.16,
        historical_context={"observation_count": 100, "same_season_baseline_count": 5},
    )
    assert rebound["trend"]["status"] == "short_term_rebound"
    assert rebound["trend"]["zh"] == "短期反弹"
    assert rebound["current_movement"]["direction"] == "up"
    assert rebound["seasonal_position"]["direction"] == "down"


def test_report_v4_quality_gate_raises_for_invalid_document() -> None:
    with pytest.raises(ValueError):
        ReportV4QualityGate().ensure_passed({"schema_version": "report_v4.0"})
