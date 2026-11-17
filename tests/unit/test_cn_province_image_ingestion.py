"""Originals are replayed offline; only expert-voted visual results are admitted."""
from datetime import date
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
import requests

from src.data.crawlers.cn_provinces import MonthlyReportLink, ProvinceMonthlyReportCrawler
from src.services.ingestion.countries import cn_province as connector


ARTICLE = 'https://official.example/report'
PICTURE = 'https://official.example/ueditor/image/report.png'
LINK = MonthlyReportLink('CN-QH', date(2026, 10, 1), '青海省2026年10月法定传染病疫情', ARTICLE)


def originals():
    out = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(out, format='PNG')
    return out.getvalue()


def response(url, content, media):
    result = requests.Response()
    result.url, result.status_code, result._content = url, 200, content
    result.headers['Content-Type'] = media
    result.encoding = 'utf-8'
    return result


def artifacts():
    plan = {'country_code': 'CN-QH', 'source': 'cn_province_monthly_report',
            'reports': [{'date': '2026-10-01', 'title': LINK.title, 'url': ARTICLE}],
            'datacenter': [], 'errors': []}
    values = [(ARTICLE, f'<div class="TRS_Editor">{LINK.title}<img src="{PICTURE}"></div>'.encode(), 'text/html'),
              (PICTURE, originals(), 'image/png')]
    return [SimpleNamespace(kind='province_plan', content=json.dumps(plan).encode())] + [
        SimpleNamespace(kind='province_response', source_url=url, content=data,
                        metadata_={'response_url': url, 'media_type': media, 'encoding': 'utf-8', 'status_code': 200})
        for url, data, media in values]


def test_image_only_report_retains_original_but_no_observations(monkeypatch):
    source = {a.source_url: a for a in artifacts()[1:]}
    crawler = ProvinceMonthlyReportCrawler()
    monkeypatch.setattr(crawler, '_get', lambda url, **kwargs: response(url, source[url].content, source[url].metadata_['media_type']))
    fetched = crawler.fetch(LINK)
    assert fetched.rows == []
    assert fetched.image_documents[0]['data'] == originals()
    assert fetched.image_documents[0]['sha256'] == hashlib.sha256(originals()).hexdigest()
    assert crawler.report_diagnostics[0]['status'] == 'image_pending_review'


def test_collection_keeps_image_only_report_in_replay_plan(monkeypatch):
    class Crawler:
        report_diagnostics = [{'status': 'image_pending_review'}]
        def __init__(self, **kwargs): pass
        def discover(self, code): return [LINK]
        def fetch(self, link): return SimpleNamespace(rows=[], image_documents=[{'data': b'original'}])
    monkeypatch.setattr(connector, 'ProvinceMonthlyReportCrawler', Crawler)
    plan = connector._collect(None, country_code='CN-QH', source='cn_province_monthly_report',
                              force=False, start_year=None, recent_months=3, today=date(2026, 11, 16))
    assert not plan['errors']
    assert plan['reports'][0]['status'] == 'image_pending_review'


async def test_failed_image_report_remains_review_pending_when_another_month_has_rows(monkeypatch):
    from src.data.crawlers import cn_provinces

    older = MonthlyReportLink('CN-QH', date(2026, 9, 1), '青海省2026年9月法定传染病疫情', ARTICLE + '-september')
    missing_image = PICTURE + '?missing=1'

    class CollectionCrawler:
        report_diagnostics = []

        def __init__(self, **kwargs):
            pass

        def discover(self, code):
            return [older, LINK]

        def fetch(self, link):
            if link == LINK:
                self.report_diagnostics = [{'url': missing_image, 'status': 'image_capture_failed',
                                            'error': 'HTTP 404'}]
                raise ValueError('No explicit disease values found')
            self.report_diagnostics = []
            return SimpleNamespace(rows=[{'Measure': 'cases'}], image_documents=[])

    real_crawler = cn_provinces.ProvinceMonthlyReportCrawler
    monkeypatch.setattr(connector, 'ProvinceMonthlyReportCrawler', CollectionCrawler)
    plan = connector._collect(None, country_code='CN-QH', source='cn_province_monthly_report',
                              force=False, start_year=None, recent_months=3, today=date(2026, 11, 16))
    monkeypatch.setattr(connector, 'ProvinceMonthlyReportCrawler', real_crawler)
    assert [report['url'] for report in plan['reports']] == [older.url]
    assert any(error.get('status') == 'image_capture_failed' and error.get('url') == missing_image
               for error in plan['errors'])

    table = ('<table><tr><th>病名</th><th>发病数</th><th>死亡数</th></tr>'
             '<tr><td>甲型肝炎</td><td>3</td><td>0</td></tr></table>')
    captured = [SimpleNamespace(kind='province_plan', content=json.dumps(plan).encode()),
                SimpleNamespace(kind='province_response', source_url=older.url, content=table.encode(),
                                metadata_={'response_url': older.url, 'media_type': 'text/html',
                                           'encoding': 'utf-8', 'status_code': 200})]
    monkeypatch.setattr(connector, 'read_artifact', lambda artifact: artifact.content)
    fetched = await connector.organize_async(captured, updater=None,
        source='cn_province_monthly_report', country_code='CN-QH')
    assert fetched.rows[0]['Cases'] == 3
    assert fetched.source_audit['status'] == 'needs_review'
    assert any(error.get('url') == missing_image for error in fetched.source_audit['errors'])


