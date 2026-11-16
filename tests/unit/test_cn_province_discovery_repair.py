"""Regression coverage for discovery, attachment evidence, and source contracts."""
import hashlib
import io
import zipfile
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
import requests

from src.data.crawlers import cn_provinces as module
from src.data.crawlers.cn_provinces import (
    MonthlyReportLink,
    ProvinceDataCenterCrawler,
    ProvinceMonthlyReportCrawler,
    _resolve_report_disease,
    parse_attachment_tables,
    parse_datacenter_spreadsheet,
    province_configs,
)


class Responses:
    def __init__(self, bodies):
        self.bodies = bodies
        self.headers = {}
        self.visited = []

    def get(self, url, **kwargs):
        self.visited.append(url)
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.encoding = "utf-8"
        body = self.bodies[url]
        response._content = body.encode() if isinstance(body, str) else body
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response


def test_discovery_keeps_column_boundary_and_budget(monkeypatch):
    base = "https://example.test/reports/"
    cfg = replace(province_configs()["CN-BJ"], index_url=base + "index.html",
                  archive_index_urls=(), pagination_url_template="", max_index_pages=2)
    monkeypatch.setattr(module, "province_configs", lambda *_: {"CN-BJ": cfg})
    session = Responses({
        base + "index.html": '<a href="new.html">2026年9月北京市传染病疫情</a>'
          '<a href="index_1.html">下一页</a><a href="/other/index_1.html">下一页</a>'
          '<a href="https://evil.test/reports/index_1.html">下一页</a>'
          '<a href="unrelated.html">下一页</a>'
          '<a href="future.html">2099年1月北京市传染病疫情</a>',
        base + "index_1.html": '<a href="old.html">2025年9月北京市传染病疫情</a>'
          '<a href="index_2.html">下一页</a>',
    })
    crawler = ProvinceMonthlyReportCrawler(session=session)
    links = crawler.discover("CN-BJ")
    assert [link.report_date for link in links] == [date(2025, 9, 1), date(2026, 9, 1)]
    assert session.visited == [base + "index.html", base + "index_1.html"]
    assert {d["status"] for d in crawler.discovery_diagnostics} >= {"page_budget_reached", "future_month_rejected"}


def test_beijing_uses_current_index_and_retains_history():
    cfg = province_configs()["CN-BJ"]
    assert cfg.index_url.endswith("/yqxx/index.html")
    assert any(url.endswith("index_1.html") for url in cfg.archive_index_urls)


def test_jiangsu_discovers_official_embedded_xml_article_links(monkeypatch):
    cfg = replace(province_configs()["CN-JS"], index_url="https://example.test/col/col49513/index.html?number=")
    monkeypatch.setattr(module, "province_configs", lambda *_: {"CN-JS": cfg})
    index = '''<script type="text/xml"><datastore><recordset>
    <record><![CDATA[<li><a title="2024年1月江苏省法定报告传染病疫情" href="/art/2024/2/report.html">报告</a></li>]]></record>
    </recordset></datastore></script>'''
    crawler = ProvinceMonthlyReportCrawler(session=Responses({cfg.index_url: index}))
    links = crawler.discover("CN-JS")
    assert [(link.report_date, link.url) for link in links] == [
        (date(2024, 1, 1), "https://example.test/art/2024/2/report.html")
    ]
    with pytest.raises(ValueError, match="XML records"):
        ProvinceMonthlyReportCrawler(session=Responses({cfg.index_url: "<p>missing</p>"})).discover("CN-JS")


def test_hebei_and_heilongjiang_include_verified_official_history_columns():
    hebei = province_configs()["CN-HE"]
    assert hebei.index_url == "https://www.hebeicdc.cn/sn/index.jhtml"
    assert [url.rsplit("/", 1)[-1] for url in hebei.archive_index_urls] == [
        "index_2.jhtml", "index_3.jhtml", "index_4.jhtml",
    ]
    heilongjiang = province_configs()["CN-HL"]
    assert heilongjiang.archive_index_urls == (
        "https://jkj.hlj.gov.cn/hljjkj/yiqing003/newsList.shtml",
    )


