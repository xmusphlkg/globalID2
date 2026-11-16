from copy import deepcopy

import pytest

from scripts.verify_unpublished_surveillance import check_projected_values
from src.data.crawlers.my_who_annual import INDICATORS
from src.generation.site_series_projection import apply_series_first_projection
from src.services.surveillance_onboarding_review import collection_country, reviewed_public_record


def who_record(year=2025):
    return {
        'source_system': 'SRC_KE_WHO_GHO', 'series_code': 'HOLD_KE_MEASLES',
        'reviewed_source_code': 'WHS3_62', 'reviewed_source_label': INDICATORS['WHS3_62'][0],
        'source_label': INDICATORS['WHS3_62'][0],
        'definition_version': 'who_gho_country_reported_annual_v1',
        'disease_id': 'D017', 'mapping_relation': 'exact', 'comparability': 'conditional',
        'aggregation_policy': 'non_additive', 'projection_policy': 'canonical',
        'metric_type': 'case_notifications', 'reporting_basis': 'who_country_reported_notifications',
        'temporal_granularity': 'annual', 'geography_key': 'country:KE:national',
        'dimension_key': 'sha256:indicator', 'dimensions': {'who_indicator': 'WHS3_62'},
        'series_unit': 'count', 'observation_unit': 'count', 'date': f'{year}-01-01',
        'cases': 42, 'deaths': None, 'recoveries': None,
    }


def test_reviewed_who_identity_dimensions_produce_original_annual_values():
    originals = [who_record(2024), who_record(2025)]
    rows = [reviewed_public_record(row, 'KE') for row in originals]
    assert originals[0]['dimension_key'] == 'sha256:indicator'
    projected = apply_series_first_projection([], rows)
    assert [(r['date'], r['cases'], r['deaths']) for r in projected] == [
        ('2024-01-01', 42, None), ('2025-01-01', 42, None),
    ]
    detail = projected[0]['_series_context']['source_series'][0]
    assert detail['point_source_dimensions'] == [{'who_indicator': 'WHS3_62'}] * 2
    check_projected_values(projected, rows)


@pytest.mark.parametrize('change', [
    {'dimensions': {'who_indicator': 'WHS3_62', 'sex': 'female'}},
    {'geography_key': 'country:UG:national'},
    {'reviewed_source_code': 'unreviewed'},
    {'disease_id': 'D025'},
    {'temporal_granularity': 'weekly'},
    {'metric_type': 'incidence_rate'},
    {'definition_version': 'changed-definition'},
    {'comparability': 'direct'},
])
def test_unreviewed_or_changed_who_semantics_cannot_enter_public_curves(change):
    record = who_record()
    record.update(change)
    assert reviewed_public_record(record, 'KE') is None


def test_verified_source_metadata_changes_do_not_split_one_total_history():
    rows = [who_record(2024), who_record(2025)]
    for index, row in enumerate(rows):
        row['projection_dimension_key'] = 'all'
        row['dimensions']['report_page'] = index + 1
        row['dimension_key'] += str(index)
    before = deepcopy(rows)
    projected = apply_series_first_projection([], rows)
    assert len(projected) == 2
    assert rows == before
    details = projected[0]['_series_context']['source_series']
    assert len(details) == 1
    assert details[0]['point_source_dimensions'] == [row['dimensions'] for row in rows]


def test_bq_keeps_own_geography_while_reading_dutch_source():
    assert collection_country('BQ') == 'NL'
    assert collection_country('US-PR') == 'US'


def test_acceptance_rejects_a_changed_source_value():
    row = who_record()
    with pytest.raises(ValueError, match='original source cell'):
        check_projected_values([{**row, 'cases': 43}], [row])
