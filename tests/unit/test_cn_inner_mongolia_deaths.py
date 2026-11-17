from __future__ import annotations

from datetime import date

from src.data.crawlers.cn_provinces import (
    MONTHLY_REPORT_SOURCE_ID, _source_row, resolve_disease,
)
from src.data.crawlers.cn_province_adapters.inner_mongolia import parse_named_monthly_deaths
from src.data.processors.cn_provinces import expand_province_metric_rows
from src.data.storage.series_observation_store import SeriesObservationStore


def parse(text: str, *, report_date=date(2026, 8, 1), title="2026年8月份全区法定传染病疫情报告情况"):
    return parse_named_monthly_deaths(
        text, report_date=report_date, title=title,
        source_url="https://wjw.nmg.gov.cn/official-report.html",
        artifact_sha256="a" * 64, config_path="configs/cn_province_sources.json",
        resolve_disease=resolve_disease, source_row=_source_row,
        source_id=MONTHLY_REPORT_SOURCE_ID,
    )


def test_explicit_death_breakdown_imports_without_fabricating_cases():
    rows = parse("2026年8月，全区法定传染病报告发病总数10776例；死亡11例（艾滋病6例、丙肝3例、肺结核2例），较上月下降38.89%。")
    assert [(r["SourceDiseaseCode"], r["Cases"], r["Measure"]) for r in rows] == [
        ("aids__deaths", 6, "deaths"),
        ("hepatitis_c__deaths", 3, "deaths"),
        ("tuberculosis__deaths", 2, "deaths"),
    ]
    assert len(expand_province_metric_rows(rows)) == 3
    built = SeriesObservationStore().build_observations(rows, "CN", source_id=MONTHLY_REPORT_SOURCE_ID)
    assert not (built.skipped_invalid or built.skipped_unmatched or built.skipped_ambiguous)
    assert all(r["series_code"].startswith("SER_CN_PROV_REPORT_DEATHS_") for r in built.observations)


def test_nested_disease_name_and_partial_breakdown_preserve_only_named_values():
    rows = parse(
        "2025年9月，全区报告死亡10例（艾滋病6例，肺结核2例，丙肝1例，肝炎（未分型）1例），较上月下降。",
        report_date=date(2025, 9, 1), title="2025年9月份全区法定传染病疫情报告情况",
    )
    assert len(rows) == 4
    assert sum(r["Cases"] for r in rows) == 10
    assert any(r["SourceDiseaseCode"] == "unspecified_hepatitis__deaths" for r in rows)
    partial = parse(
        "2026年8月，全区报告死亡13例（艾滋病4例，肺结核3例，丙肝4例，乙肝1例），较上月下降。"
    )
    assert len(partial) == 4
    assert all(r["Raw"]["named_death_breakdown_complete"] is False for r in partial)
    trailing_comma = parse(
        "2024年7月，全区报告死亡20例（艾滋病8例，丙肝8例，肺结核3例，乙肝1例，），较上月上升。",
        report_date=date(2024, 7, 1), title="2024年7月份全区法定传染病疫情报告情况",
    )
    assert len(trailing_comma) == 4
    assert sum(r["Cases"] for r in trailing_comma) == 20


def test_rejects_ambiguous_death_breakdown_or_wrong_month():
    assert parse("2026年8月死亡3例（艾滋病4例），较上月下降。") == []
    assert parse("2026年8月死亡3例（艾滋病2例、未知病1例），较上月下降。") == []
    assert parse("去年同期死亡3例（艾滋病3例），较上月下降。", title="2026年7月份全区法定传染病疫情报告情况") == []
