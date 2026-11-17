import io
from types import SimpleNamespace

from openpyxl import Workbook
import pytest

from scripts.population_sources.jp import JP_PREFECTURES, fetch_jp, parse_workbook


def workbook(year=2024, *, unit='千人', heading='総人口', sex='男女計', duplicate=False):
    book = Workbook()
    sheet = book.active
    sheet.append([f'都道府県、男女別人口及び人口性比―総人口、日本人人口（{year}年10月1日現在）'])
    sheet.append([f'（単位 {unit}）'])
    sheet.append([None, None, heading, None, None, '日本人人口'])
    sheet.append([None, None, sex, '男', '女', '男女計'])
    for index, name in enumerate(JP_PREFECTURES):
        sheet.append([f'{index+1:02d}', name, 100 + index, 48, 52, 90 + index])
    if duplicate:
        sheet.cell(6, 2).value = '北海道'
    body = io.BytesIO()
    book.save(body)
    return body.getvalue()


def test_total_population_includes_foreign_residents_and_converts_published_thousands():
    rows = parse_workbook(workbook(), 2024, 'https://www.stat.go.jp/published.xlsx')
    assert len(rows) == 47
    assert rows[0].population == 100_000  # not Japanese-only 90,000
    assert rows[12].country_code == 'JP-13'
    assert all(row.source == 'StatisticsJapan' and row.reference_date == '2024-10-01' for row in rows)


@pytest.mark.parametrize('kwargs', [
    {'unit': '人'}, {'heading': '日本人人口'}, {'sex': '男'}, {'duplicate': True},
])
def test_japan_rejects_changed_unit_demographic_scope_or_geography(kwargs):
    with pytest.raises(ValueError):
        parse_workbook(workbook(**kwargs), 2024, 'https://www.stat.go.jp/published.xlsx')


def test_japan_rejects_other_year_without_filling_requested_year():
    with pytest.raises(ValueError):
        parse_workbook(workbook(2023), 2024, 'https://www.stat.go.jp/published.xlsx')


def test_japan_discovers_official_new_annual_links_not_a_pinned_workbook():
    calls = []
    class Source:
        def fetch(self, name, url):
            calls.append(url)
            if name == 'jp_population_catalogue.html':
                html = ''.join(f'<a href="/data/jinsui/{year}np/index.html">{year}年</a>' for year in [2023, 2024, 2999])
                html += '<a href="https://www.e-stat.go.jp/stat-search/files?lid=monthly">2025年（令和7年）12月全国人口</a>'
                return SimpleNamespace(url=url, content=html.encode())
            if name.endswith('_release.html'):
                year = int(name.split('_')[2])
                html = f'<a href="new-vintage/{year}-totals.xlsx">第2表 都道府県、男女別人口及び人口性比―総人口、日本人人口</a>'
                return SimpleNamespace(url=url, content=html.encode())
            year = int(name.split('_')[2].split('.')[0])
            return SimpleNamespace(url=url, content=workbook(year))
    rows = fetch_jp(Source())
    assert len(rows) == 94 and {row.year for row in rows} == {2023, 2024}
    assert all('/new-vintage/' in row.source_url for row in rows)
    assert not any('2999' in url for url in calls)


def test_japan_moved_columns_follow_the_total_header_not_the_old_column_position():
    from openpyxl import load_workbook
    original = load_workbook(io.BytesIO(workbook()))
    moved = Workbook()
    sheet = moved.active
    for row in original.active.values:
        padded = list(row) + [None] * max(0, 6 - len(row))
        sheet.append([padded[index] for index in [0, 1, 5, 3, 4, 2]])
    body = io.BytesIO(); moved.save(body)
    rows = parse_workbook(body.getvalue(), 2024, 'https://www.stat.go.jp/moved.xlsx')
    assert rows[0].population == 100_000


def test_new_current_year_is_discovered_and_a_missing_formal_workbook_fails():
    from scripts.population_sources import jp
    class Today:
        @staticmethod
        def today():
            from datetime import date
            return date(2026, 11, 16)
        def __new__(cls, *args):
            from datetime import date
            return date(*args)
    class Source:
        def __init__(self, missing=False): self.missing = missing
        def fetch(self, name, url):
            if name == 'jp_population_catalogue.html':
                return SimpleNamespace(url=url, content=b'<a href="/data/jinsui/2026np/index.html?release=new">2026</a>')
            if name.endswith('_release.html'):
                html = '' if self.missing else '<a href="latest.xlsx">第2表 都道府県、男女別人口及び人口性比―総人口、日本人人口</a>'
                return SimpleNamespace(url=url, content=html.encode())
            return SimpleNamespace(url=url, content=workbook(2026))
    from unittest.mock import patch
    with patch.object(jp, 'date', Today):
        assert {row.year for row in fetch_jp(Source())} == {2026}
        with pytest.raises(ValueError, match='2026.*listing changed'):
            fetch_jp(Source(missing=True))


