"""Mixed report formats must retain every original and quarantine conflicts."""
from datetime import date, datetime, timezone
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
from PIL import Image
import pytest
import requests

from src.ai.attachments import build_probe_pdf
from src.data.crawlers import cn_provinces as crawler_module
from src.data.crawlers.cn_provinces import MonthlyReportLink, ProvinceMonthlyReportCrawler
from src.domain import TaskStatus
from src.services import cn_province_image_review
from src.services.automation_service import AutomationJobConfig, _ingestion_job_health
from src.services.ingestion.countries import cn_province as connector

ARTICLE = "https://official.example/report"
IMAGE = "https://official.example/ueditor/image/report.png"
XLSX = "https://official.example/report.xlsx"
PDF = "https://official.example/report.pdf"
LINK = MonthlyReportLink("CN-QH", date(2026, 10, 1), "青海省2026年10月疫情", ARTICLE)
TABLE = "<table><tr><th>病名</th><th>发病数</th><th>死亡数</th></tr><tr><td>甲型肝炎</td><td>3</td><td>0</td></tr></table>"


def png():
    output = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(output, format="PNG")
    return output.getvalue()


def response(url, data, media):
    result = requests.Response()
    result.url, result.status_code, result._content, result.encoding = url, 200, data, "utf-8"
    result.headers["Content-Type"] = media
    return result


def setup_crawler(monkeypatch, content):
    crawler = ProvinceMonthlyReportCrawler()
    source = {
        ARTICLE: response(ARTICLE, ("<div class='TRS_Editor'>" + content + "</div>").encode(), "text/html"),
        IMAGE: response(IMAGE, png(), "image/png"),
        XLSX: response(XLSX, b"excel", "application/octet-stream"),
        PDF: response(PDF, build_probe_pdf("ScannedDocumentPlaceholder"), "application/pdf"),
    }
    getter = Mock(side_effect=lambda url, **kwargs: source[url])
    monkeypatch.setattr(crawler, "_get", getter)
    monkeypatch.setattr(crawler_module, "parse_attachment_tables", lambda data, url, **kwargs:
        [pd.DataFrame([{"病名": "甲型肝炎", "发病数": 3, "死亡数": 0}])] if url == XLSX else [])
    return crawler, getter


def test_html_table_does_not_hide_remaining_image_original(monkeypatch):
    crawler, getter = setup_crawler(monkeypatch, TABLE + f'<img src="{IMAGE}">')
    fetched = crawler.fetch(LINK)
    assert fetched.rows[0]["Cases"] == 3
    assert len(fetched.image_documents) == 1
    assert fetched.image_documents[0]["source_url"] == IMAGE
    assert IMAGE in [call.args[0] for call in getter.call_args_list]


def test_xlsx_does_not_discard_already_captured_image(monkeypatch):
    crawler, _ = setup_crawler(monkeypatch, f'<img src="{IMAGE}"><a href="{XLSX}">附件</a>')
    fetched = crawler.fetch(LINK)
    assert fetched.rows[0]["Cases"] == 3
    assert fetched.image_documents[0]["data"] == png()


def test_later_xlsx_does_not_discard_pending_pdf_pages(monkeypatch):
    crawler, _ = setup_crawler(monkeypatch, f'<a href="{PDF}">扫描表</a><a href="{XLSX}">补充表</a>')
    fetched = crawler.fetch(LINK)
    assert len(fetched.rows) == len(fetched.image_documents) == 1
    assert fetched.image_documents[0]["source_url"] == PDF


def row(month=10, cases=3):
    return {"Date": f"2026-{month:02d}-01", "Cases": cases,
            "SourceID": "SRC_CN_PROV_MONTHLY_REPORT", "JurisdictionCode": "CN-QH",
            "GeographyKey": "country:CN-QH:national", "SourceDiseaseCode": "hepatitis_a",
            "DefinitionVersion": "CN_PROVINCE_MONTHLY_REPORT_V1", "Raw": {}}


def bundle(report=None, diagnostics=None):
    return {"report": report or {"url": ARTICLE}, "diagnostics": diagnostics or [],
            "documents": [], "link": LINK, "article_text": ""}