def test_wps_routes_only_verified_word_signatures(monkeypatch):
    called = []
    monkeypatch.setattr(module, "_legacy_doc_tables", lambda content: called.append(content) or [])
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"example"
    assert parse_attachment_tables(ole, "https://example.test/report.wps") == []
    assert called == [ole]
    with pytest.raises(ValueError, match="signature"):
        parse_attachment_tables(b"<html>challenge</html>", "https://example.test/report.wps")
    with pytest.raises(ValueError, match="signature"):
        parse_attachment_tables(b"PK\x03\x04not-a-document", "https://example.test/report.wps")


def test_wps_docx_signature_can_parse_actual_table():
    stream = io.BytesIO()
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
    for cells in (("病名", "发病数"), ("甲肝", "3")):
        xml += '<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>' + v + '</w:t></w:r></w:p></w:tc>' for v in cells) + '</w:tr>'
    xml += '</w:tbl></w:body></w:document>'
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    tables = parse_attachment_tables(stream.getvalue(), "https://example.test/report.wps")
    assert tables[0].iloc[0].tolist() == ["甲肝", "3"]


def test_page_narrative_keeps_page_evidence_after_bad_attachment():
    page = '<html><p>报告发病1654例，均为水痘病例。</p><a href="report.wps">附件</a></html>'
    url = "https://example.test/page.html"
    session = Responses({url: page, "https://example.test/report.wps": b"bad attachment"})
    crawler = ProvinceMonthlyReportCrawler(session=session)
    parsed = crawler.fetch(MonthlyReportLink("CN-GS", date(2026, 10, 1), "2026年10月甘肃省传染病疫情", url))
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row["Cases"] == 1654
    assert row["SourceURL"] == url
    assert row["Raw"]["artifact_sha256"] == hashlib.sha256(page.encode()).hexdigest()
    assert any(d["status"] == "attachment_parse_failed" for d in crawler.report_diagnostics)


def test_summary_only_report_has_explicit_no_detail_diagnostic():
    url = "https://example.test/summary.html"
    session = Responses({url: "<html><p>法定传染病报告发病总数123例，位居前列的有流感和肺结核。</p></html>"})
    crawler = ProvinceMonthlyReportCrawler(session=session)
    with pytest.raises(ValueError, match="No explicit disease values"):
        crawler.fetch(MonthlyReportLink("CN-HE", date(2026, 8, 1), "2026年8月河北省传染病疫情", url))
    assert crawler.report_diagnostics[-1] == {
        "url": url, "report_month": "2026-08", "status": "summary_only",
        "html_table_count": 0, "attachment_count": 0, "image_link_count": 0,
    }


def test_no_value_with_attachment_parse_failure_is_not_called_summary_only():
    url = "https://example.test/attachment.html"
    attachment = "https://example.test/report.wps"
    session = Responses({url: '<html><p>传染病报告发病总数123例</p><a href="report.wps">附件</a></html>',
                         attachment: b"unsupported"})
    crawler = ProvinceMonthlyReportCrawler(session=session)
    with pytest.raises(ValueError, match="No explicit disease values"):
        crawler.fetch(MonthlyReportLink("CN-HE", date(2026, 8, 1), "2026年8月河北省传染病疫情", url))
    assert crawler.report_diagnostics[-1]["status"] == "no_disease_values"
    assert crawler.report_diagnostics[-1]["attachment_count"] == 1


@pytest.mark.parametrize("label", ["肺结核死亡", "肺结核疑似", "流行性感冒累计", "手足口病疑似病例", "肺炭疽", "流行性出血热出血热死亡", "伤寒和副伤寒和副寒疑似"])
def test_disease_suffix_cannot_change_case_definition(label):
    assert _resolve_report_disease(label) is None


