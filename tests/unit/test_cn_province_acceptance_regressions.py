"""Offline regression checks for the rc.2 acceptance counterexamples."""
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import requests

from scripts.update_cn_provinces import preflight_rows
from src.data.crawlers.cn_provinces import (
    MONTHLY_REPORT_SOURCE_ID, MonthlyReportLink, ProvinceMonthlyReportCrawler,
    _deduplicate_source_rows, _normalize_narrative_report,
)
from src.data.processors.cn_provinces import expand_province_metric_rows
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.exceptions import TaskCancelledError
from src.services.ingestion.countries import cn_province as connector

ARTICLE = "https://official.example/report"
ATTACHMENT = "https://official.example/report.xlsx"
LINK = MonthlyReportLink("CN-BJ", date(2026, 10, 1), "北京市2026年10月传染病疫情", ARTICLE)
TABLE = "<table><tr><th>病名</th><th>发病数</th><th>死亡数</th></tr><tr><td>甲型肝炎</td><td>3</td><td>0</td></tr></table>"


def narrative(text, link=LINK):
    return _normalize_narrative_report(text, link=link, source_url=link.url, artifact_sha256="regression")


@pytest.mark.parametrize("period", [
    "2026年10月15日", "10月15号", "2026年10月1日至10月7日", "10月1—7日",
    "2026-10-15", "2026/10/15", "10月十五日", "10月2日至10月31日",
    "10月1日至11月1日", "2025年10月1日至10月31日",
])
def test_daily_and_partial_periods_are_not_monthly_counts(period):
    assert narrative(period + "报告病例数为流行性感冒（100例）。") == []
    assert narrative(period + "出血热无发病报告。") == []
    assert narrative(period + "报告发病100例，均为水痘病例。") == []


@pytest.mark.parametrize("period", ["2026年10月", "本月", "当月", "2026年10月1日至10月31日", "10月1日—31日"])
def test_whole_month_counts_keep_literal_source_evidence(period):
    text = period + "报告病例数为流行性感冒（100例）。"
    rows = narrative(text)
    assert len(rows) == 1 and rows[0]["Cases"] == 100
    assert period in rows[0]["Raw"]["explicit_narrative_value"]


def test_explicit_monthly_reset_can_follow_a_daily_comparison():
    rows = narrative("10月15日报告病例数为流行性感冒（10例），本月报告病例数为流行性感冒（100例）。")
    assert len(rows) == 1 and rows[0]["Cases"] == 100


@pytest.mark.parametrize("year,last_day", [(2024, 29), (2025, 28)])
def test_full_february_range_uses_actual_month_length(year, last_day):
    link = MonthlyReportLink("CN-BJ", date(year, 2, 1), f"{year}年2月", ARTICLE)
    assert narrative(f"{year}年2月1日至2月{last_day}日报告病例数为流行性感冒（100例）。", link)[0]["Cases"] == 100
    assert narrative(f"{year}年2月1日至2月{last_day - 1}日报告病例数为流行性感冒（100例）。", link) == []


def normalized(table):
    return ProvinceMonthlyReportCrawler()._normalize_tables([table], link=LINK, source_url=ARTICLE, artifact_sha256="original")


@pytest.mark.parametrize("missing", ["-", "", None])
@pytest.mark.parametrize("deaths", [0, 2])
def test_missing_cases_keep_explicit_deaths_as_independent_observations(missing, deaths):
    rows = normalized(pd.DataFrame([["甲型肝炎", missing, deaths]], columns=["病名", "发病数", "死亡数"]))
    observations = SeriesObservationStore().build_observations(expand_province_metric_rows(rows), "CN", source_id=MONTHLY_REPORT_SOURCE_ID)
    assert observations.skipped_invalid == observations.skipped_ambiguous == observations.skipped_unmatched == 0
    facts = {row["series_code"]: row for row in observations.observations}
    assert facts["SER_CN_PROV_REPORT_HEPATITIS_A"]["value"] is None
    assert facts["SER_CN_PROV_REPORT_HEPATITIS_A"]["metadata"]["missing_value_status"] == "source_missing"
    assert facts["SER_CN_PROV_REPORT_DEATHS_HEPATITIS_A"]["value"] == deaths
    assert facts["SER_CN_PROV_REPORT_DEATHS_HEPATITIS_A"]["metadata"]["missing_value_status"] is None
    assert preflight_rows(rows)["unique_observations"] == 2
    assert connector.collection_status(rows, []) == "death_only"


def test_death_only_table_does_not_create_an_absent_case_metric():
    rows = normalized(pd.DataFrame([["甲型肝炎", 2]], columns=["病名", "死亡数"]))
    assert len(rows) == 1 and rows[0]["SourceDiseaseCode"] == "hepatitis_a__deaths"
    assert rows[0]["Cases"] == 2 and rows[0]["Measure"] == "deaths"
    assert preflight_rows(rows)["unique_observations"] == 1


def test_known_case_evidence_supersedes_unknown_without_losing_death_evidence():
    unknown = normalized(pd.DataFrame([["甲型肝炎", "-", 2]], columns=["病名", "发病数", "死亡数"]))
    known = normalized(pd.DataFrame([["甲型肝炎", 3]], columns=["病名", "发病数"]))
    for rows in ([*unknown, *known], [*known, *unknown]):
        deduped = _deduplicate_source_rows(rows)
        merged, errors = connector.merge_report_rows([({}, rows)])
        assert not errors and merged == deduped
        assert {row["SourceDiseaseCode"]: row["Cases"] for row in merged} == {"hepatitis_a": 3, "hepatitis_a__deaths": 2}


