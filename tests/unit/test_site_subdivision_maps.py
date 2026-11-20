import copy
import json

import pytest

from src.generation.site_subdivision_maps import (
    BOUNDARIES, build_map_payloads, monthly_series, normalise_population,
    parent_code, write_subdivision_maps,
)


def population(code="AU-NSW", year=2024, value=8_492_050):
    return {"country_code": code, "year": year, "population": value, "source": "ABS", "metadata": {"location_code": code, "reference_date": f"{year}-06-30", "population_basis": "june_estimated_resident_population", "source_url": "https://www.abs.gov.au/official-population.xlsx"}}


def monthly(values=None):
    return {"name_en": "Pertussis", "name_zh": "百日咳", "period_granularity": "monthly", "dates": ["2024-01-01", "2024-02-01"], "cases": values or [0, 100], "deaths": [900, 900], "selected_series_codes": ["SER_TEST"], "point_series_codes": ["SER_TEST"], "projection_policy": "single_series", "source_series": [{"series_code": "SER_TEST", "dimension_key": "all", "dimensions": {}, "aggregation_policy": "non_additive", "mapping_relation": "exact", "metric_type": "case_notifications", "temporal_granularity": "monthly", "unit": "count"}], "definition_version": "same-definition"}


def test_population_is_local_official_exact_year_with_traceable_semantics():
    row = population()
    result = normalise_population(row)
    assert result["value"] == 8_492_050
    assert result["reference_date"] == "2024-06-30"
    assert len(result["version"]) == 64
    for patch in [
        {"country_code": "AU"}, {"source": "WPP"}, {"population": 0},
        {"metadata": {**row["metadata"], "reference_date": "2023-06-30"}},
        {"metadata": {**row["metadata"], "source_url": "https://example.org/abs"}},
        {"metadata": {**row["metadata"], "location_code": "AU-VIC"}},
        {"metadata": {**row["metadata"], "population_basis": None}},
    ]:
        assert normalise_population({**row, **patch}) is None


def test_registry_recovers_real_parent_but_does_not_guess_unknown_codes():
    assert parent_code({"code": "CN-GD"}) == "CN"
    assert parent_code({"code": "AU-NSW"}) == "AU"
    assert parent_code({"code": "ZZ-UNREGISTERED"}) is None


def test_monthly_numerator_preserves_zero_null_and_suppression_and_ignores_deaths():
    assert monthly_series(monthly(), "AU")["points"] == [["2024-01-01", 0, "observed"], ["2024-02-01", 100, "observed"]]
    result = monthly_series(monthly([None, 5]), "AU")
    assert result["points"][0] == ["2024-01-01", None, "missing_or_suppressed"]
    entry = monthly([1, 5]); entry["quality_statuses"] = ["suppressed", "provisional"]
    assert monthly_series(entry, "CN")["points"] == [["2024-01-01", None, "suppressed"], ["2024-02-01", 5, "provisional"]]


@pytest.mark.parametrize("patch", [
    {"period_granularity": "weekly"}, {"metric_type": "deaths"},
    {"metric_type": "cumulative_cases"}, {"metric_type": "sentinel_case_notifications"},
    {"dates": ["2024-01-01", "2024-01-01"]}, {"dates": ["2024-01-02", "2024-02-01"]},
    {"cases": [1, -1]}, {"cases": [1, .2]},
    {"loss_risk": "non_additive_series_not_rolled_up"},
    {"selected_series_codes": ["SER_TEST"], "source_series": [{"series_code": "SER_TEST", "dimension_key": "age:child"}]},
])
def test_non_comparable_or_non_interval_counts_are_not_admitted(patch):
    assert monthly_series({**monthly(), **patch}, "AU") is None


