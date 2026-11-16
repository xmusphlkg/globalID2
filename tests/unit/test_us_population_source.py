"""Official Census discovery, geography and annual total denominator safeguards."""
import csv
import io
from types import SimpleNamespace

import pytest

from scripts.population_sources.us import (
    BASIS, CENSUS_DIRECTORY, CORE_STATES, FIPS, fetch_us,
    parse_intercensal_state_totals, parse_state_totals,
)

URL = CENSUS_DIRECTORY + '2020-2025/state/totals/NST-EST2025-ALLDATA.csv'


def csv_fixture(first=2020, last=2025, *, puerto_rico=True, value=1_000_000):
    fields = ['SUMLEV', 'STATE', 'NAME', 'CENSUS2020POP', 'ESTIMATESBASE2020', *[f'POPESTIMATE{year}' for year in range(first, last + 1)]]
    records = [{**{field: '999999999' for field in fields}, 'SUMLEV': '010', 'STATE': '00', 'NAME': 'United States'}]
    for ident, abbreviation in FIPS.items():
        if ident == '72' and not puerto_rico:
            continue
        records.append({**{f'POPESTIMATE{year}': str(value + year) for year in range(first, last + 1)}, 'SUMLEV': '040', 'STATE': ident, 'NAME': abbreviation, 'CENSUS2020POP': '999999999', 'ESTIMATESBASE2020': '999999999'})
    return fields, records


def encode(fields, records):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


def test_official_july_estimates_keep_exact_local_geography_and_ignore_census_base():
    fields, records = csv_fixture()
    rows = parse_state_totals(encode(fields, records), URL, first_year=2020, last_year=2025)
    assert len(rows) == 52 * 6
    assert {row.source for row in rows} == {'Census'}
    assert {row.basis for row in rows} == {BASIS}
    assert all(row.reference_date == f'{row.year}-07-01' and row.population == 1_000_000 + row.year for row in rows)
    assert 'US' not in {row.country_code for row in rows}
    assert 'US-PR' in {row.country_code for row in rows}
    assert not {'US-AS', 'US-GU', 'US-MP', 'US-VI'}.intersection(row.country_code for row in rows)


@pytest.mark.parametrize('invalid', ['missing_state', 'duplicate', 'unknown_fips', 'nonpositive', 'fractional', 'unknown_level', 'missing_year', 'unofficial_url'])
def test_invalid_or_partial_vintage_is_rejected_before_import(invalid):
    fields, records = csv_fixture()
    url = URL
    if invalid == 'missing_state': records.pop(1)
    if invalid == 'duplicate': records.append(records[1].copy())
    if invalid == 'unknown_fips': records[1]['STATE'] = '99'
    if invalid == 'nonpositive': records[1]['POPESTIMATE2025'] = '0'
    if invalid == 'fractional': records[1]['POPESTIMATE2025'] = '1.5'
    if invalid == 'unknown_level': records[1]['SUMLEV'] = '050'
    if invalid == 'missing_year':
        fields.remove('POPESTIMATE2025')
        for record in records: record.pop('POPESTIMATE2025')
    if invalid == 'unofficial_url': url = 'https://census.gov.evil.example/population.csv'
    with pytest.raises(ValueError):
        parse_state_totals(encode(fields, records), url, first_year=2020, last_year=2025)


def test_intercensal_population_selects_only_all_people_total_and_never_sums_strata():
    fields = ['STATE', 'NAME', 'SEX', 'ORIGIN', 'RACE', 'AGEGRP', *[f'POPESTIMATE{year}' for year in range(2000, 2011)]]
    records = []
    for ident in sorted(CORE_STATES):
        record = {key: '0' for key in ['SEX', 'ORIGIN', 'RACE', 'AGEGRP']}
        record.update(STATE=str(int(ident)), NAME=FIPS[ident])
        record.update({f'POPESTIMATE{year}': '100000' for year in range(2000, 2011)})
        records += [record, {**record, 'SEX': '1', 'POPESTIMATE2000': '50000'}, {**record, 'AGEGRP': '1', 'POPESTIMATE2000': '10000'}]
    rows = parse_intercensal_state_totals(encode(fields, records), URL)
    assert len(rows) == 51 * 11
    assert all(row.population == 100000 for row in rows)
    assert 'US-PR' not in {row.country_code for row in rows}


class Source:
    def __init__(self, *, invalid_latest=False):
        self.calls = []
        self.invalid_latest = invalid_latest
    def fetch(self, name, url):
        self.calls.append(url)
        if url == CENSUS_DIRECTORY:
            body = ''.join(f'<a href="{period}/">{period}</a>' for period in ['2010-2019', '2010-2020', '2020-2025', '2020-2026'])
        elif url.endswith('/state/totals/'):
            last = url.split('/')[-4].split('-')[-1]
            body = f'<a href="nst-est{last}-alldata.csv">population</a>'
        else:
            first, last = map(int, url.split('/')[-4].split('-'))
            fields, records = csv_fixture(first, last, value=2_000_000 if first == 2020 else 1_000_000)
            if self.invalid_latest and last == 2026: records.pop(1)
            body = encode(fields, records)
        return SimpleNamespace(url=url, text=body, content=body.encode())


def test_latest_vintage_is_discovered_and_newer_decade_revisions_override_overlap():
    source = Source()
    rows = fetch_us(source, start_year=2010)
    assert len(rows) == 52 * 17
    assert all(row.population == 2_002_020 for row in rows if row.year == 2020)
    assert all(row.population == 1_002_019 for row in rows if row.year == 2019)
    assert all(row.year >= 2010 for row in rows)
    assert any('2020-2026/state/totals/nst-est2026-alldata.csv' in url for url in source.calls)
    assert not any('/2010-2019/' in url or '/2020-2025/' in url for url in source.calls)


def test_invalid_latest_population_cannot_silently_fall_back_to_old_vintage():
    source = Source(invalid_latest=True)
    with pytest.raises(ValueError, match='partial'):
        fetch_us(source, start_year=2010)
    assert not any('/2020-2025/' in url for url in source.calls)


def test_unpublished_new_vintage_does_not_make_up_a_new_year():
    class NotYetPublished(Source):
        def fetch(self, name, url):
            if url.endswith('/2020-2026/state/totals/'):
                self.calls.append(url)
                return SimpleNamespace(url=url, text='<a href="file-layout.pdf">not population</a>', content=b'')
            return super().fetch(name, url)
    source = NotYetPublished()
    rows = fetch_us(source, start_year=2010)
    assert max(row.year for row in rows) == 2025
    assert not any('nst-est2026-alldata.csv' in url for url in source.calls)
    assert any('nst-est2025-alldata.csv' in url for url in source.calls)


def test_transport_failure_does_not_look_like_an_unpublished_vintage():
    import requests
    class BrokenTransport(Source):
        def fetch(self, name, url):
            if url.endswith('/2020-2026/state/totals/'):
                raise requests.exceptions.SSLError('TLS failure')
            return super().fetch(name, url)
    with pytest.raises(requests.exceptions.SSLError):
        fetch_us(BrokenTransport(), start_year=2010)
