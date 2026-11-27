from copy import deepcopy

from src.generation.br_sinan_projection import BR_SINAN_AGE_SERIES, br_sinan_age_totals


def test_sinan_age_totals_require_both_age_groups_for_each_disease_and_month():
    def record(disease_id, age, month, cases, **overrides):
        return {
            "disease_id": disease_id,
            "series_code": BR_SINAN_AGE_SERIES[disease_id][age],
            "date": f"2024-{month}-01",
            "cases": cases,
            "incidence_rate": cases * 0.1 if cases is not None else None,
            "population": 1000000,
            "population_source": "WPP",
            "source_system": "SRC_BR_SINAN",
            "geography_key": "country:BR:national",
            "dimension_key": "all",
            "temporal_granularity": "monthly",
            "metric_type": "case_notifications",
            "reporting_basis": "notification",
            "unit": "count",
            "mapping_relation": "exact",
            **overrides,
        }

    records = [
        record("D005", 0, "04", 3653), record("D005", 1, "04", 25),
        record("D162", 0, "04", 4601), record("D162", 1, "04", 1, population=None),
        record("D162", 0, "05", 4210),
        record("D162", 1, "05", 24, geography_key="country:BR-SP:national"),
        record("D162", 1, "05", 10, series_code="SER_BR_HIV_EXPOSURE_HIVE"),
        record("D162", 0, "06", 4052), record("D162", 1, "06", None),
        record("D162", 0, "07", 25), record("D162", 1, "07", 0, population=999999),
    ]
    original = deepcopy(records)
    projected = br_sinan_age_totals(records)
    assert [(r["disease_id"], r["date"], r["cases"]) for r in projected] == [
        ("D005", "2024-04-01", 3678),
        ("D162", "2024-04-01", 4602),
        ("D162", "2024-07-01", 25),
    ]
    assert projected[0]["incidence_rate"] == 367.8
    assert projected[0]["incidence_rate_source"] == "wpp_computed"
    assert all(r["incidence_rate"] is None and r["incidence_rate_source"] == "missing_population"
               for r in projected[1:])
    assert all(r["_series_context"]["projection_policy"] == "sum_disjoint" for r in projected)
    assert all(r["series_code"] is None for r in projected)
    assert records == original