def test_cn_projection_allows_resolved_non_additive_sources_and_raw_points():
    entry = monthly()
    entry.update({
        "projection_policy": "priority_overlay",
        "selected_series_codes": ["SER_HIGH", "SER_LOW"],
        "point_series_codes": ["SER_HIGH", "SER_LOW"],
        "quality_statuses": ["raw", "raw"],
        "source_series": [
            {"series_code": "SER_HIGH", "dimension_key": "all", "dimensions": {},
             "aggregation_policy": "non_additive", "projection_policy": "priority_overlay",
             "projection_priority": 200, "comparability_set": "CN_TEST",
             "mapping_relation": "exact", "metric_type": "case_notifications",
             "temporal_granularity": "monthly", "unit": "count"},
            {"series_code": "SER_LOW", "dimension_key": "all", "dimensions": {},
             "aggregation_policy": "non_additive", "projection_policy": "priority_overlay",
             "projection_priority": 100, "comparability_set": "CN_TEST",
             "mapping_relation": "exact", "metric_type": "case_notifications",
             "temporal_granularity": "monthly", "unit": "count"},
        ],
    })
    result = monthly_series(entry, "CN")
    assert result["points"] == [["2024-01-01", 0, "raw"], ["2024-02-01", 100, "raw"]]


def test_cn_map_selects_one_source_system_and_uses_its_values():
    def source(system, code, values, basis, time_basis):
        return {"series_code": code, "source_system": system,
                "definition_version": "CN_PROVINCE_DATACENTER_ONSET_V1" if system.endswith("DATACENTER") else "CN_PROVINCE_MONTHLY_REPORT_V1",
                "reporting_basis": basis, "time_basis": time_basis,
                "metric_type": "case_notifications", "unit": "count", "temporal_granularity": "monthly",
                "mapping_relation": "exact", "comparability": "conditional", "comparability_break": None,
                "series_is_active": True, "geography_key": "country:CN-AH:national", "dimension_key": "all", "dimensions": {},
                "dates": ["2021-01-01", "2021-02-01"], "values": values, "point_quality_statuses": ["raw", "raw"]}
    dc = source("SRC_CN_PROV_DATACENTER", "DC", [1461, 837], "national_notifiable_disease_surveillance_by_onset", "month of onset")
    monthly_report = source("SRC_CN_PROV_MONTHLY_REPORT", "MR", [1482, 860], "provincial_statutory_infectious_disease_monthly_report", "publisher reporting month")
    entry = {"name_en": "Influenza", "name_zh": "流感", "period_granularity": "monthly",
             "source_series": [dc, monthly_report], "data_layer": "series_registry"}
    ah = {"country_code": "CN-AH", "country_name_en": "Anhui", "disease_series": {"D038": entry}}
    boundary = {"codes": ["CN-AH"], "expected_regions": 1}
    _, _, payloads = build_map_payloads("CN", {"CN-AH": ah}, [], boundary)
    result = payloads["D038"]["regions"]["CN-AH"]
    assert result["source_system"] == "SRC_CN_PROV_DATACENTER"
    assert result["points"][0] == ["2021-01-01", 1461, "raw"]


def test_cn_non_additive_sources_without_projection_resolution_are_rejected():
    entry = monthly()
    entry.update({
        "projection_policy": "representative_series",
        "selected_series_codes": ["SER_TEST", "SER_OTHER"],
        "point_series_codes": ["SER_TEST", "SER_OTHER"],
        "source_series": [
            {"series_code": "SER_TEST", "dimension_key": "all", "dimensions": {}, "aggregation_policy": "non_additive"},
            {"series_code": "SER_OTHER", "dimension_key": "all", "dimensions": {}, "aggregation_policy": "non_additive"},
        ],
    })
    assert monthly_series(entry, "CN") is None


def test_legacy_fallback_curves_are_not_admitted_to_subnational_maps():
    entry = monthly()
    entry["data_layer"] = "legacy_fallback"
    assert monthly_series(entry, "AU") is None


def test_map_does_not_mix_national_counts_or_other_year_population():
    boundary = json.loads((BOUNDARIES / "manifest.json").read_text())["countries"]["AU"]
    snapshots = {"AU-NSW": {"country_code": "AU-NSW", "disease_series": {"D028": monthly()}}, "AU": {"country_code": "AU", "disease_series": {"D028": monthly([999999, 999999])}}}
    catalog, populations, diseases = build_map_payloads("AU", snapshots, [population(year=2023)], boundary)
    assert [row["code"] for row in catalog["regions"]] == ["AU-NSW"]
    assert set(diseases["D028"]["regions"]) == {"AU-NSW"}
    assert "2024" not in populations["regions"]["AU-NSW"]
    assert diseases["D028"]["regions"]["AU-NSW"]["points"][1][1] == 100
    with pytest.raises(ValueError, match="Duplicate population"):
        build_map_payloads("AU", snapshots, [population(), population()], boundary)