@pytest.mark.parametrize("two_visual_reports", [False, True])
async def test_same_month_conflict_cannot_complete_as_healthy(monkeypatch, two_visual_reports):
    old_month = row(9, 7)
    original = [] if two_visual_reports else [row()]
    pending = [bundle(), bundle({"url": ARTICLE + "-revised"})] if two_visual_reports else [bundle()]
    fetched = SimpleNamespace(rows=[old_month, *original], image_pending=pending,
        report_batches=[({"url": "old-month"}, [old_month]), ({"url": ARTICLE}, original)], source_audit={"errors": []})
    monkeypatch.setattr(connector, "organize", lambda *args, **kwargs: fetched)
    results = [{"status": "validated", "audit": {}, "rows": [row(cases=4)]}]
    if two_visual_reports:
        results.insert(0, {"status": "validated", "audit": {}, "rows": [row(cases=3)]})
    monkeypatch.setattr(cn_province_image_review, "review_image_report", AsyncMock(side_effect=results))
    monkeypatch.setattr(connector, "_verified_image_rows", lambda review, *args: review["rows"])
    organized = await connector.organize_async([], updater=None, source="cn_province_monthly_report", country_code="CN-QH")
    assert organized.rows == [old_month]
    assert organized.source_audit["status"] == "needs_review"
    assert organized.source_audit["errors"][0]["stage"] == "reconcile"
    task = SimpleNamespace(status=TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc),
                           metadata_={"province_ingestion": {"status": organized.source_audit["status"]}})
    health = _ingestion_job_health(AutomationJobConfig(job_id="cn-qh", name="QH", country_code="CN-QH"), task, task)
    assert health["health_status"] == "needs_review"


@pytest.mark.parametrize("diagnostic", ["image_capture_failed", "image_document_limit"])
async def test_incomplete_originals_with_some_existing_facts_do_not_call_ai(monkeypatch, diagnostic):
    explicit = row()
    fetched = SimpleNamespace(rows=[explicit], report_batches=[({}, [explicit])],
        image_pending=[bundle(diagnostics=[{"status": diagnostic}])], source_audit={"errors": []})
    monkeypatch.setattr(connector, "organize", lambda *args, **kwargs: fetched)
    ai = AsyncMock()
    monkeypatch.setattr(cn_province_image_review, "review_image_report", ai)
    organized = await connector.organize_async([], updater=None, source="cn_province_monthly_report", country_code="CN-QH")
    assert organized.rows == [explicit]
    assert organized.source_audit["status"] == "needs_review"
    ai.assert_not_awaited()


async def test_all_image_failures_are_visible_even_without_pending_document_bundle(monkeypatch):
    explicit = row()
    fetched = SimpleNamespace(rows=[explicit], report_batches=[({}, [explicit])], image_pending=[],
        source_audit={"errors": [{"status": "image_capture_failed", "url": IMAGE, "error": "HTTP 404"}]})
    monkeypatch.setattr(connector, "organize", lambda *args, **kwargs: fetched)
    ai = AsyncMock()
    monkeypatch.setattr(cn_province_image_review, "review_image_report", ai)
    organized = await connector.organize_async([], updater=None, source="cn_province_monthly_report", country_code="CN-QH")
    assert organized.source_audit["status"] == "needs_review"
    ai.assert_not_awaited()


async def test_async_audit_freshness_uses_latest_explicit_monthly_case(monkeypatch):
    case = row(7)
    death = row(10)
    death.update(Measure="deaths", SourceDiseaseCode="hepatitis_a__deaths")
    center = row(11)
    center["SourceID"] = "SRC_CN_PROV_DATACENTER"
    fetched = SimpleNamespace(rows=[case, death, center], image_pending=[],
        report_batches=[({}, [case, death])], source_audit={"errors": []})
    monkeypatch.setattr(connector, "organize", lambda *args, **kwargs: fetched)
    organized = await connector.organize_async([], updater=None,
        source="cn_province_monthly_report", country_code="CN-QH")
    assert organized.source_audit["latest_report_month"] == "2026-07"