def test_expanding_combined_metrics_does_not_copy_case_missingness_into_deaths():
    row = normalized(pd.DataFrame([["甲型肝炎", "-", 2]], columns=["病名", "发病数", "死亡数"]))[0]
    expanded = expand_province_metric_rows([{**row, "Deaths": 2}])
    assert expanded[0]["MissingValueStatus"] == "source_missing"
    assert expanded[1]["Cases"] == 2 and "MissingValueStatus" not in expanded[1]


def response(url, body, status=200):
    result = requests.Response()
    result.url, result.status_code, result._content, result.encoding = url, status, body.encode(), "utf-8"
    result.headers["Content-Type"] = "text/html"
    return result


def test_broken_supplemental_attachment_retains_html_and_exposes_partial_status(monkeypatch):
    page = TABLE + f'<a href="{ATTACHMENT}">附件</a>'
    session = SimpleNamespace(headers={}, get=lambda url, **kwargs: response(url, page if url == ARTICLE else "missing", 200 if url == ARTICLE else 404))
    crawler = ProvinceMonthlyReportCrawler(session=session)
    fetched = crawler.fetch(LINK)
    assert fetched.rows[0]["Cases"] == 3
    errors = connector.diagnostic_errors(crawler.report_diagnostics, "fetch")
    assert errors[0]["status"] == "attachment_fetch_failed" and errors[0]["url"] == ATTACHMENT
    assert connector.collection_status(fetched.rows, errors) == "partial"


def test_attachment_cancellation_propagates_instead_of_becoming_partial(monkeypatch):
    crawler = ProvinceMonthlyReportCrawler()
    def get(url, **kwargs):
        if url == ARTICLE:
            return response(url, TABLE + f'<a href="{ATTACHMENT}">附件</a>')
        raise TaskCancelledError("stop")
    monkeypatch.setattr(crawler, "_get", get)
    with pytest.raises(TaskCancelledError):
        crawler.fetch(LINK)


async def test_offline_replay_keeps_html_when_failed_attachment_has_no_success_artifact(monkeypatch):
    plan = {"country_code": "CN-BJ", "source": "cn_province_monthly_report", "reports": [{"date": "2026-10-01", "title": LINK.title, "url": ARTICLE}], "datacenter": [], "errors": []}
    page = TABLE + f'<a href="{ATTACHMENT}">附件</a>'
    artifacts = [SimpleNamespace(kind="province_plan", content=json.dumps(plan).encode()), SimpleNamespace(kind="province_response", source_url=ARTICLE, content=page.encode(), metadata_={"response_url": ARTICLE, "media_type": "text/html", "encoding": "utf-8", "status_code": 200})]
    monkeypatch.setattr(connector, "read_artifact", lambda artifact: artifact.content)
    monkeypatch.setattr(requests.Session, "send", lambda *args, **kwargs: pytest.fail("network forbidden"))
    fetched = await connector.organize_async(artifacts, updater=None, source="cn_province_monthly_report", country_code="CN-BJ")
    assert fetched.rows[0]["Cases"] == 3
    assert fetched.source_audit["status"] == "partial"


async def test_unknown_case_cells_do_not_advance_case_freshness(monkeypatch):
    plan = {"country_code": "CN-BJ", "source": "cn_province_monthly_report", "reports": [{"date": "2026-10-01", "title": LINK.title, "url": ARTICLE}], "datacenter": [], "errors": []}
    page = TABLE.replace("<td>3</td>", "<td>-</td>")
    artifacts = [SimpleNamespace(kind="province_plan", content=json.dumps(plan).encode()), SimpleNamespace(kind="province_response", source_url=ARTICLE, content=page.encode(), metadata_={"response_url": ARTICLE, "media_type": "text/html", "encoding": "utf-8", "status_code": 200})]
    monkeypatch.setattr(connector, "read_artifact", lambda artifact: artifact.content)
    fetched = await connector.organize_async(artifacts, updater=None, source="cn_province_monthly_report", country_code="CN-BJ")
    assert fetched.source_audit["latest_report_month"] is None
    assert fetched.source_audit["status"] == "death_only"
    assert {row["SourceDiseaseCode"]: row["Cases"] for row in fetched.rows} == {"hepatitis_a": None, "hepatitis_a__deaths": 0}


async def test_incomplete_attachment_original_blocks_visual_review(monkeypatch):
    from src.services import cn_province_image_review
    rows = normalized(pd.DataFrame([["甲型肝炎", 3]], columns=["病名", "发病数"]))
    fetched = SimpleNamespace(rows=rows, report_batches=[({}, rows)], image_pending=[{"report": {}, "diagnostics": [{"status": "attachment_fetch_failed"}], "documents": []}], source_audit={"errors": []})
    monkeypatch.setattr(connector, "organize", lambda *args, **kwargs: fetched)
    review = AsyncMock()
    monkeypatch.setattr(cn_province_image_review, "review_image_report", review)
    organized = await connector.organize_async([], updater=None, source="cn_province_monthly_report", country_code="CN-BJ")
    assert organized.rows == rows and organized.source_audit["status"] == "needs_review"
    review.assert_not_awaited()