def test_map_export_updates_population_revisions_retires_regions_and_disables_historical_rates(tmp_path):
    state, public = tmp_path / "state", tmp_path / "public"
    (state / "countries").mkdir(parents=True)
    (state / "countries/au-nsw.json").write_text(json.dumps({"country_code": "AU-NSW", "disease_series": {"D028": monthly()}}))
    write_subdivision_maps(state, public, [population()])
    first = json.loads((public / "subdivision-maps/au/population.json").read_text())
    write_subdivision_maps(state, public, [population(value=8_500_000)])
    second = json.loads((public / "subdivision-maps/au/population.json").read_text())
    assert first["regions"]["AU-NSW"]["2024"]["version"] != second["regions"]["AU-NSW"]["2024"]["version"]
    write_subdivision_maps(state, public, [population()], recorded_at_as_of="2024-12-31T00:00:00Z")
    assert json.loads((public / "subdivision-maps/au/population.json").read_text())["regions"] == {}
    write_subdivision_maps(state, public, [population()], active_codes=set())
    assert json.loads((public / "subdivision-maps/index.json").read_text())["countries"] == []
    assert not (public / "subdivision-maps/au/d028.json").exists()


def test_incremental_map_export_preserves_unchanged_cn_diseases_and_regions(tmp_path):
    state, public = tmp_path / "state", tmp_path / "public"
    countries = state / "countries"
    countries.mkdir(parents=True)
    ah = {"country_code": "CN-AH", "country_name_en": "Anhui", "disease_series": {"D001": monthly(), "D002": monthly([2, 4])}}
    bj = {"country_code": "CN-BJ", "country_name_en": "Beijing", "disease_series": {"D001": monthly(), "D002": monthly([3, 5])}}
    (countries / "cn-ah.json").write_text(json.dumps(ah))
    (countries / "cn-bj.json").write_text(json.dumps(bj))
    write_subdivision_maps(state, public, [], active_codes={"CN-AH", "CN-BJ"})
    initial = json.loads((public / "subdivision-maps/cn/index.json").read_text())
    assert {item["id"] for item in initial["diseases"]} == {"D001", "D002"}

    # A scoped release contains only the changed province and one changed
    # disease. Existing map payloads supply the omitted province and disease.
    (countries / "cn-bj.json").unlink()
    changed = {"country_code": "CN-AH", "country_name_en": "Anhui", "disease_series": {"D001": monthly([9, 11])}}
    (countries / "cn-ah.json").write_text(json.dumps(changed))
    write_subdivision_maps(
        state,
        public,
        [],
        active_codes={"CN-AH", "CN-BJ"},
        preserve_existing=True,
    )
    latest = json.loads((public / "subdivision-maps/cn/index.json").read_text())
    assert {item["id"] for item in latest["diseases"]} == {"D001", "D002"}
    d001 = json.loads((public / "subdivision-maps/cn/d001.json").read_text())
    d002 = json.loads((public / "subdivision-maps/cn/d002.json").read_text())
    assert d001["regions"]["CN-AH"]["points"][1][1] == 11
    assert d001["regions"]["CN-BJ"]["points"][1][1] == 100
    assert d002["regions"]["CN-BJ"]["points"][1][1] == 5


def test_case_definition_changes_have_different_comparison_keys():
    a, b = monthly(), monthly()
    b["definition_version"] = "different-definition"
    assert monthly_series(a, "AU")["comparison_key"] != monthly_series(b, "AU")["comparison_key"]


def test_open_reporting_month_is_not_accepted_as_complete():
    from datetime import date
    result = monthly_series(monthly(), 'AU', as_of=date(2024, 2, 15))
    assert result['points'][0][1] == 0
    assert result['points'][1] == ['2024-02-01', None, 'incomplete']