@pytest.mark.parametrize('validated', [False, True])
async def test_offline_organize_only_admits_visually_validated_rows(monkeypatch, validated):
    from src.services import cn_province_image_review
    monkeypatch.setattr(connector, 'read_artifact', lambda a: a.content)
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **kw: pytest.fail('network forbidden'))
    review = AsyncMock(return_value={'status': 'validated' if validated else 'needs_review',
        'rows': [{'label': '急性出血性结膜炎', 'cases': 6, 'deaths': 0}],
        'audit': {'reason': 'independent_visual_readings' if validated else 'native_file_unavailable'}})
    monkeypatch.setattr(cn_province_image_review, 'review_image_report', review)
    fetched = await connector.organize_async(artifacts(), updater=None, source='cn_province_monthly_report', country_code='CN-QH')
    assert len(fetched.rows) == int(validated)
    assert fetched.source_audit['status'] == ('parsed' if validated else 'needs_review')
    assert not hasattr(fetched, 'image_pending')
    assert '"data":' not in json.dumps(fetched.source_audit)
    if validated:
        assert fetched.rows[0]['Cases'] == 6
        assert fetched.rows[0]['Deaths'] == 0
        assert fetched.rows[0]['QualityStatus'] == 'validated'
        assert fetched.rows[0]['Raw']['extraction_method'] == 'ai_expert_image_vote'
    assert review.await_args.kwargs['documents'][0]['data'] == originals()


async def test_missing_original_page_defers_entire_visual_review(monkeypatch):
    from src.services import cn_province_image_review
    captured = artifacts()
    captured.pop()  # captured article still references an image, but bytes are missing
    monkeypatch.setattr(connector, 'read_artifact', lambda a: a.content)
    review = AsyncMock()
    monkeypatch.setattr(cn_province_image_review, 'review_image_report', review)
    # With no original and no facts the existing empty-store gate still applies.
    with pytest.raises(RuntimeError, match='No usable observations'):
        await connector.organize_async(captured, updater=None, source='cn_province_monthly_report', country_code='CN-QH')
    review.assert_not_awaited()


async def test_structured_reports_do_not_require_an_ai_call(monkeypatch):
    from src.services import cn_province_image_review
    captured = artifacts()[:2]
    captured[1].content = ('<table><tr><th>病名</th><th>发病数</th><th>死亡数</th></tr>'
                           '<tr><td>甲型肝炎</td><td>3</td><td>0</td></tr></table>').encode()
    monkeypatch.setattr(connector, 'read_artifact', lambda a: a.content)
    review = AsyncMock()
    monkeypatch.setattr(cn_province_image_review, 'review_image_report', review)
    fetched = await connector.organize_async(captured, updater=None, source='cn_province_monthly_report', country_code='CN-QH')
    assert fetched.rows[0]['Cases'] == 3
    review.assert_not_awaited()


def test_mixed_pdf_does_not_admit_only_its_native_table_page(monkeypatch):
    import pypdfium2
    import pandas as pd
    from src.ai.attachments import build_probe_pdf
    from src.services.document_images import PDFIUM_LOCK, image_to_pdf
    from src.data.crawlers import cn_provinces
    with PDFIUM_LOCK:
        document = pypdfium2.PdfDocument(build_probe_pdf('NativePage'))
        scanned = pypdfium2.PdfDocument(image_to_pdf(originals()))
        try:
            document.import_pages(scanned)
            out = io.BytesIO()
            document.save(out)
        finally:
            scanned.close()
            document.close()
    pdf_url = 'https://official.example/mixed.pdf'
    source = {ARTICLE: response(ARTICLE, f'<a href="{pdf_url}">报表</a>'.encode(), 'text/html'),
              pdf_url: response(pdf_url, out.getvalue(), 'application/pdf')}
    crawler = ProvinceMonthlyReportCrawler()
    monkeypatch.setattr(crawler, '_get', lambda url, **kwargs: source[url])
    monkeypatch.setattr(cn_provinces, 'parse_attachment_tables', lambda *args, **kwargs: [
        pd.DataFrame([{'病名': '甲型肝炎', '发病数': 3, '死亡数': 0}])])
    fetched = crawler.fetch(LINK)
    assert fetched.rows == []
    assert len(fetched.image_documents) == 1
    assert fetched.image_documents[0]['data'] == out.getvalue()


@pytest.mark.parametrize('markup', [
    '<img data-original="/ueditor/image/report.png" src="/thumbnail.png">',
    '<a href="/ueditor/image/report.png"><img src="/thumbnail.png"></a>',
    '<a href="/ueditor/image/report.png">下载扫描表</a>',
])
def test_lazy_images_and_linked_originals_share_capture_pipeline(monkeypatch, markup):
    crawler = ProvinceMonthlyReportCrawler()
    requested = []
    def get(url, **kwargs):
        requested.append(url)
        if url == ARTICLE:
            return response(url, f'<div class="TRS_Editor">{markup}</div>'.encode(), 'text/html')
        assert url == PICTURE
        return response(url, originals(), 'image/png')
    monkeypatch.setattr(crawler, '_get', get)
    fetched = crawler.fetch(LINK)
    assert len(fetched.image_documents) == 1
    assert requested == [ARTICLE, PICTURE]
