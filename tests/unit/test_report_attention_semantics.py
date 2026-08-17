from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.generation.report_figures import ReportFigureLibrary, ReportFigureSpec
from src.generation.report_v4.composer import compose_report_document
from src.generation.report_v4.dataset import SourcePolicy
from src.generation.report_v4.evidence import build_evidence_packet


def _packet() -> dict:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 28, tzinfo=timezone.utc)
    data = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-01", "cases": 10, "deaths": 0, "data_source": "fixture"},
            {"disease_id": 1, "time": "2026-02-01", "cases": 35, "deaths": 1, "data_source": "fixture"},
            {"disease_id": 2, "time": "2026-01-01", "cases": 20, "deaths": 0, "data_source": "fixture"},
            {"disease_id": 2, "time": "2026-02-01", "cases": 18, "deaths": 0, "data_source": "fixture"},
        ]
    )
    return build_evidence_packet(
        data=data,
        historical_data=data,
        country={"id": 1, "code": "XX", "name_zh": "测试地区", "name_en": "Testland"},
        diseases={
            1: {"code": "D001", "name_zh": "甲病", "name_en": "Disease A"},
            2: {"code": "D002", "name_zh": "乙病", "name_en": "Disease B"},
        },
        period_start=start,
        period_end=end,
        source_policy=SourcePolicy(death_counts="reported", case_scope="national", rate_basis="unavailable"),
    )


def test_attention_score_is_canonical_and_legacy_risk_keys_are_aliases() -> None:
    packet = _packet()

    assert packet["method_version"] == "report_v4.1"
    assert packet["score_semantics"]["type"] == "surveillance_attention_priority"
    assert "not a public-health risk estimate" in packet["score_semantics"]["description"]["en"]
    assert packet["attention_ranking"] == packet["risk_ranking"]
    assert packet["summary_metrics"]["high_attention_diseases"] == packet["summary_metrics"]["high_risk_diseases"]

    disease = packet["diseases"][0]
    assert disease["attention"]["score"] == disease["risk"]["score"]
    assert disease["risk"]["deprecated"] is True
    assert disease["risk"]["alias_for"] == "attention"
    assert disease["attention_rank"] == disease["risk_rank"]
    assert packet["attention_ranking"][0]["evidence_refs"][0].endswith(".attention_score")


def test_report_and_figures_present_review_priority_not_a_risk_rating() -> None:
    packet = _packet()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 28, tzinfo=timezone.utc)
    document = compose_report_document(
        evidence_packet=packet,
        country={"name_zh": "测试地区", "name_en": "Testland"},
        period_start=start,
        period_end=end,
    ).to_dict()

    assert document["attention_ranking"] == document["risk_ranking"]
    assert document["score_semantics"]["type"] == "surveillance_attention_priority"
    directory = document["disease_directory"][0]
    assert directory["attention_score"] == directory["risk_score"]
    assert directory["attention_level"] == directory["risk_level"]

    rendered_zh = "\n".join(
        [document["summary"]["zh"], *document["key_findings"]["zh"]]
        + [section["body"]["zh"] for section in document["sections"]]
        + [section["content_i18n"]["zh"] for section in directory["analysis_sections"]]
    )
    rendered_en = "\n".join(
        [document["summary"]["en"], *document["key_findings"]["en"]]
        + [section["body"]["en"] for section in document["sections"]]
        + [section["content_i18n"]["en"] for section in directory["analysis_sections"]]
    )
    assert "风险等级" not in rendered_zh
    assert "监测关注优先级" in rendered_zh
    assert "不代表公共卫生风险" in rendered_zh
    assert " risk (" not in rendered_en
    assert "surveillance attention priority" in rendered_en
    assert "not a public-health risk" in rendered_en

    library = ReportFigureLibrary()
    specs = [
        ReportFigureSpec(figure_type="risk_ranking_bar", section_type="priority_signals"),
        ReportFigureSpec(figure_type="risk_matrix", section_type="priority_signals"),
    ]
    figures = library.render_figures(packet=packet, specs=specs, language="en")
    figure_data = library.build_figure_data(packet=packet, figures=figures, language="en")
    assert figure_data["attention_ranking"] == figure_data["risk_ranking"]
    assert all("attention_score" in row for row in figure_data["attention_ranking"])
    assert "attention" in figures[0]["title"].lower()
    assert "not a public-health risk estimate" in figures[0]["caption"]
