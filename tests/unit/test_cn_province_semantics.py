"""Regression examples from official province bulletins and ambiguity counterexamples."""
from datetime import date

import pandas as pd
import pytest

from src.data.crawlers.cn_province_adapters.parsing.normalization import (
    _normalize_report_table,
)
from src.data.crawlers.cn_provinces import (
    MonthlyReportLink,
    _deduplicate_source_rows,
    _normalize_narrative_report,
)


def narrative(text: str, *, province: str = "CN-SX", month: int = 10) -> dict[str, dict]:
    link = MonthlyReportLink(province, date(2026, month, 1), f"2026年{month}月传染病疫情", "https://example.test/report")
    return {
        row["SourceDiseaseCode"]: row
        for row in _normalize_narrative_report(
            text, link=link, source_url=link.url, artifact_sha256="semantic-regression",
        )
    }


@pytest.mark.parametrize("text,expected", [
    ("无甲型肝炎病例报告。", {"hepatitis_a"}),
    ("无甲型肝炎、乙型肝炎病例报告。", {"hepatitis_a", "hepatitis_b"}),
    ("无皮肤炭疽病例报告。", {"cutaneous_anthrax"}),
    ("无埃博拉出血热病例报告。", {"ebola"}),
    ("无鼠疫、霍乱等2种甲类传染病报告。", {"plague", "cholera"}),
])
def test_zero_statement_uses_longest_nonoverlapping_names(text, expected):
    rows = narrative(text)
    assert set(rows) == expected
    assert all(row["Cases"] == 0 for row in rows.values())


def test_shanxi_october_ebola_zero_does_not_claim_hfrs_zero():
    # https://wjw.shanxi.gov.cn/zfxxgk/fdzdgknr/yqfb/202611/t20261112_10258428.shtml
    text = (
        "传染性非典型肺炎、脊髓灰质炎、人感染新亚型流感、麻疹、狂犬病、登革热、猴痘、"
        "基孔肯雅热、发热伴血小板减少综合征、流脑、白喉、新生儿破伤风、钩体病、血吸虫病无发病、死亡报告。"
        "中东呼吸综合征（MERS）、埃博拉出血热、寨卡病毒病、拉沙热无发病、死亡报告。"
    )
    rows = narrative(text)
    assert "hfrs" not in rows
    assert {"ebola", "mers", "zika", "lassa_fever"} <= rows.keys()
    assert len(rows) == 18


def test_yunnan_explicit_no_case_and_death_statement():
    # https://www.yncdc.cn/jkzt/jkts/crb/2026/17205.shtml
    statement = (
        "传染性非典型肺炎、脊髓灰质炎、狂犬病、流行性乙型脑炎、"
        "基孔肯雅热、发热伴血小板减少综合征、流行性脑脊髓膜炎、"
        "白喉、新生儿破伤风、钩体病、血吸虫病无发病死亡报告。"
    )
    rows = narrative(statement, province="CN-YN", month=5)
    assert len(rows) == 11
    assert all(row["Cases"] == 0 for row in rows.values())
    assert "hfrs" not in rows
    assert narrative("去年同期" + statement, province="CN-YN", month=5) == {}


def test_same_case_value_with_absent_narrative_deaths_keeps_table_deaths():
    table = {"Date": "2026-08-01", "SourceDiseaseCode": "anthrax", "JurisdictionCode": "CN-YN",
             "SourceID": "SRC_CN_PROV_MONTHLY_REPORT", "Cases": 0, "Deaths": 0,
             "Raw": {"source_row": {"病名": "炭疽", "发病数": 0, "死亡数": 0}}}
    narrative_row = {**table, "Raw": {"explicit_narrative_value": "炭疽无发病死亡报告"}}
    narrative_row.pop("Deaths")
    assert _deduplicate_source_rows([narrative_row, table]) == [table]
    with pytest.raises(ValueError, match="Conflicting province source rows"):
        _deduplicate_source_rows([table, {**narrative_row, "Cases": 1}])


def test_shanxi_september_preserves_independent_hfrs_evidence():
    # Official September article, archived as CN-SX_202609_extra.html in the audit.
    actual_hfrs_statement = (
        "传染性非典型肺炎、脊髓灰质炎、人感染新亚型流感、麻疹、出血热、狂犬病、登革热、"
        "基孔肯雅热、流脑、白喉、新生儿破伤风、钩体病、血吸虫病无发病、死亡报告"
    )
    rows = narrative(
        actual_hfrs_statement + "。中东呼吸综合征（MERS）、埃博拉出血热、寨卡病毒病、"
        "拉沙热、人感染猪链球菌病无发病、死亡报告。", month=9,
    )
    assert rows["hfrs"]["Cases"] == 0
    assert rows["hfrs"]["Raw"]["explicit_narrative_value"] == actual_hfrs_statement
    assert rows["hfrs"]["Raw"]["explicit_narrative_evidence"] == [actual_hfrs_statement]


def test_ebola_zero_does_not_conflict_with_positive_hfrs_or_replace_evidence():
    rows = narrative("报告病例数为出血热（3例）。埃博拉出血热无发病报告。")
    assert rows["hfrs"]["Cases"] == 3
    assert rows["ebola"]["Cases"] == 0
    assert "出血热（3例）" in rows["hfrs"]["Raw"]["explicit_narrative_value"]


def test_multiple_independent_zero_statements_retain_their_evidence():
    rows = narrative("出血热无发病报告。无出血热病例报告。")
    assert rows["hfrs"]["Raw"]["explicit_narrative_evidence"] == [
        "出血热无发病报告", "无出血热病例报告",
    ]


