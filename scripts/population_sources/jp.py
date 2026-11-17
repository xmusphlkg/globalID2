"""Japan Statistics Bureau's published 1 October prefecture population totals.

The live annual release catalogue is the discovery root. Annual workbooks and
census-year Population Estimates workbooks use total population (including
foreign residents), both sexes, all ages. Their published thousand-person unit
is converted to persons without inventing extra precision. Unpublished years
are absent; neither national totals nor Japanese-only totals are substitutes.
Published census backcasts supersede the affected annual estimates; their
official revised values are imported directly under a separate population basis.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import io
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import pandas as pd

from scripts.import_subdivision_population import SubdivisionPopulation

JP_CATALOGUE = 'https://www.stat.go.jp/data/jinsui/2.html'
JP_PREFECTURES = dict(zip(
    '北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県'.split(),
    (f'JP-{index:02d}' for index in range(1, 48)), strict=True,
))
REBASED_BASIS = 'october_official_census_rebased_resident_population'


def clean(value) -> str:
    if value is None or isinstance(value, float) and pd.isna(value):
        return ''
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


def parse_workbook(content: bytes, year: int, source_url: str) -> list[SubdivisionPopulation]:
    """Read the explicitly labelled total-population / both-sexes column only."""
    workbooks = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    candidates = []
    for sheet in workbooks.values():
        records = sheet.where(pd.notna(sheet), None).values.tolist()
        header = [[clean(cell) for cell in row] for row in records[:25]]
        header_text = ''.join(''.join(row) for row in header)
        if not re.search(r'単位[^）)]*千人', header_text):
            continue
        years = {int(value) for value in re.findall(r'((?:19|20)\d{2})年10月1日', header_text)}
        years |= {1988 + int(value) for value in re.findall(r'平成(\d+)年10月1日', header_text)}
        years |= {2018 + int(value) for value in re.findall(r'令和(\d+)年10月1日', header_text)}
        if years != {year}:
            continue
        total_headers = [(r, c) for r, row in enumerate(header) for c, cell in enumerate(row) if cell == '総人口']
        for header_row, col in total_headers:
            if not any(col < len(row) and row[col] == '男女計' for row in header[header_row + 1:]):
                continue
            observed = {}
            # Census-year workbooks repeat gender/nationality sections. The
            # first complete block under the explicit totals heading is used.
            for row in records[header_row + 1:]:
                labels = [clean(cell) for cell in row]
                names = [name for name in JP_PREFECTURES if name in labels]
                if not names:
                    continue
                if len(names) != 1 or names[0] in observed:
                    raise ValueError('Duplicate or ambiguous Japanese prefecture row')
                if col >= len(row):
                    raise ValueError('Incomplete Japanese population row')
                value = row[col]
                if value is None or isinstance(value, bool):
                    raise ValueError('Missing Japanese prefecture population')
                number = Decimal(str(value)) * 1000
                if not number.is_finite() or number <= 0 or number != number.to_integral_value():
                    raise ValueError('Invalid Japanese prefecture population')
                observed[names[0]] = int(number)
                if len(observed) == 47:
                    break
            if set(observed) == set(JP_PREFECTURES):
                candidates.append(observed)
    if len(candidates) != 1:
        raise ValueError('Japanese population workbook lacks one complete 47-prefecture all-resident total block')
    return [SubdivisionPopulation(code, year, candidates[0][name], 'StatisticsJapan', f'{year}-10-01', source_url, 'october_estimated_resident_population') for name, code in JP_PREFECTURES.items()]


def _official_url(base: str, href: str) -> str:
    url = urljoin(base, href)
    if url.startswith('http://'):
        url = 'https://' + url.removeprefix('http://')
    host = urlparse(url).hostname or ''
    if host not in {'www.stat.go.jp', 'www.e-stat.go.jp'}:
        raise ValueError('Japanese population discovery left official domains')
    return url


def _workbook_links(page) -> list[str]:
    soup = BeautifulSoup(page.content, 'html.parser')
    links = []
    for anchor in soup.select('a[href]'):
        href, text = anchor['href'], clean(anchor.get_text())
        if re.search(r'\.xlsx?$', urlparse(href).path, re.I) and '第2表' in text and '都道府県' in text and '総人口' in text:
            links.append(_official_url(page.url, href))
        elif 'file-download' in href and 'EXCEL' in text.upper():
            parent = anchor
            for _ in range(4):
                parent = parent.parent
            title = clean(parent.get_text())
            if '都道府県' in title and '総人口' in title and '男女' in title:
                links.append(_official_url(page.url, href))
    return sorted(set(links))


def _year(value: str) -> int | None:
    match = re.fullmatch(r'((?:19|20)\d{2})(?:年)?(?:\(国勢調査\))?', value)
    if match:
        return int(match[1])
    match = re.fullmatch(r'(平成|令和)(\d+)年(?:\(国勢調査\))?', value)
    return (1988 if match[1] == '平成' else 2018) + int(match[2]) if match else None


def parse_rebased_workbook(content: bytes, base_year: int, census_year: int, source_url: str) -> list[SubdivisionPopulation]:
    """Use published census backcasts; never compute interpolated values here."""
    results = {}
    def add(name, year, value):
        if not base_year <= year <= census_year:
            raise ValueError('Japanese official backcast has an unexpected year')
        code = JP_PREFECTURES[name]
        number = Decimal(str(value)) * 1000
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError('Invalid official Japanese backcast population')
        key = code, year
        if key in results:
            raise ValueError('Duplicate official Japanese backcast total')
        results[key] = SubdivisionPopulation(code, year, int(number), 'StatisticsJapan', f'{year}-10-01', source_url, REBASED_BASIS)

    for sheet in pd.read_excel(io.BytesIO(content), sheet_name=None, header=None).values():
        records = sheet.where(pd.notna(sheet), None).values.tolist()
        normalized = [[clean(value) for value in row] for row in records]
        # Recent e-Stat machine workbooks label nationality, sex, unit and
        # geography on every row. They must all agree with the total scope.
        machine_header = next((index for index, row in enumerate(normalized[:20]) if {'人口区分', '性別', '都道府県コード', '地域', '区分', '単位'}.issubset(row)), None)
        if machine_header is not None:
            header = normalized[machine_header]
            columns = {label: header.index(label) for label in ['人口区分', '性別', '都道府県コード', '地域', '区分', '単位']}
            years = {index: _year(value) for index, value in enumerate(header) if _year(value) is not None}
            if not years or '各年10月1日現在' not in ''.join(''.join(row) for row in normalized[:machine_header]):
                raise ValueError('Japanese official backcast reference period changed')
            for row in records[machine_header + 1:]:
                values = [clean(value) for value in row]
                if values[columns['人口区分']] != '総人口' or values[columns['性別']] != '男女計':
                    continue
                if values[columns['区分']] != '人口' or values[columns['単位']] != '千人':
                    raise ValueError('Japanese official backcast population units changed')
                name = values[columns['地域']]
                if name == '全国':
                    continue
                if name not in JP_PREFECTURES:
                    raise ValueError('Japanese official backcast prefecture changed')
                region = values[columns['都道府県コード']]
                if region != JP_PREFECTURES[name][3:] + '000':
                    raise ValueError('Japanese official backcast geography code/name mismatch')
                for index, year in years.items():
                    add(name, year, row[index])
            continue
        # The 2015 census revision uses a separate total-population sheet,
        # with a labelled both-sexes column under each census/estimate year.
        header = normalized[:25]
        if not any(value == '総人口' for row in header for value in row):
            continue
        text = ''.join(''.join(row) for row in header)
        if not re.search(r'単位[^）)]*千人', text) or '各年10月1日現在' not in text:
            raise ValueError('Japanese official backcast population unit/reference changed')
        sex_headers = [(i, row) for i, row in enumerate(header) if row.count('男女計') > 1]
        if len(sex_headers) != 1:
            raise ValueError('Japanese official backcast both-sexes columns changed')
        sex_row, labels = sex_headers[0]
        years = {}
        for col, label in enumerate(labels):
            if label != '男女計':
                continue
            candidates = [_year(row[col]) for row in reversed(header[:sex_row]) if col < len(row) and _year(row[col]) is not None]
            if not candidates:
                raise ValueError('Japanese official backcast year columns changed')
            years[col] = candidates[0]
        for row in records[sex_row + 1:]:
            names = [name for name in JP_PREFECTURES if name in [clean(value) for value in row]]
            if not names:
                continue
            if len(names) != 1:
                raise ValueError('Ambiguous official Japanese backcast geography')
            for col, year in years.items():
                add(names[0], year, row[col])
    # Endpoints are census observations; only the official revised estimates
    # between them override original annual editions.
    for year in range(base_year + 1, census_year):
        if {code for code, y in results if y == year} != set(JP_PREFECTURES.values()):
            raise ValueError(f'Incomplete Japanese official backcast population year {year}')
    return [row for row in results.values() if base_year < row.year < census_year]


def _official_backcasts(source, catalogue, annual_rows, *, start_year):
    periods = {}
    for anchor in catalogue.select('a[href]'):
        label = clean(anchor.get_text())
        if '国勢調査結果による補間補正人口' not in label:
            continue
        years = []
        for era, first, second in re.findall(r'(平成|令和)(\d+)年(?:及び(\d+)年)?', label):
            offset = 1988 if era == '平成' else 2018
            years.extend(offset + int(value) for value in (first, second) if value)
        if len(years) == 2 and max(years) > start_year:
            periods[min(years), max(years)] = _official_url(JP_CATALOGUE, anchor['href'])
    years_present = {row.year for row in annual_rows}
    for period in [(2010, 2015), (2015, 2020)]:
        if any(period[0] < year < period[1] for year in years_present) and period not in periods:
            raise ValueError('Japanese published census revision catalogue is missing; refusing superseded annual totals')
    rows, notes = {}, []
    for (base_year, census_year), url in sorted(periods.items(), key=lambda item: item[0][1]):
        method = source.fetch(f'jp_backcast_{census_year}_method.html', url)
        method_page = BeautifulSoup(method.content, 'html.parser')
        lists = {_official_url(method.url, a['href']) for a in method_page.select('a[href]') if '統計表' in clean(a.get_text()) and urlparse(urljoin(method.url, a['href'])).hostname == 'www.e-stat.go.jp'}
        if len(lists) != 1:
            raise ValueError('Japanese official census revision table listing changed')
        listing = source.fetch(f'jp_backcast_{census_year}_listing.html', lists.pop())
        downloads = set()
        for anchor in BeautifulSoup(listing.content, 'html.parser').select('a[href]'):
            if 'file-download' not in anchor['href'] or 'fileKind=0' not in anchor['href'] or 'EXCEL' not in clean(anchor.get_text()).upper():
                continue
            parent = anchor
            for _ in range(4):
                parent = parent.parent
            title = clean(parent.get_text())
            if re.search(r'都道府県[,、]?男女別人口[―－-]総人口', title) and not any(word in title for word in ['人口性比', '年齢', '割合']):
                downloads.add(_official_url(listing.url, anchor['href']))
        if len(downloads) != 1:
            raise ValueError('Japanese official all-resident backcast workbook listing changed')
        workbook = source.fetch(f'jp_backcast_{census_year}.xlsx', downloads.pop())
        source_url = _official_url(workbook.url, workbook.url)
        revised = parse_rebased_workbook(workbook.content, base_year, census_year, source_url)
        for row in revised:
            if row.year >= start_year:
                rows[row.country_code, row.year] = row
        notes.append({'base_census_year': base_year, 'new_census_year': census_year, 'revised_years': sorted({row.year for row in revised}), 'source_url': source_url})
    return rows, notes


def fetch_jp(source, *, start_year: int = 2011) -> list[SubdivisionPopulation]:
    page = source.fetch('jp_population_catalogue.html', JP_CATALOGUE)
    soup = BeautifulSoup(page.content, 'html.parser')
    annual, census, pending_census = {}, {}, {}
    today = date.today()
    for anchor in soup.select('a[href]'):
        href, label = anchor['href'], clean(anchor.get_text())
        path = urlparse(urljoin(page.url, href)).path
        match = re.fullmatch(r'/data/jinsui/(\d{4})np/(?:index\.html)?', path)
        if match and start_year <= int(match[1]) <= today.year and date(int(match[1]), 10, 1) <= today:
            annual[int(match[1])] = _official_url(page.url, href)
        # Statistics Bureau links the census-year Population Estimates tables
        # through e-Stat. Discover those lists rather than pinning file IDs.
        if urlparse(urljoin(page.url, href)).hostname == 'www.e-stat.go.jp' and re.fullmatch(r'\d{4}年\([^)]*\)', label):
            year = int(label[:4])
            if start_year <= year <= today.year and date(year, 10, 1) <= today:
                census[year] = _official_url(page.url, href)
        census_match = re.fullmatch(r'/data/kokusei/(\d{4})/(?:index\.html)?', path)
        if census_match and '国勢調査' in label and start_year <= int(census_match[1]) <= today.year:
            # A census landing page is not the Population Estimates' reviewed
            # all-resident prefecture table. Record the pending year explicitly.
            pending_census[int(census_match[1])] = _official_url(page.url, href)
    publications = {**annual, **census}
    if not annual:
        raise ValueError('Japanese annual population release catalogue changed')
    catalogue_metadata = getattr(source, 'sources', {}).get('jp_population_catalogue.html')
    if catalogue_metadata is not None:
        catalogue_metadata['population_discovery'] = {
            'published_years': sorted(publications),
            'pending_census_years': {str(year): url for year, url in sorted(pending_census.items()) if year not in publications},
            'pending_reason': 'census_landing_page_is_not_a_reviewed_annual_prefecture_estimate',
            'historical_vintage_policy': 'published_annual_editions_not_a_unified_latest_census_backcast',
        }
    rows = []
    for year, url in sorted(publications.items()):
        release = source.fetch(f'jp_population_{year}_release.html', url)
        links = _workbook_links(release)
        if len(links) != 1:
            raise ValueError(f'Japanese {year} prefecture population workbook listing changed')
        workbook = source.fetch(f'jp_population_{year}.xlsx', links[0])
        rows.extend(parse_workbook(workbook.content, year, _official_url(workbook.url, workbook.url)))
    revisions, notes = _official_backcasts(source, soup, rows, start_year=start_year)
    combined = {(row.country_code, row.year): row for row in rows}
    combined.update(revisions)
    if catalogue_metadata is not None:
        catalogue_metadata['population_discovery']['official_backcasts'] = notes
        catalogue_metadata['population_discovery']['historical_vintage_policy'] = 'official_census_revisions_override_original_annual_editions_where_published'
    return sorted(combined.values(), key=lambda row: (row.country_code, row.year))
