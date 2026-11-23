"""Current FHI table contracts, unknown cells, overlaps and offline equivalence."""
import itertools
import json
from datetime import date

import pytest
import requests

from src.data.crawlers.no import NOContractError
from src.data.crawlers.no_statistikk import (
    MONTH_DIMENSION,
    TOTALS,
    NorwayStatisticsCrawler,
    parse_case_cube,
)
from src.data.processors.no import NOMonthlyUpdater
from src.services.ingestion.countries import no
from tests.contract.test_no_observations import Writer


def request_body(months=('2026-09', '2026-10')):
    choices = {'diagnose': ['Difteri', 'Kikhoste'], MONTH_DIMENSION: list(months),
               **{k: [v] for k, v in TOTALS.items()}}
    return {'response': {'format': 'json-stat2'}, 'dimensions': [
        {'code': k, 'filter': 'item', 'values': v} for k, v in choices.items()]}


def cube(body):
    # The response may order dimensions differently from the request.
    dimensions = list(reversed(body['dimensions']))
    ids = [d['code'] for d in dimensions]
    values = [d['values'] for d in dimensions]
    return {'class': 'dataset', 'version': '2.0', 'id': ids,
            'size': [len(v) for v in values],
            'dimension': {d['code']: {'category': {'index': d['values']}}
                          for d in dimensions},
            'value': [0 if dict(zip(ids, coords))['diagnose'] == 'Difteri' else 12
                      for coords in itertools.product(*values)], 'status': ''}


def metadata(table_id, names):
    choices = {'diagnose': names, MONTH_DIMENSION: ['2026-09', '2026-10', '2026-11', '2026-12'],
               **{k: [v] for k, v in TOTALS.items()}}
    return {'tableId': table_id, 'dimensions': {'dimensions': [
        {'code': k, 'categories': [{'value': v, 'label': v, 'children': []} for v in values]}
        for k, values in choices.items()]}}


def install(monkeypatch, *, conflict=False):
    posted = []

    def response(url, payload):
        r = requests.Response()
        r.status_code, r.url = 200, url
        r.headers['Content-Type'] = 'application/json'
        r._content = json.dumps(payload, ensure_ascii=False).encode()
        r._content_consumed = True
        return r

    def get(session, url, **kwargs):
        if url.endswith('/default'):
            payload = {'identifier': 'default-id'}
        elif '/menu-structure/' in url:
            payload = {'menu': [{'name': 'Case group', 'children': [
                {'name': 'Antall tilfeller per måned', 'routerLink': '/msis/group-a'},
                {'name': 'Antall tilfeller per måned', 'routerLink': '/msis/group-b'},
                {'name': 'Antall isolater per måned', 'routerLink': '/msis/not-cases'},
            ]}]}
        elif url.endswith('/group-a'):
            payload = metadata(10, ['Difteri', 'Kikhoste', 'Tuberkulose - forebyggende behandling'])
        elif url.endswith('/group-b'):
            payload = metadata(11, ['Kikhoste'])
        else:
            pytest.fail(f'unexpected metadata request: {url}')
        return response(url, payload)

    def post(session, url, **kwargs):
        body = kwargs['json']
        posted.append(body)
        assert all(d['filter'] == 'item' for d in body['dimensions'])
        assert 'Tuberkulose - forebyggende behandling' not in body['dimensions'][0]['values']
        payload = cube(body)
        if conflict and '/11/' in url:
            payload['value'] = [13] * len(payload['value'])
        return response(url, payload)

    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(requests.Session, 'post', post)
    return posted


def test_jsonstat_respects_dimension_order_and_explicit_zero():
    body = request_body()
    values, missing = parse_case_cube(cube(body), body)
    assert missing == []
    assert sorted(values) == [('Difteri', '2026-09', 0), ('Difteri', '2026-10', 0),
                              ('Kikhoste', '2026-09', 12), ('Kikhoste', '2026-10', 12)]


def test_missing_sparse_and_suppressed_values_are_not_zero():
    body = request_body()
    payload = cube(body)
    payload['value'] = {'0': 0, '1': 99, '3': None}
    payload['status'] = {'1': ':'}
    values, missing = parse_case_cube(payload, body)
    assert len(values) == 1 and values[0][2] == 0
    assert len(missing) == 3
    assert {m['status'] for m in missing} == {':', 'missing'}


@pytest.mark.parametrize('change', ['geography', 'measure', 'month', 'negative', 'bool', 'dimensions'])
def test_cube_rejects_changed_grain_and_invalid_counts(change):
    body = request_body()
    payload = cube(body)
    if change in ('geography', 'measure', 'month'):
        code, replacement = {'geography': ('bostedsfylkenrmappet', '03'),
            'measure': ('MEASURE_TYPE', 'rate'), 'month': (MONTH_DIMENSION, '2027-01')}[change]
        payload['dimension'][code]['category']['index'][0] = replacement
        # Cube construction shares lists; the expected request is independent.
        body = request_body()
    elif change == 'negative':
        payload['value'][0] = -1
    elif change == 'bool':
        payload['value'][0] = False
    else:
        payload['id'].append('unrequested_age')
    with pytest.raises(NOContractError):
        parse_case_cube(payload, body)