def test_japan_explicit_census_pending_is_reported_and_national_monthly_links_are_excluded():
    class Source:
        def __init__(self): self.sources = {'jp_population_catalogue.html': {}}
        def fetch(self, name, url):
            if name == 'jp_population_catalogue.html':
                html = '''<a href="/data/jinsui/2024np/index.html">2024年</a>
<a href="/data/kokusei/2025/index.html">令和7年国勢調査</a>
<a href="https://www.e-stat.go.jp/stat-search/files?lid=national-month">2025年（令和7年）12月全国人口、2026年5月概算</a>'''
                return SimpleNamespace(url=url, content=html.encode())
            if name.endswith('_release.html'):
                return SimpleNamespace(url=url, content='<a href="totals.xlsx">第2表 都道府県、男女別人口及び人口性比―総人口、日本人人口</a>'.encode())
            assert 'national-month' not in url and 'kokusei' not in url
            return SimpleNamespace(url=url, content=workbook())
    source = Source()
    rows = fetch_jp(source)
    assert len(rows) == 47 and {row.year for row in rows} == {2024}
    notes = source.sources['jp_population_catalogue.html']['population_discovery']
    assert notes['published_years'] == [2024]
    assert notes['pending_census_years'] == {'2025': 'https://www.stat.go.jp/data/kokusei/2025/index.html'}


def test_japan_workbook_http_failure_is_not_silently_skipped():
    import requests
    class Source:
        def fetch(self, name, url):
            if name == 'jp_population_catalogue.html':
                return SimpleNamespace(url=url, content=b'<a href="/data/jinsui/2024np/index.html">2024</a>')
            if name.endswith('_release.html'):
                return SimpleNamespace(url=url, content='<a href="totals.xlsx">第2表 都道府県、男女別人口及び人口性比―総人口、日本人人口</a>'.encode())
            raise requests.HTTPError('404 formal workbook missing')
    with pytest.raises(requests.HTTPError, match='404'):
        fetch_jp(Source())


def rebased_workbook(*, machine=True, incomplete=False, wrong_unit=False):
    book = Workbook(); sheet = book.active
    if machine:
        sheet.append(['統計名：', '人口推計'])
        sheet.append(['表題：', '都道府県、男女別人口（各年10月1日現在）'])
        sheet.append(['人口区分', '性別', '都道府県コード', '地域', '区分', '単位', *[f'{year}年' for year in range(2015, 2021)]])
        for index, (name, code) in enumerate(JP_PREFECTURES.items()):
            if incomplete and index == 46: continue
            sheet.append(['総人口', '男女計', code[3:] + '000', name, '人口', '人' if wrong_unit else '千人', *[500 + index] * 6])
            sheet.append(['日本人人口', '男女計', code[3:] + '000', name, '人口', '千人', *[400 + index] * 6])
    else:
        sheet.title = '総人口'
        sheet.append(['都道府県、男女別人口（各年10月1日現在）'])
        sheet.append(['（単位 千人）'])
        sheet.append(['総人口'])
        sheet.append(['都道府県', *[value for year in range(2010, 2016) for value in [f'平成{year-1988}年', None, None]]])
        sheet.append([None, *['男女計', '男', '女'] * 6])
        for index, name in enumerate(JP_PREFECTURES):
            if incomplete and index == 46: continue
            sheet.append([name, *[500 + index, 220, 280] * 6])
        japanese = book.create_sheet('日本人人口')
        japanese.append(['都道府県、男女別人口（各年10月1日現在）'])
        japanese.append(['（単位 千人）'])
        japanese.append(['日本人人口'])
        japanese.append(['都道府県', *[value for year in range(2010, 2016) for value in [f'平成{year-1988}年', None, None]]])
        japanese.append([None, *['男女計', '男', '女'] * 6])
        for name in JP_PREFECTURES: japanese.append([name, *[400, 180, 220] * 6])
    body = io.BytesIO(); book.save(body)
    return body.getvalue()


@pytest.mark.parametrize('machine', [True, False])
def test_official_census_backcast_uses_published_totals_and_excludes_census_endpoints(machine):
    from scripts.population_sources.jp import REBASED_BASIS, parse_rebased_workbook
    base, end = (2015, 2020) if machine else (2010, 2015)
    rows = parse_rebased_workbook(rebased_workbook(machine=machine), base, end, 'https://www.e-stat.go.jp/revised.xls')
    assert len(rows) == 47 * 4
    assert {row.year for row in rows} == set(range(base + 1, end))
    assert {row.population for row in rows if row.country_code == 'JP-01'} == {500_000}
    assert all(row.basis == REBASED_BASIS for row in rows)
    assert all(row.source_url == 'https://www.e-stat.go.jp/revised.xls' for row in rows)


@pytest.mark.parametrize('kwargs', [{'incomplete': True}, {'wrong_unit': True}, {'machine': False, 'incomplete': True}])
def test_official_census_backcast_rejects_missing_prefectures_or_changed_unit(kwargs):
    from scripts.population_sources.jp import parse_rebased_workbook
    base, end = (2010, 2015) if kwargs.get('machine') is False else (2015, 2020)
    with pytest.raises(ValueError):
        parse_rebased_workbook(rebased_workbook(**kwargs), base, end, 'https://www.e-stat.go.jp/revised.xls')


def test_superseded_annual_population_is_not_accepted_when_revision_catalogue_disappears():
    from scripts.population_sources.jp import _official_backcasts
    from scripts.import_subdivision_population import SubdivisionPopulation
    from bs4 import BeautifulSoup
    old = SubdivisionPopulation('JP-13', 2019, 1, 'StatisticsJapan', '2019-10-01', 'https://www.stat.go.jp/old.xls', 'october_estimated_resident_population')
    with pytest.raises(ValueError, match='refusing superseded'):
        _official_backcasts(None, BeautifulSoup('', 'html.parser'), [old], start_year=2011)