def test_explicit_footnote_remains_supported():
    assert _resolve_report_disease("肺结核①")["code"] == "tuberculosis"
    assert _resolve_report_disease("肺结核（1）")["code"] == "tuberculosis"
    assert _resolve_report_disease("伤寒、副伤寒")["code"] == "typhoid_paratyphoid"
    assert _resolve_report_disease("猴痘#")["code"] == "mpox"
    assert _resolve_report_disease("肺结核#") is None


def test_hunan_verified_word_revision_labels_preserve_values_and_raw_names():
    labels = ["流行性出血热出血热", "伤寒和副伤寒和副寒"]
    table = pd.DataFrame({"病名": labels, "发病数": [22, 38], "死亡数11": [0, 0]})
    rows = ProvinceMonthlyReportCrawler()._normalize_tables(
        [table], link=MonthlyReportLink("CN-HN", date(2026, 7, 1), "report", "https://example.test"),
        source_url="https://example.test/report.doc", artifact_sha256="c2d0353da61690e80479c597a60324aac6c7c5b5ed77d977c6b473574e489407",
    )
    assert [r["SourceDiseaseCode"] for r in rows] == [
        _resolve_report_disease("流行性出血热")["code"],
        _resolve_report_disease("伤寒和副伤寒")["code"],
    ]
    assert [(r["Cases"], r["Deaths"]) for r in rows] == [(22, 0), (38, 0)]
    assert [r["RawDiseaseLabel"] for r in rows] == labels
    assert [r["Raw"]["label"] for r in rows] == labels


def test_table_preserves_deaths_and_rejects_pre_definition_values():
    table = pd.DataFrame({"病名": ["甲肝", "发热伴血小板减少综合征"], "本月发病数": [3, 9], "本月死亡数": [0, 1]})
    crawler = ProvinceMonthlyReportCrawler()
    rows = crawler._normalize_tables([table], link=MonthlyReportLink("CN-BJ", date(2026, 3, 1), "report", "https://example.test"), source_url="https://example.test", artifact_sha256="a" * 64)
    assert [(r["SourceDiseaseCode"], r["Cases"], r.get("Deaths")) for r in rows] == [("hepatitis_a", 3, 0)]
    assert any(d["status"] == "before_definition_start" for d in crawler.report_diagnostics)


def spreadsheet(label="甲肝"):
    rows = [[""], ["", "", label], ["", "地区", "发病数"], ["", "全国", "8"], ["", "北京市", "3"]]
    xml = '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table>'
    xml += ''.join('<Row>' + ''.join('<Cell><Data>' + cell + '</Data></Cell>' for cell in row) + '</Row>' for row in rows)
    return (xml + '</Table></Worksheet></Workbook>').encode()


def test_small_datacenter_response_is_parsed_instead_of_discarded():
    crawler = ProvinceDataCenterCrawler(request_interval=0)
    url = crawler.config["download_url"].format(year=2020, disease_id=240, month=1)
    crawler.session = Responses({url: spreadsheet()})
    rows = crawler._fetch_report(2020, 240, 1)
    assert len(spreadsheet()) < 6144
    assert rows[0]["Cases"] == 3
    assert crawler.response_diagnostics[-1]["status"] == "parsed"


def test_unknown_datacenter_disease_is_visible_failure():
    with pytest.raises(ValueError, match="unmapped disease.*未知新病种"):
        parse_datacenter_spreadsheet(spreadsheet("未知新病种"), report_date=date(2020, 1, 1), source_url="https://example.test")


def test_invalid_datacenter_response_has_diagnostic():
    crawler = ProvinceDataCenterCrawler(request_interval=0)
    url = crawler.config["download_url"].format(year=2020, disease_id=240, month=1)
    crawler.session = Responses({url: b"<html>Access denied</html>"})
    with pytest.raises(ValueError, match="invalid response"):
        crawler._fetch_report(2020, 240, 1)
    assert crawler.response_diagnostics[-1]["status"] == "invalid_response"


