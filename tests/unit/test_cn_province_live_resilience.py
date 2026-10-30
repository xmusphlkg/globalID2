from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.data.crawlers.cn_provinces import MonthlyReportLink, _normalize_narrative_report
from src.data.processors.cn_provinces import CNProvinceUpdater
from src.services.crawl_service import CrawlService


@pytest.mark.parametrize('text,count', [
    ('重点监测其他传染病报告发病914例，均为水痘病例。',914),
    ('报告重点监测的4种其他非法定传染病195例,均为水痘。',195),
    ('重点监测其他传染病报告发病914例，均为水痘和肝吸虫病。',None),
    ('死亡病例报告914例，均为水痘病例。',None),
    ('报告发病914例，其中水痘病例。',None),
])
def test_explicit_sole_disease_only(text,count):
    rows=_normalize_narrative_report(text,link=MonthlyReportLink('CN-GS',date(2026,9,1),'report','https://example.test'),source_url='https://example.test',artifact_sha256='a'*64)
    assert len(rows)==(0 if count is None else 1)
    if count is not None: assert int(rows[0]['Cases'])==count


@pytest.mark.parametrize('code', ['CN-LN','CN-XZ','BR-SP','BR-AC'])
def test_subdivision_pipeline_keeps_own_identity(code):
    spec=CrawlService._PIPELINES[code]
    updater=CrawlService._make_updater(spec.updater_name,code)
    assert updater.country_code==code
    if code.startswith('BR-'): assert code in str(updater.output_csv)


async def test_province_store_rejects_foreign_rows_before_write():
    updater=CNProvinceUpdater(store=AsyncMock(),country_code='CN-LN')
    with pytest.raises(ValueError,match='different province'):
        await updater.import_rows(AsyncMock(),[{'SourceID':'SRC_CN_PROV_MONTHLY_REPORT','GeographyKey':'country:CN-BJ:national'}])
    updater.store.save_rows.assert_not_called()


def test_discovery_handles_base_data_links_and_excludes_national(monkeypatch):
    import requests
    from src.data.crawlers.cn_provinces import ProvinceMonthlyReportCrawler
    response=requests.Response()
    response.status_code=200
    response.url='https://example.test/nested/list.html'
    response._content='''<base href="https://example.test/">
        <a href="national">2026年9月全国传染病概况</a>
        <div data-link="reports/local"><div class="article__title">2026年9月陕西省传染病疫情</div><div>2025年8月旧记录</div></div>
    '''.encode()
    crawler=ProvinceMonthlyReportCrawler()
    monkeypatch.setattr(crawler,'_get',lambda *a,**k:response)
    rows=crawler.discover('CN-SN')
    assert len(rows)==1
    assert rows[0].url=='https://example.test/reports/local'
    assert rows[0].report_date==date(2026,9,1)
