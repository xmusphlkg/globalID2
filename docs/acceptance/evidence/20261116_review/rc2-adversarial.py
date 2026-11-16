"""Offline counterexamples; run from repository root using venv/bin/python."""
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
import pandas as pd
import requests
from src.data.crawlers.cn_provinces import MonthlyReportLink, ProvinceMonthlyReportCrawler, _normalize_narrative_report
from src.data.crawlers.cn_province_adapters.parsing.normalization import _normalize_report_table

link = MonthlyReportLink('CN-BJ', date(2026, 10, 1), '北京市2026年10月传染病疫情', 'https://official.example/monthly')
for text in ['2026年10月15日报告病例数为流行性感冒（100例）。', '2026年10月1日至10月7日报告病例数为流行性感冒（100例）。']:
    rows = _normalize_narrative_report(text, link=link, source_url=link.url, artifact_sha256='adversarial')
    print({'scenario': 'daily_count_as_monthly', 'input': text, 'rows': [(r['Date'], r['SourceDiseaseCode'], r['Cases']) for r in rows]})

table = pd.DataFrame([['甲型肝炎', '-', 2]], columns=['病名', '发病数', '死亡数'])
print({'scenario': 'known_deaths_missing_cases', 'normalization': _normalize_report_table(table, include_deaths=True)})
try:
    rows = ProvinceMonthlyReportCrawler()._normalize_tables([table], link=link, source_url=link.url, artifact_sha256='adversarial')
    print({'scenario': 'known_deaths_missing_cases', 'rows': rows})
except ValueError as error:
    print({'scenario': 'known_deaths_missing_cases', 'error': str(error)})

html = '<table><tr><th>病名</th><th>发病数</th><th>死亡数</th></tr><tr><td>甲型肝炎</td><td>3</td><td>0</td></tr></table><a href="https://official.example/broken.xlsx">附件</a>'
response = requests.Response()
response.url, response.status_code, response._content, response.encoding = link.url, 200, html.encode(), 'utf-8'
response.headers['Content-Type'] = 'text/html'
crawler = ProvinceMonthlyReportCrawler()

def getter(url, **kwargs):
    if url == link.url:
        return response
    raise requests.HTTPError('404 missing supplemental attachment')

crawler._get = getter
try:
    fetched = crawler.fetch(link)
    print({'scenario': 'html_table_plus_broken_attachment', 'rows': fetched.rows})
except Exception as error:
    print({'scenario': 'html_table_plus_broken_attachment', 'error': repr(error), 'diagnostics': crawler.report_diagnostics})
