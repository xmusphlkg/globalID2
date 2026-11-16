from __future__ import annotations

from copy import deepcopy

import pytest

from src.data.crawlers.cn_provinces import MONTHLY_REPORT_SOURCE_ID
from src.data.processors.cn_provinces import expand_province_metric_rows
from src.data.storage.series_observation_store import SeriesObservationStore
from src.generation.site_series_projection import apply_series_first_projection
from src.ontology import load_disease_ontology
from src.services.disease_ontology_sync_service import build_disease_ontology_sync_payload


def raw_row(**overrides):
    return {
        'Date': '2026-09-01', 'SourceID': MONTHLY_REPORT_SOURCE_ID,
        'SourceDiseaseCode': 'tuberculosis', 'RawDiseaseLabel': '肺结核',
        'DefinitionVersion': 'CN_PROVINCE_MONTHLY_REPORT_V1',
        'Cases': 905, 'GeographyKey': 'country:CN-GS:national',
        'JurisdictionCode': 'CN-GS', 'Raw': {'source_row': {'病种': '肺结核', '发病数': '905', '死亡数': '1'}},
        **overrides,
    }


def test_explicit_deaths_resolve_independently_and_missing_never_becomes_zero():
    store = SeriesObservationStore()
    source = raw_row(Deaths=1)
    expanded = expand_province_metric_rows([source, raw_row(Date='2026-08-01')])
    assert len(expanded) == 3
    built = store.build_observations(expanded, 'CN', source_id=MONTHLY_REPORT_SOURCE_ID)
    assert not (built.skipped_invalid or built.skipped_ambiguous or built.skipped_unmatched)
    assert [(r['series_code'], r['value']) for r in built.observations] == [
        ('SER_CN_PROV_REPORT_TUBERCULOSIS', 905),
        ('SER_CN_PROV_REPORT_DEATHS_TUBERCULOSIS', 1),
        ('SER_CN_PROV_REPORT_TUBERCULOSIS', 905),
    ]
    assert expanded[1]['Raw']['source_row']['发病数'] == '905'
    assert source['SourceDiseaseCode'] == 'tuberculosis'
    zero = expand_province_metric_rows([raw_row(Deaths=0)])
    assert zero[1]['Cases'] == 0


@pytest.mark.parametrize('value', [-1, 0.5, True, '1'])
def test_invalid_death_count_is_rejected(value):
    with pytest.raises(ValueError, match='non-negative integer'):
        expand_province_metric_rows([raw_row(Deaths=value)])


def test_death_registry_retains_definition_boundary_and_pairing():
    ontology = load_disease_ontology()
    death = ontology.series_lookup(series_id='SER_CN_PROV_REPORT_DEATHS_SFTS')
    assert death['measure'] == 'deaths'
    assert death['valid_from'] == '2026-04-01'
    assert death['paired_case_series_code'] == 'SER_CN_PROV_REPORT_SFTS'
    payload = build_disease_ontology_sync_payload(ontology)
    row = next(r for r in payload.surveillance_series if r['series_code'] == death['id'])
    assert row['metadata']['paired_case_series_code'] == 'SER_CN_PROV_REPORT_SFTS'


def source_record(series='SER_CN_PROV_REPORT_TUBERCULOSIS', value=905, **extra):
    return {
        'disease_id': 'D025', 'date': '2026-09-01', 'year_month': '2026-09',
        'cases': value, 'series_code': series, 'source_system': MONTHLY_REPORT_SOURCE_ID,
        'metric_type': 'case_notifications', 'series_unit': 'count', 'observation_unit': 'count',
        'mapping_relation': 'exact', 'dimension_key': 'all', 'dimensions': {},
        'geography_key': 'country:CN-GS:national', 'definition_version': 'CN_PROVINCE_MONTHLY_REPORT_V1',
        'temporal_granularity': 'monthly', 'reporting_basis': 'provincial_statutory_infectious_disease_monthly_report',
        'time_basis': 'publisher reporting month', 'aggregation_policy': 'non_additive',
        **extra,
    }


def death_record(**extra):
    return source_record('SER_CN_PROV_REPORT_DEATHS_TUBERCULOSIS', 1,
                         metric_type='deaths', paired_case_series_code='SER_CN_PROV_REPORT_TUBERCULOSIS', **extra)


def test_public_projection_pairs_deaths_without_adding_to_cases_or_filling_absent_months():
    rows = apply_series_first_projection([], [
        source_record(), death_record(), source_record(date='2026-10-01', year_month='2026-10'),
    ])
    assert [(r['cases'], r['deaths']) for r in rows] == [(905, 1), (905, None)]
    assert rows[0]['_series_context']['metric_layers']['deaths'] == 'series_registry'
    assert len(rows[0]['_series_context']['source_series']) == 2


@pytest.mark.parametrize('field,value', [
    ('source_system', 'SRC_CN_PROV_DATACENTER'), ('geography_key', 'country:CN-BJ:national'),
    ('definition_version', 'different'), ('temporal_granularity', 'annual'),
    ('time_basis', 'onset month'), ('paired_case_series_code', 'SER_OTHER'),
])
def test_unmatched_death_definition_never_attaches(field, value):
    death = death_record()
    death[field] = value
    rows = apply_series_first_projection([], [source_record(), death])
    assert rows[0]['cases'] == 905
    assert rows[0]['deaths'] is None
