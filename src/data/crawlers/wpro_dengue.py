"""Explicit national dengue narratives distributed in WHO WPRO reports.

Only source-stated seven-day intervals and their cumulative snapshots are
parsed. Plotted points, earlier comparison values and interpolated weeks are
outside this contract.
"""
from __future__ import annotations
from datetime import datetime
from html import unescape
import json
import re
from urllib.parse import urlsplit
import pypdfium2 as pdfium
from .base import BaseCrawler

DEFAULT_SCOPE = 'who_wpro_dengue'
LANDING_URL = 'https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue'
COUNTRIES = {
    'LA': ('Lao PDR', 'Lao People’s Democratic Republic', 'National Centre for Laboratory and Epidemiology, Ministry of Health, Lao PDR'),
    'VN': ('Viet Nam', 'Viet Nam', 'Vietnam Administration of Disease prevention, Ministry of Health, Viet Nam'),
}
HISTORY_START_YEAR = 2015
# PDFium is process-global and not thread safe. Normal organization runs in
# executor threads, so serialize all native extraction in this connector.
from src.services.situation_notices.documents import _PDFIUM_LOCK


def country_contract(country_code):
    code = str(country_code).upper()
    if code not in COUNTRIES:
        raise ValueError('Unreviewed WPRO dengue country')
    return code, COUNTRIES[code]


def discover_publications(payload, *, max_reports=24):
    html = payload.decode('utf-8')
    urls = list(dict.fromkeys(unescape(u) for u in re.findall(r'https://www\.who\.int/westernpacific/publications/m/item/dengue-situation-update[^\"\s<>]+', html)))
    if not urls or len(urls) > int(max_reports):
        raise ValueError('WHO current report listing is empty or exceeds reviewed bound')
    return urls


def discover_pdf(payload):
    html = unescape(payload.decode('utf-8'))
    urls = list(dict.fromkeys(re.findall(r'https://cdn\.who\.int/[^\s<>\"\x27]+\.pdf\?[^\s<>\"\x27]+', html)))
    urls = [u for u in urls if '/wpro---documents/emergency/surveillance/dengue/' in u]
    if len(urls) != 1:
        raise ValueError('WHO report page requires one explicit official dengue PDF')
    parsed = urlsplit(urls[0])
    if parsed.scheme != 'https' or parsed.netloc != 'cdn.who.int':
        raise ValueError('WHO PDF left official distribution host')
    return urls[0]


def extract_text(payload):
    if not payload.startswith(b'%PDF'):
        raise ValueError('WHO dengue report is not PDF')
    with _PDFIUM_LOCK, pdfium.PdfDocument(payload) as pdf:
        texts = []
        for index in range(min(len(pdf), 8)):
            page = pdf[index]
            textpage = page.get_textpage()
            try:
                texts.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
        return '\n'.join(texts).replace('\r\n', '\n')


def count_value(value):
    v = value.strip().lower()
    words = {'one': 1, 'two': 2, 'no': 0, 'zero': 0}
    if v in words:
        return words[v]
    if not re.fullmatch(r'\d+|\d{1,3}(?:[ ,]\d{3})+', v):
        raise ValueError('Invalid dengue notification count')
    return int(v.replace(' ', '').replace(',', ''))


def parse_period(text, year):
    # The publication year is an upper bound, not the observation year. A
    # January publication can explicitly report the previous December week.
    # Only source-stated endpoints are used; no epidemiological/ISO calendar
    # conversion is involved.
    m = re.search(
        r'(?:[Ff]rom|\()\s*(?P<start_day>\d{1,2})'
        r'(?:\s+(?P<start_month>[A-Z][a-z]+))?'
        r'(?:\s+(?P<start_year>20\d{2}))?\s+to\s+'
        r'(?P<end_day>\d{1,2})\s+(?P<end_month>[A-Z][a-z]+)\s+(?P<end_year>20\d{2})', text)
    if not m:
        raise ValueError('Country narrative has no explicit seven-day interval')
    end = datetime.strptime(f'{m["end_day"]} {m["end_month"]} {m["end_year"]}', '%d %B %Y').date()
    start_year = int(m['start_year'] or m['end_year'])
    begin = datetime.strptime(f'{m["start_day"]} {m["start_month"] or m["end_month"]} {start_year}', '%d %B %Y').date()
    if not m['start_year'] and m['start_month'] and begin > end:
        begin = begin.replace(year=begin.year - 1)
    if (end - begin).days != 6 or begin.year < HISTORY_START_YEAR or end.year > year:
        raise ValueError('Dengue source interval is not a reviewed seven-day period before publication')
    return begin, end, m[0]


