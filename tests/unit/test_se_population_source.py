from copy import deepcopy
from itertools import product

import pytest

from scripts.population_sources.se import (
    EXPECTED_COUNTY_LABELS, SCB_COUNTIES, parse_population, population_query,
)


def metadata(*, ckm=False):
    choices = {
        'Region': list(SCB_COUNTIES),
        'Civilstand': ['SC'] if ckm else ['OG', 'G', 'ÄNKL', 'SK'],
        'Alder': ['TotSA'] if ckm else ['tot'],
        'Kon': ['TotSa', '1', '2'] if ckm else ['1', '2'],
        'ContentsCode': ['population', 'growth'], 'Tid': ['2024', '2025'],
    }
    labels = {
        'Region': [name + ' county' for name in EXPECTED_COUNTY_LABELS.values()],
        'Civilstand': ['total, all mariatal statuses'] if ckm else ['single', 'married', 'widowers/widows', 'divorced'],
        'Alder': ['total, all ages'],
        'Kon': ['total, all men and women', 'men', 'women'] if ckm else ['men', 'women'],
        'ContentsCode': ['Population', 'Population growth'], 'Tid': ['2024', '2025'],
    }
    return {'variables': [{'code': code, 'values': values, 'valueTexts': labels[code]} for code, values in choices.items()]}


def response(query, value=10):
    selections = {q['code']: q['selection']['values'] for q in query['query']}
    dimensions = {code: {'category': {'index': {v: i for i, v in enumerate(values)}}} for code, values in selections.items()}
    dimensions['ContentsCode']['extension'] = {'refperiod': {'population': '31 December each year'}}
    dimensions['ContentsCode']['category']['unit'] = {'population': {'base': 'number of persons', 'decimals': 0}}
    cells = list(product(*selections.values()))
    return {'class': 'dataset', 'id': list(selections), 'size': [len(v) for v in selections.values()], 'dimension': dimensions, 'value': [value] * len(cells)}


def test_old_table_sums_exactly_all_four_marital_categories_and_both_sexes_once():
    query = population_query(metadata(), end_year=2025)
    rows = parse_population(response(query), query, 'https://api.scb.se/old-table')
    assert len(rows) == 42
    assert all(row.population == 80 for row in rows)
    assert all(row.basis == 'year_end_resident_population' for row in rows)
    assert all(row.reference_date == f'{row.year}-12-31' for row in rows)


def test_new_ckm_table_uses_published_total_cell_without_summing_perturbed_components():
    query = population_query(metadata(ckm=True), end_year=2025)
    selected = {q['code']: q['selection']['values'] for q in query['query']}
    assert selected['Kon'] == ['TotSa']
    assert selected['Civilstand'] == ['SC']
    assert selected['Alder'] == ['TotSA']
    rows = parse_population(response(query, 100), query, 'https://api.scb.se/ckm-table')
    assert len(rows) == 42 and all(row.population == 100 for row in rows)
    assert all(row.basis == 'year_end_resident_population_ckm' for row in rows)


@pytest.mark.parametrize('mutation', ['missing_cell', 'missing_sex', 'wrong_unit', 'wrong_reference', 'extra_category'])
def test_scb_rejects_partial_demographic_cells_or_wrong_units(mutation):
    query = population_query(metadata(), end_year=2025)
    data = response(query)
    if mutation == 'missing_cell': data['value'][0] = None
    elif mutation == 'missing_sex': data['dimension']['Kon']['category']['index'] = {'1': 0}
    elif mutation == 'wrong_unit': data['dimension']['ContentsCode']['category']['unit']['population']['base'] = 'percent'
    elif mutation == 'wrong_reference': data['dimension']['ContentsCode']['extension']['refperiod']['population'] = '1 November each year'
    else: data['dimension']['Civilstand']['category']['index']['total'] = 4
    with pytest.raises(ValueError): parse_population(data, query, 'https://api.scb.se/table')


def test_scb_rejects_changed_county_registry_or_non_exhaustive_old_categories():
    for code in ['Region', 'Civilstand']:
        meta = metadata()
        variable = next(v for v in meta['variables'] if v['code'] == code)
        variable['values'].pop(); variable['valueTexts'].pop()
        with pytest.raises(ValueError): population_query(meta, end_year=2025)


def test_scb_rejects_ambiguous_category_index_ordering():
    query = population_query(metadata(), end_year=2025)
    data = response(query)
    data['dimension']['Region']['category']['index']['03'] = 0
    with pytest.raises(ValueError, match='indices'):
        parse_population(data, query, 'https://api.scb.se/table')


def test_scb_catalogue_new_years_are_automatically_requested_and_old_only_is_rejected(monkeypatch):
    from scripts.population_sources import se
    from types import SimpleNamespace
    class Today:
        @staticmethod
        def today():
            from datetime import date
            return date(2027, 11, 16)
    monkeypatch.setattr(se, 'date', Today)
    class Source:
        def __init__(self, *, historical_only=False, stale_metadata=False):
            self.historical_only, self.stale_metadata = historical_only, stale_metadata
            self.requested = []
        def fetch(self, name, url, body=None):
            if name == 'scb_population_catalogue.json':
                tables = [{'id': 'BefolkningNy', 'type': 't', 'text': 'Population by region, marital status, age and sex. Year 1968 - 2024', 'updated': '2025'}]
                if not self.historical_only:
                    tables.append({'id': 'BefolkningCKM', 'type': 't', 'text': 'Population by region, marital status, age and sex. Year 2025 - 2026', 'updated': '2027'})
                return SimpleNamespace(json=lambda: tables)
            if body is not None:
                self.requested.append(body)
                return SimpleNamespace(json=lambda: response(body, 100 if 'CKM' in url else 10))
            meta = metadata(ckm='CKM' in url)
            years = ['2025'] if self.stale_metadata else ['2025', '2026']
            for variable in meta['variables']:
                if variable['code'] == 'Tid':
                    variable['values'] = years if 'CKM' in url else ['2024']
                    variable['valueTexts'] = variable['values']
            return SimpleNamespace(json=lambda: meta)
    source = Source()
    rows = se.fetch_se(source)
    assert len(rows) == 63 and {r.year for r in rows} == {2024, 2025, 2026}
    assert {r.population for r in rows if r.year == 2026} == {100}
    with pytest.raises(ValueError, match='historical-only'):
        se.fetch_se(Source(historical_only=True))
    with pytest.raises(ValueError, match='catalogue latest'):
        se.fetch_se(Source(stale_metadata=True))
