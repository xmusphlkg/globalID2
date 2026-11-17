from copy import deepcopy
from datetime import date

from scripts.refresh_site_population_rates import fill_rates, refresh_country


def entry():
    source = {'series_code': 'TEST', 'source_system': 'SRC_US_NNDSS',
              'metric_type': 'case_notifications', 'unit': 'count',
              'temporal_granularity': 'weekly', 'mapping_relation': 'exact',
              'dimension_key': 'all', 'geography_key': 'country:US-CA:national',
              'dates': ['2025-01-04'], 'values': [0], 'point_quality_statuses': ['observed']}
    return {'dates': ['2025-01-04'], 'cases': [0], 'period_granularity': 'weekly',
            'metric_type': 'case_notifications', 'selected_series_codes': ['TEST'],
            'source_series': [source], 'incidence_rates': [None]}


def population(year=2025):
    return {str(year): {'year': year, 'value': 100000, 'source': 'Census'}}


def test_refresh_retains_true_zero_and_published_rates():
    value = entry()
    assert fill_rates(value, 'US-CA', population()) == 1
    assert value['incidence_rates'] == [0]
    assert value['incidence_sources'] == ['census_computed']
    value['incidence_rates'] = [7]
    assert fill_rates(value, 'US-CA', population()) == 0
    assert value['incidence_rates'] == [7]


def test_refresh_uses_source_reporting_year_at_new_year_and_requires_own_population():
    value = entry(); value['cases'] = [10]
    # The MMWR week ends in 2025 even though it starts in 2024.
    assert fill_rates(value, 'US-CA', population(2024)) == 0
    assert fill_rates(value, 'US-CA', population(2025)) == 1
    assert value['incidence_rates'] == [10]


def test_refresh_rejects_sentinel_strata_other_geographies_and_incomplete_periods():
    for fields in [{'reporting_basis': 'sentinel_surveillance'}, {'dimension_key': 'sex:female'},
                   {'dimensions': {'age': 'under5'}}, {'geography_key': 'country:US:national'},
                   {'metric_type': 'cumulative_cases'}, {'unit': 'per_site'}]:
        value = entry(); value['source_series'][0].update(fields)
        assert fill_rates(value, 'US-CA', population()) == 0
    value = entry(); value['quality_statuses'] = ['suppressed']
    assert fill_rates(value, 'US-CA', population()) == 0
    assert fill_rates(entry(), 'US-CA', population(), as_of=date(2025, 1, 3)) == 0


def test_refresh_updates_selected_source_without_mixing_historical_population_versions():
    data = {'country_code': 'US-CA', 'generated_at': '2025-02-01', 'disease_series': {'D028': entry()}}
    before = deepcopy(data); data['recorded_at_as_of'] = '2025-02-01T00:00:00+00:00'
    assert refresh_country(data, population()) == 0
    assert data['disease_series'] == before['disease_series']
    data.pop('recorded_at_as_of')
    assert refresh_country(data, population()) == 2
    source = data['disease_series']['D028']['source_series'][0]
    assert source['incidence_rates'] == [0]
    assert source['population_values'] == [100000]
    assert refresh_country(data, population()) == 0