async def test_current_portal_collect_organize_roundtrip_has_no_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    posted = install(monkeypatch)
    updater = NOMonthlyUpdater(output_csv=tmp_path/'source.csv', today_provider=lambda: date(2026, 11, 23))
    saved = await no.collect(Writer(), source='fhi_msis', updater=updater,
                             months=[(2026, 9), (2026, 10), (2026, 11), (2026, 12)])
    assert len(posted) == 2
    assert all(d['values'] == ['2026-09', '2026-10'] for body in posted
               for d in body['dimensions'] if d['code'] == MONTH_DIMENSION)
    monkeypatch.setattr(requests.Session, 'get', lambda *a, **k: pytest.fail('offline GET escaped'))
    monkeypatch.setattr(requests.Session, 'post', lambda *a, **k: pytest.fail('offline POST escaped'))
    result = no.organize(saved, updater=updater, source='fhi_msis')
    assert len(result.rows) == 4
    assert result.source_audit['duplicate_rows'] == 2
    assert result.source_audit['status'] == 'validated'
    assert all(row['SourceContract'].startswith('fhi-msis-statistikk-') for row in result.rows)
    assert any(row['Cases'] == '0' for row in result.rows)


def test_overlap_conflict_fails_closed(tmp_path, monkeypatch):
    install(monkeypatch, conflict=True)
    crawler = NorwayStatisticsCrawler(delay=0)
    try:
        with pytest.raises(NOContractError, match='overlapping'):
            crawler.crawl_monthly_national(tmp_path/'out.csv', months=[(2026, 10)], as_of=date(2026, 11, 23))
        assert not (tmp_path/'out.csv').exists()
    finally:
        crawler.session.close()


def test_unpublished_requested_month_cannot_be_validated(tmp_path, monkeypatch):
    install(monkeypatch)
    crawler = NorwayStatisticsCrawler(delay=0)
    try:
        with pytest.raises(ValueError, match='no normalized monthly rows'):
            crawler.crawl_monthly_national(tmp_path/'out.csv', months=[(2025, 10)], as_of=date(2026, 11, 23))
    finally:
        crawler.session.close()


def test_missing_table_month_is_partial_even_if_another_table_covers_it(monkeypatch):
    install(monkeypatch)
    crawler = NorwayStatisticsCrawler(delay=0)
    try:
        diagnoses = crawler.fetch_diagnoses()
        crawler._tables[1]['categories'][MONTH_DIMENSION].pop('2026-10')
        rows = crawler.fetch_year_rows(2026, diagnoses=diagnoses, target_months={(2026, 10)},
                                       as_of=date(2026, 11, 23), include_current_month=False)
        assert len(rows) == 2
        assert crawler.source_audit['status'] == 'partial'
        assert crawler.source_audit['unavailable_table_months'] == [
            {'table_id': 11, 'months': ['2026-10'], 'diagnoses': ['Kikhoste']}]
    finally:
        crawler.session.close()


def test_documented_archived_period_is_audited_without_inventing_values(monkeypatch):
    install(monkeypatch)
    crawler = NorwayStatisticsCrawler(delay=0)
    try:
        diagnoses = crawler.fetch_diagnoses()
        table = crawler._tables[1]
        table['title'] = 'Antall tilfeller per måned (2009-2012)'
        table['categories'][MONTH_DIMENSION] = {'2012-12': '2012-12'}
        crawler.fetch_year_rows(2026, diagnoses=diagnoses, target_months={(2026, 10)},
                                as_of=date(2026, 11, 23), include_current_month=False)
        assert crawler.source_audit['status'] == 'validated'
        assert crawler.source_audit['archived_table_months_excluded'] == [
            {'table_id': 11, 'months': ['2026-10'], 'diagnoses': ['Kikhoste']}]
    finally:
        crawler.session.close()


def test_invalid_live_window_is_not_used_after_postparse_validation(tmp_path, monkeypatch):
    from src.data.crawlers.no import NOFetchSummary

    class IncompleteCrawler(NorwayStatisticsCrawler):
        def crawl_monthly_national(self, output_csv, **kwargs):
            # A current month omitted as all-zero leaves only the older month.
            self._write_rows(output_csv, [{'Date': '2026-10-01', 'RawDiseaseLabel': 'Difteri',
                'DiseaseCode': 'Difteri', 'Cases': '1', 'DataStatus': 'closed'}])
            return NOFetchSummary(1, date(2026, 10, 1), 1, 1, self.portal_url)

    updater = NOMonthlyUpdater(output_csv=tmp_path/'source.csv', crawler_type=IncompleteCrawler,
        today_provider=lambda: date(2026, 11, 23), include_current_month=True)
    with pytest.raises(ValueError, match='missing requested.*2026-11'):
        updater.refresh_source(months=[(2026, 10), (2026, 11)])


def test_modern_save_raw_records_post_body_and_contract(tmp_path, monkeypatch):
    install(monkeypatch)
    crawler = NorwayStatisticsCrawler(delay=0, save_raw=True, raw_dir=tmp_path/'raw')
    try:
        crawler.crawl_monthly_national(tmp_path/'out.csv', months=[(2026, 10)], as_of=date(2026, 11, 23))
        artifacts = list((tmp_path/'raw'/'monthly'/'2026').glob('*.json'))
        assert len(artifacts) == 2
        archived = json.loads(artifacts[0].read_text())
        assert archived['contract_version'].startswith('fhi-msis-statistikk-')
        assert archived['request']['method'] == 'POST'
        assert archived['request']['json']['response'] == {'format': 'json-stat2'}
    finally:
        crawler.session.close()