def parse_report(payload, *, country_code, provenance, as_of=None):
    code, (name, heading, agency) = country_contract(country_code)
    text = extract_text(payload)
    title = re.search(r'(?m)^Dengue Situation Update\s+(\d+)\s*\n(\d{1,2} [A-Z][a-z]+ 20\d{2})', text)
    if not title:
        raise ValueError('WHO report identity/publication date missing')
    published = datetime.strptime(title[2], '%d %B %Y').date()
    if as_of and published > as_of:
        raise ValueError('WHO report publication lies in future')
    # Exact heading line confines current narrative; annex and chart captions
    # cannot supply a country's case count.
    m = re.search(r'^' + re.escape(heading) + r'\s*\n(.*?)(?:\nFigure\s+\d+[:.]|\nDengue Situation Update|\nSouthern Hemisphere)', text, re.M | re.S)
    if not m:
        return [], {'status': 'country_not_reported', 'country_code': code, 'report_date': published.isoformat()}
    narrative = re.sub(r'\s+', ' ', m[1]).strip()
    if 'There was no update' in narrative:
        return [], {'status': 'no_country_update', 'country_code': code, 'report_date': published.isoformat()}
    begin, end, original_period = parse_period(narrative, published.year)
    if end > published:
        raise ValueError('WHO observation interval ends after publication')
    if agency.lower() not in text.lower():
        raise ValueError('WHO source agency attribution changed')
    weekly = re.search(r'(?:,|\)),?\s*(?:a total of\s+)?([\d][\d ,]*?)\s+(?:new\s+)?(?:dengue\s+)?cases?(?:,\s+including\s+(?:[\d][\d ,]*|one|two|no|zero)\s+deaths?,)?\s+(?:were|was)\s*reported', narrative)
    if not weekly:
        raise ValueError('Country narrative lacks explicit current-period case count')
    values = [('weekly_cases', count_value(weekly[1]), begin, end, 'weekly', 'case_notifications')]
    # Only explicit current reporting-period deaths; a cumulative death clause
    # is captured below, not interpreted as weekly deaths.
    deaths = re.search(r'([\d][\d ,]*|One|Two|No|Zero)\s+deaths?\s+(?:were|was)\s*reported in this reporting period', narrative)
    if not deaths:
        deaths = re.search(r'cases?,\s+including\s+([\d][\d ,]*|one|two|no|zero)\s+deaths?,\s+(?:were|was)\s*reported', narrative, re.I)
    if deaths:
        values.append(('weekly_deaths', count_value(deaths[1]), begin, end, 'weekly', 'death_notifications'))
    cumulative = re.search(
        r'(?:Cumulatively,\s*)?(?:As of epidemiological week\s+\d+\s+of\s+(?P<asof_year>20\d{2}),\s*)?'
        r'a (?:cumulative )?total of\s+(?P<cases>[\d][\d ,]*?)\s+cases'
        r'(?:,\s+including\s+(?P<deaths>[\d][\d ,]*?)\s+deaths)?[,]?\s+'
        r'(?:have|had)\s+been\s+reported[^.]*?(?:in\s+(?P<year>20\d{2})|(?P<this_year>for this year))', narrative, re.I)
    cumulative_year_mismatch = False
    if cumulative:
        source_years = {int(value) for value in (cumulative['asof_year'], cumulative['year']) if value}
        if cumulative['this_year']:
            source_years.add(published.year)
        cumulative_year_mismatch = source_years != {end.year}
        # Retain the existing publication-dated snapshot contract. A prior-year
        # comparison or ambiguous January summary cannot become this source
        # interval's cumulative observation.
        if not cumulative_year_mismatch:
            start = end.replace(month=1, day=1)
            values.append(('cumulative_cases', count_value(cumulative['cases']), start, published, 'cumulative', 'cumulative_case_notifications'))
            if cumulative['deaths']:
                values.append(('cumulative_deaths', count_value(cumulative['deaths']), start, published, 'cumulative', 'cumulative_death_notifications'))
    week_match = re.search(r'epidemiological week\s+(\d+)', narrative)
    rows = []
    for category, count, period_start, period_end, period_type, measure in values:
        rows.append({
            'Date': period_end.isoformat(), 'Year': str(period_end.year), 'Month': str(period_end.month),
            'PeriodStart': period_start.isoformat(), 'PeriodEnd': period_end.isoformat(),
            'DiseaseCode': '__source_native__', 'SourceDiseaseCode': f'dengue__{category}',
            'RawDiseaseLabel': f'Dengue {category.replace("_", " ")}', 'Cases': str(count),
            'MissingValueStatus': 'reported', 'Frequency': 'weekly', 'PeriodType': period_type,
            'Measure': measure, 'Unit': 'count', 'ReportingBasis': 'who_distributed_national_dengue_notifications',
            'DefinitionVersion': f'wpro_dengue_{code.lower()}_explicit_interval_v1',
            'Geography': name, 'GeographyKey': f'country:{code}:national',
            'Source': f'{name} WHO WPRO Dengue Updates', 'SourceURL': LANDING_URL,
            'SourcePublishedAt': published.isoformat(), 'SourceReportNumber': title[1],
            'OriginatingAgency': agency, 'Distribution': 'WHO Western Pacific Regional Office',
            'SourceDateBasis': 'publication_dated_cumulative_snapshot' if period_type == 'cumulative' else 'explicit_reporting_interval',
            'SourceCumulativeCutoffVerified': 'false' if period_type == 'cumulative' else '',
            'SourceCumulativeYear': str(end.year) if period_type == 'cumulative' else '',
            'SourcePeriodLabel': original_period, 'SourceEpiWeek': week_match[1] if week_match else '',
            'SourceNarrative': narrative, 'DatasetStatus': 'provisional_revisable', 'IsProvisional': 'true',
            'RevisionSemantics': 'authoritative_revision', 'AuthoritativeRevision': 'true',
            'PublicReleaseEnabled': 'false', 'AggregationPolicy': 'non_additive',
            'Dimensions': json.dumps({'reporting_country': code, 'source_category': category}, sort_keys=True),
            **provenance,
        })
    return rows, {'status': 'parsed', 'country_code': code, 'report_date': published.isoformat(),
                  'observation_start': begin.isoformat(), 'observation_end': end.isoformat(), 'rows': len(rows),
                  'cumulative_year_mismatch': cumulative_year_mismatch}


class WPRODengueCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, delay=0.1)
    async def crawl(self, **kwargs):
        raise NotImplementedError('Use staged WPRO capture')
    def parse(self, response):
        raise NotImplementedError('Use captured WHO PDF with source provenance')