def test_dynamic_release_pins_revised_population_and_case_shards_together(tmp_path):
    from src.generation import surveillance_data_export as dynamic
    state, public = tmp_path / 'state', tmp_path / 'public'
    series = monthly()
    context = {
        'generated_at': '2024-04-01T00:00:00Z', 'countries_simple': [{'code': 'AU-NSW', 'data_available': True}],
        'country_exports': [{'code': 'AU-NSW', 'country_data': {'country_code': 'AU-NSW', 'generated_at': '2024-04-01T00:00:00Z', 'disease_series': {'D028': series}}, 'site_data': {'meta': {'cc': 'AU-NSW'}, 'series': []}}],
        'disease_exports': [], 'diseases': [], 'map_population_rows': [population()], 'retired_country_codes': [],
    }
    logical = dynamic.write_surveillance_artifacts(context, state, public)
    first = dynamic.freeze_public_generation(public, state, logical, release_id='before', generated_at=context['generated_at'])
    keys = ['subdivision-maps/au/population.json', 'subdivision-maps/au/d028.json', 'subdivision-maps/au/boundary.json']
    assert all(key in first['files'] for key in keys)
    frozen = {key: (public / first['files'][key]['url']).read_bytes() for key in keys}
    context['map_population_rows'] = [population(value=9_000_000)]
    series['cases'][1] = 200
    logical = dynamic.write_surveillance_artifacts(context, state, public)
    second = dynamic.freeze_public_generation(public, state, logical, release_id='after', generated_at=context['generated_at'])
    assert second['files'][keys[0]]['sha256'] != first['files'][keys[0]]['sha256']
    assert second['files'][keys[1]]['sha256'] != first['files'][keys[1]]['sha256']
    assert second['files'][keys[2]]['sha256'] == first['files'][keys[2]]['sha256']
    assert all((public / first['files'][key]['url']).read_bytes() == body for key, body in frozen.items())


def direct_source(country, *, cadence='weekly', metric='case_notifications'):
    system = {'US': 'SRC_US_NNDSS', 'JP': 'SRC_JP_NIID', 'SE': 'SRC_SE_FOHM_SMINET'}[country]
    return {'series_code': 'REVIEWED', 'source_system': system, 'metric_type': metric,
            'temporal_granularity': cadence, 'unit': 'count', 'mapping_relation': 'exact',
            'dimension_key': 'all', 'dimensions': {}, 'aggregation_policy': 'direct_only',
            'reporting_basis': 'notification'}


def direct_series(country, dates, *, cadence='weekly'):
    return {'period_granularity': cadence, 'metric_type': 'case_notifications',
            'dates': dates, 'cases': list(range(len(dates))),
            'quality_statuses': ['provisional'] * len(dates),
            'selected_series_codes': ['REVIEWED'], 'source_series': [direct_source(country, cadence=cadence)]}


def test_week_calendars_keep_true_source_periods_and_reporting_years():
    from src.generation.site_subdivision_maps import reported_series
    us = reported_series(direct_series('US', ['2022-01-01', '2022-01-08']), 'US')
    assert us['periods'] == [
        {'start': '2021-12-26', 'end_exclusive': '2022-01-02', 'reporting_year': 2021},
        {'start': '2022-01-02', 'end_exclusive': '2022-01-09', 'reporting_year': 2022},
    ]
    jp = reported_series(direct_series('JP', ['2021-01-03', '2021-01-10']), 'JP')
    assert jp['periods'] == [
        {'start': '2020-12-28', 'end_exclusive': '2021-01-04', 'reporting_year': 2020},
        {'start': '2021-01-04', 'end_exclusive': '2021-01-11', 'reporting_year': 2021},
    ]
    assert reported_series(direct_series('JP', ['2021-01-02']), 'JP') is None
    assert reported_series(direct_series('US', ['2021-01-03']), 'US') is None
    # Weekly dates outside reviewed source calendars cannot be guessed.
    assert reported_series(direct_series('US', ['2021-01-02']), 'AU') is None


