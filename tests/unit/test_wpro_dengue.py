"""WPRO numerical contracts from an actual WHO dengue publication."""
from datetime import date
from pathlib import Path
import pytest

from src.data.crawlers import wpro_dengue as crawler
from src.data.processors.wpro_dengue import WPRODengueUpdater

PDF = Path('tests/fixtures/ingestion/WPRO/dengue_20261029.pdf').read_bytes()


def test_actual_la_period_deaths_and_cumulative_snapshot_are_distinct():
    rows, audit = crawler.parse_report(PDF, country_code='LA', provenance={})
    values = {row['SourceDiseaseCode']: row for row in rows}
    assert values['dengue__weekly_cases']['Cases'] == '232'
    assert values['dengue__weekly_cases']['Date'] == '2026-10-18'
    assert values['dengue__weekly_cases']['PeriodStart'] == '2026-10-12'
    assert values['dengue__weekly_deaths']['Cases'] == '1'
    snapshot = values['dengue__cumulative_cases']
    assert snapshot['Cases'] == '6133'
    assert snapshot['Date'] == '2026-10-29'
    assert snapshot['SourceCumulativeCutoffVerified'] == 'false'
    assert snapshot['Measure'] == 'cumulative_case_notifications'
    assert snapshot['PublicReleaseEnabled'] == 'false'
    assert audit['status'] == 'parsed'


def test_actual_vietnam_does_not_use_previous_week_or_comparison_year():
    rows, _ = crawler.parse_report(PDF, country_code='VN', provenance={})
    values = {row['SourceDiseaseCode']: row['Cases'] for row in rows}
    assert values == {'dengue__weekly_cases': '5390', 'dengue__cumulative_cases': '166507', 'dengue__cumulative_deaths': '21'}
    assert rows[0]['PeriodStart'] == '2026-10-19'
    assert rows[0]['Date'] == '2026-10-25'
    assert rows[0]['GeographyKey'] == 'country:VN:national'


def test_country_without_numeric_section_is_not_an_zero(monkeypatch):
    source = crawler.extract_text(PDF)
    source = source.replace('\nViet Nam\n', '\nUnreported Country\n')
    monkeypatch.setattr(crawler, 'extract_text', lambda payload: source)
    rows, audit = crawler.parse_report(PDF, country_code='VN', provenance={})
    assert rows == []
    assert audit['status'] == 'country_not_reported'


def test_no_update_does_not_reuse_previous_period(monkeypatch):
    source = crawler.extract_text(PDF).replace('In epidemiological week 43', 'There was no update in this reporting period. In epidemiological week 43')
    monkeypatch.setattr(crawler, 'extract_text', lambda payload: source)
    rows, audit = crawler.parse_report(PDF, country_code='VN', provenance={})
    assert rows == []
    assert audit['status'] == 'no_country_update'


def test_other_countrys_interval_cannot_supply_vietnam_calendar(monkeypatch):
    source = crawler.extract_text(PDF).replace('In epidemiological week 43 (19 to 25 October 2026)', 'In epidemiological week 43')
    monkeypatch.setattr(crawler, 'extract_text', lambda payload: source)
    with pytest.raises(ValueError, match='explicit seven-day'):
        crawler.parse_report(PDF, country_code='VN', provenance={})


@pytest.mark.parametrize('text', ['From 1 to 31 August 2026', 'From 12 to 19 October 2026'])
def test_unreviewed_intervals_rejected(text):
    with pytest.raises(ValueError, match='seven-day'):
        crawler.parse_period(text, 2026)


def test_explicit_cross_month_interval():
    begin, end, _ = crawler.parse_period('During week36 (from 31 August to 6 September 2026)', 2026)
    assert begin == date(2026, 8, 31) and end == date(2026, 9, 6)