def test_query_pagination_cannot_switch_publisher_column():
    original = "https://example.test/f/newsList?id=epidemics"
    assert ProvinceMonthlyReportCrawler._is_column_page(original + "&pageNo=2", original)
    assert not ProvinceMonthlyReportCrawler._is_column_page("https://example.test/f/newsList?id=news&pageNo=2", original)


def test_extensionless_pagination_stays_inside_original_column():
    original = "https://example.test/xwzx/yqxx"
    assert ProvinceMonthlyReportCrawler._is_column_page(original + "_2", original)
    assert ProvinceMonthlyReportCrawler._is_column_page(original + "_3", original + "_2")
    assert not ProvinceMonthlyReportCrawler._is_column_page("https://example.test/xwzx/news_2", original)


def test_json_discovery_pages_fixed_category_and_deduplicates(monkeypatch):
    import json
    cfg = province_configs()["CN-JX"]
    cfg = replace(cfg, max_index_pages=2)
    monkeypatch.setattr(module, "province_configs", lambda *_: {"CN-JX": cfg})
    class JsonSession(Responses):
        def __init__(self):
            super().__init__({})
            self.posts = []
        def post(self, url, data, **kwargs):
            self.posts.append(data)
            self.bodies[url] = json.dumps({"count": 3, "list": (
                [{"id": "a", "title": "2026年7月江西省法定传染病疫情"}, {"id": "b", "title": "2026年6月江西省法定传染病疫情"}]
                if data["pageNo"] == "1" else [{"id": "b", "title": "2026年6月江西省法定传染病疫情"}]
            )})
            return self.get(url)
    session = JsonSession()
    links = ProvinceMonthlyReportCrawler(session=session).discover("CN-JX")
    assert [link.report_date for link in links] == [date(2026, 6, 1), date(2026, 7, 1)]
    assert [p["pageNo"] for p in session.posts] == ["1", "2"]
    assert all(p["categoryId"] == "680a2925e9824b12b967b10530f8adc3" for p in session.posts)


@pytest.mark.parametrize("month,expected", [(8, {"hepatitis_a"}), (9, {"hepatitis_a", "novel_influenza"})])
def test_novel_influenza_table_obeys_september_2025_definition(month, expected):
    table = pd.DataFrame({"病名": ["甲肝", "人感染新亚型流感"], "发病数": [3, 0]})
    rows = ProvinceMonthlyReportCrawler()._normalize_tables(
        [table], link=MonthlyReportLink("CN-YN", date(2025, month, 1), "report", "https://example.test"),
        source_url="https://example.test", artifact_sha256="a" * 64,
    )
    assert {r["SourceDiseaseCode"] for r in rows} == expected


def test_json_html_listing_extracts_official_report_links(monkeypatch):
    import json
    cfg = replace(province_configs()["CN-SD"], max_index_pages=1)
    monkeypatch.setattr(module, "province_configs", lambda *_: {"CN-SD": cfg})
    body = json.dumps({"data": {"html": '<a href="/zwgk/yqxx/report.html">山东省2026年9月法定报告传染病疫情通报</a>'}})
    links = ProvinceMonthlyReportCrawler(session=Responses({cfg.index_url: body})).discover("CN-SD")
    assert len(links) == 1
    assert links[0].url == "http://jkj.shandong.gov.cn/zwgk/yqxx/report.html"


def test_nested_json_pagination_cannot_change_epidemic_column():
    import json
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

    from src.data.crawlers.cn_province_adapters.shandong import _listing_url
    first, second = _listing_url(1), _listing_url(2)
    assert ProvinceMonthlyReportCrawler._is_column_page(second, first)
    parts = urlsplit(second)
    query = parse_qs(parts.query)
    params = json.loads(query["paramJson"][0])
    params["search"] = json.dumps({"xxgkId": "another-column"})
    query["paramJson"] = [json.dumps(params)]
    other_column = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
    assert not ProvinceMonthlyReportCrawler._is_column_page(other_column, first)