def test_japan_all_case_source_does_not_merge_historical_sentinel_counts():
    from src.generation.site_subdivision_maps import reported_series
    entry = direct_series('JP', ['2024-01-07'])
    entry['loss_risk'] = 'non_additive_series_not_rolled_up'
    sentinel = direct_source('JP', metric='sentinel_case_notifications')
    sentinel['series_code'] = 'SENTINEL'
    sentinel['reporting_basis'] = 'sentinel_surveillance'
    entry['source_series'].append(sentinel)
    assert reported_series(entry, 'JP')['points'] == [['2024-01-07', 0, 'provisional']]
    entry['selected_series_codes'] = ['SENTINEL']
    assert reported_series(entry, 'JP') is None
    entry['selected_series_codes'] = ['REVIEWED', 'SENTINEL']
    assert reported_series(entry, 'JP') is None


def test_unknown_registry_or_stratified_series_cannot_use_total_population():
    from src.generation.site_subdivision_maps import reported_series
    for patch in [{'source_system': 'UNKNOWN'}, {'metric_type': 'hospitalized_case_notifications'},
                  {'unit': 'rate'}, {'dimension_key': 'age:5'}, {'mapping_relation': 'broader'},
                  {'comparability_break': True}, {'temporal_granularity': 'annual'}]:
        entry = direct_series('US', ['2024-01-06'])
        entry['source_series'][0].update(patch)
        assert reported_series(entry, 'US') is None
    entry = direct_series('US', ['2024-01-06'])
    entry['source_series'][0]['geography_key'] = 'country:US:national'
    assert reported_series(entry, 'US', location_code='US-CA') is None


def test_revised_single_swedish_series_and_calendar_quarter_year_contracts():
    from src.generation.site_subdivision_maps import reported_series
    entry = direct_series('SE', ['2024-01-01', '2024-02-01'], cadence='monthly')
    entry['quality_statuses'] = ['revised', 'revised']
    entry['source_series'][0]['aggregation_policy'] = 'non_additive'
    result = reported_series(entry, 'SE')
    assert result['points'][0] == ['2024-01-01', 0, 'observed']
    assert result['periods'][1]['end_exclusive'] == '2024-03-01'
    for cadence, day, end in [('quarterly', '2024-04-01', '2024-07-01'), ('annual', '2024-01-01', '2025-01-01')]:
        result = reported_series({'period_granularity': cadence, 'dates': [day], 'cases': [12]}, 'AU')
        assert result['periods'][0] == {'start': day, 'end_exclusive': end, 'reporting_year': 2024}
        assert result['cadence'] == cadence


@pytest.mark.parametrize('code,source,basis,url', [
    ('US-CA', 'Census', 'july_estimated_resident_population', 'https://www2.census.gov/state.csv'),
    ('JP-13', 'StatisticsJapan', 'october_estimated_resident_population', 'https://www.e-stat.go.jp/table.xlsx'),
    ('SE-AB', 'SCB', 'year_end_resident_population_ckm', 'https://api.scb.se/table'),
])
def test_new_population_profiles_require_official_geography_year_source_and_basis(code, source, basis, url):
    row = population(code=code)
    row['source'] = source
    row['metadata'].update({'source_url': url, 'population_basis': basis})
    assert normalise_population(row)['source'] == source
    row['metadata']['source_url'] = 'https://example.com/'
    assert normalise_population(row) is None


def test_new_boundaries_have_all_reviewed_registered_regions_and_attribution():
    from src.core.country_library import get_country_bootstrap_config
    manifest = json.loads((BOUNDARIES / 'manifest.json').read_text())['countries']
    for code, size in [('US', 56), ('JP', 47), ('SE', 21)]:
        boundary = manifest[code]
        geometry = json.loads((BOUNDARIES / boundary['file']).read_text())
        keys = [item['properties']['code'] for item in geometry['features']]
        assert len(set(keys)) == size
        assert set(keys) == set(boundary['codes'])
        assert all(get_country_bootstrap_config(key)['parent_country_code'] == code for key in keys)
        assert boundary['metadata_url'] and boundary['source_license']
    assert 'US-SD' in manifest['US']['codes'] and 'SU-SD' not in manifest['US']['codes']