@pytest.mark.parametrize('country', ['LA', 'VN'])
def test_updater_preserves_previous_year_week_in_january_publication(monkeypatch, tmp_path, country):
    _, heading, agency = crawler.COUNTRIES[country]
    text = (f'Dengue Situation Update 100\n8 January 2026\n{heading}\n'
            f'From 25 to 31 December 2025, 123 dengue cases were reported.\n'
            f'Figure 1: Cases\n{agency}\n')
    monkeypatch.setattr(crawler, 'extract_text', lambda _: text)
    rows, _ = crawler.parse_report(b'%PDF offline narrative', country_code=country,
                                   provenance={}, as_of=date(2026, 1, 8))
    updater = WPRODengueUpdater(country, output_csv=tmp_path / 'observations.csv')
    result = updater.prepare_rows(rows)
    assert len(result.rows) == 1
    assert result.rows[0]['Date'] == '2025-12-31'
    assert result.source_latest_date == date(2025, 12, 31)


def test_pdf_discovery_handles_official_unquoted_href():
    page = b'<a href=https://cdn.who.int/media/docs/default-source/wpro---documents/emergency/surveillance/dengue/dengue_20261029.pdf?sfvrsn=1&amp;download=true >Download</a>'
    assert crawler.discover_pdf(page).endswith('sfvrsn=1&download=true')
    with pytest.raises(ValueError):
        crawler.discover_pdf(page.replace(b'cdn.who.int', b'evil.example'))


def test_new_country_or_future_publication_rejected():
    with pytest.raises(ValueError, match='Unreviewed'):
        WPRODengueUpdater('PH')
    with pytest.raises(ValueError, match='future'):
        crawler.parse_report(PDF, country_code='LA', provenance={}, as_of=date(2026, 10, 28))


def test_real_pdf_parser_serializes_native_parallel_calls():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda code: crawler.parse_report(PDF, country_code=code, provenance={})[0], ['LA', 'VN']))
    assert [len(rows) for rows in results] == [3, 3]


def test_offline_replay_validates_listing_plan_and_pdf(monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from src.services.ingestion.countries import wpro_dengue as connector
    pub = 'https://www.who.int/westernpacific/publications/m/item/dengue-situation-update---757--29-october-2026'
    pdf_url = 'https://cdn.who.int/media/docs/default-source/wpro---documents/emergency/surveillance/dengue/dengue_20261029.pdf?download=true'
    listing = f'<a href="{pub}">Dengue Situation Update</a>'.encode()
    page = f'<a href={pdf_url}>Download</a>'.encode()
    timestamp = '2026-11-16T03:53:55+00:00'
    plan = {'country_code': 'LA', 'retrieved_at': timestamp,
            'listing_sha256': hashlib.sha256(listing).hexdigest(), 'publications': [pub],
            'reports': [{'publication': pub, 'pdf': pdf_url, 'sha256': hashlib.sha256(PDF).hexdigest()}],
            'failed_reports': []}
    def artifact(payload, url, endpoint):
        return SimpleNamespace(payload=payload, source_url=url, sha256=hashlib.sha256(payload).hexdigest(),
                               metadata_={'endpoint': endpoint, 'retrieved_at': timestamp})
    originals = [artifact(listing, crawler.LANDING_URL, 'listing'), artifact(page, pub, 'publication'),
                 artifact(PDF, pdf_url, 'pdf'), artifact(json.dumps(plan).encode(), crawler.LANDING_URL, 'collection_plan')]
    monkeypatch.setattr(connector, 'read_artifact', lambda a: a.payload)
    result = connector.organize(originals, updater=WPRODengueUpdater('LA'), country_code='LA')
    assert len(result.rows) == 3
    assert result.source_audit['captured_reports'] == 1
    assert result.source_audit['coverage_complete'] is False
    assert result.rows[0]['RawSHA256'] == hashlib.sha256(PDF).hexdigest()
    with pytest.raises(ValueError, match='country/listing'):
        connector.organize(originals, updater=WPRODengueUpdater('VN'), country_code='VN')
    with pytest.raises(ValueError, match='Duplicate'):
        connector.organize(originals + [originals[1]], updater=WPRODengueUpdater('LA'))
    plan['reports'][0]['sha256'] = '0' * 64
    originals[-1].payload = json.dumps(plan).encode()
    with pytest.raises(ValueError, match='differs'):
        connector.organize(originals, updater=WPRODengueUpdater('LA'))