@pytest.mark.parametrize("prefix", ["今年累计", "去年同期", "上年同期", "本周", "第42周", "1至10月累计", "今日", "2025年10月", "2026年9月"])
def test_nonmonthly_narrative_claims_are_not_assigned_to_report_month(prefix):
    assert narrative(f"{prefix}报告病例数为流行性感冒（2393例）。") == {}
    assert narrative(f"{prefix}出血热无发病报告。") == {}
    assert narrative(f"{prefix}报告发病914例，均为水痘病例。") == {}


def test_explicit_current_month_resets_comparison_context():
    rows = narrative("去年同期报告病例数为流行性感冒（2393例），本月报告病例数为流行性感冒（100例）。")
    assert rows["influenza"]["Cases"] == 100


def test_explicit_report_month_is_accepted():
    assert narrative("2026年10月报告病例数为流行性感冒（100例）。")["influenza"]["Cases"] == 100


@pytest.mark.parametrize("text", [
    "死亡病例数为流行性感冒（5例）。",
    "报告病例数为甲肝（5例），死亡病例数为流行性感冒（2例）。",
    "除霍乱外其他病种无发病报告。",
    "重点监测其他传染病报告发病914例，均为水痘和肝吸虫病。",
])
def test_deaths_exceptions_and_multidisease_totals_are_not_case_observations(text):
    rows = narrative(text)
    assert not {"influenza", "cholera", "varicella", "clonorchiasis"}.intersection(rows)


def test_ningxia_sole_reported_nonstatutory_disease():
    # Official 2026-10 bulletin published 2026-11-10, archived in the audit:
    # CN-NX_02_3aeddd432bea.bin. Preserve only its explicit waterpox count.
    text = (
        "宁夏共报告法定传染病2447例,死亡3例(肺结核2例、艾滋病1例)。"
        "无甲类传染病发病、死亡病例报告。"
        "报告重点监测的其他非法定传染病294例,均为水痘,无死亡病例报告。"
    )
    rows = narrative(text, province="CN-NX")
    assert set(rows) == {"varicella"}
    assert rows["varicella"]["Cases"] == 294


def test_gansu_same_month_monitoring_transition_is_not_a_year_comparison():
    # https://www.gscdc.cn/html/news/yqtb/yqtb-show-11517.html
    statement = "同期，重点监测的其他非法定传染病共报告发病1654例，均为水痘病例，无死亡病例报告。"
    assert narrative(statement, province="CN-GS")["varicella"]["Cases"] == 1654
    assert narrative("去年" + statement, province="CN-GS") == {}


@pytest.mark.parametrize("unsafe", [
    "累计发病数", "上年同期发病数", "本周病例数", "死亡病例数", "发病率", "本月发病率", "发病数同比%",
])
def test_table_chooses_monthly_cases_over_other_metrics_regardless_of_order(unsafe):
    table = pd.DataFrame([["甲肝", 100, 3]], columns=["病名", unsafe, "本月发病数"])
    assert _normalize_report_table(table)[0]["cases"] == 3


def test_table_prefers_explicit_month_to_unspecified_case_count():
    table = pd.DataFrame([["甲肝", 100, 3]], columns=["病名", "病例数", "本月发病数"])
    assert _normalize_report_table(table)[0]["cases"] == 3


@pytest.mark.parametrize("columns", [
    ["病名", "病例数", "发病数"],
    ["病名", "本月病例数", "本月发病数"],
    ["病名", "发病数", "发病数"],
])
def test_table_ambiguous_count_columns_fail_closed(columns):
    with pytest.raises(ValueError, match="Ambiguous monthly case columns"):
        _normalize_report_table(pd.DataFrame([["甲肝", 100, 3]], columns=columns))


@pytest.mark.parametrize("column", ["累计发病数", "同期病例数", "本周病例数", "死亡病例数", "病例数同比%"])
def test_table_without_monthly_cases_does_not_relabel_other_metrics(column):
    assert _normalize_report_table(pd.DataFrame([["甲肝", 100]], columns=["病名", column])) == []


def test_table_preserves_explicit_deaths_without_filling_missing_values():
    table = pd.DataFrame(
        [["甲肝", 10, 99, 1], ["乙肝", 20, 99, None], ["丙肝", 30, 99, 0]],
        columns=["病名", "发病数", "累计死亡数", "本月死亡数"],
    )
    rows = _normalize_report_table(table, include_deaths=True)
    assert rows[0]["deaths"] == 1
    assert "deaths" not in rows[1]
    assert rows[2]["deaths"] == 0
    assert all("deaths" not in row for row in _normalize_report_table(table))


@pytest.mark.parametrize("unsafe", ["累计死亡数", "同期死亡数", "本周死亡数", "死亡率", "死亡数同比%"])
def test_table_does_not_relabel_nonmonthly_deaths(unsafe):
    rows = _normalize_report_table(pd.DataFrame([["甲肝", 10, 100]], columns=["病名", "发病数", unsafe]), include_deaths=True)
    assert "deaths" not in rows[0]


def test_table_ambiguous_death_columns_fail_closed():
    with pytest.raises(ValueError, match="Ambiguous monthly death columns"):
        _normalize_report_table(
            pd.DataFrame([["甲肝", 100, 2, 3]], columns=["病名", "发病数", "死亡数", "死亡病例数"]),
            include_deaths=True,
        )


def test_shanghai_explicit_residence_total_remains_supported():
    table = pd.DataFrame([["甲肝", 2, 1, 3]], columns=["病名", "本市户籍", "非本市户籍", "合计"])
    assert _normalize_report_table(table)[0]["cases"] == 3


def test_unqualified_total_is_not_assumed_to_measure_cases():
    assert _normalize_report_table(pd.DataFrame([["甲肝", 3]], columns=["病名", "合计"])) == []
