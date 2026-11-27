from copy import deepcopy

from src.generation.us_nndss_projection import US_NNDSS_WEEKLY_LABELS, project_us_nndss_weekly


def observation(disease, label, day, cases, flag=None):
    return {
        "disease_id": disease, "series_code": label, "source_label": label,
        "source_system": "SRC_US_NNDSS", "geography_key": "country:US:national",
        "date": day, "cases": cases, "current_week_flag": flag,
        "unit": "count", "metric_type": "case_notifications", "dimension_key": "all",
        "temporal_granularity": "weekly", "mapping_relation": "exact", "population": 100000,
        "incidence_rate": cases, "incidence_rate_source": "wpp_computed", "population_source": "WPP",
    }


def test_botulism_requires_three_weekly_components_and_preserves_sources():
    labels = US_NNDSS_WEEKLY_LABELS["D091"]
    rows = [
        observation("D091", label, day, value, flag)
        for day, values in [
            ("2024-03-09", [(1, "-"), (4, None), (1, None)]),
            ("2024-06-08", [(1, None), (1, None), (None, "-")]),
            ("2024-08-24", [(None, "-"), (None, "-"), (1, None)]),
            ("2024-08-31", [(None, "U"), (None, "-"), (1, None)]),
            ("2024-09-07", [(1, None), (2, None)]),
        ] for label, (value, flag) in zip(labels, values)
    ]
    rows.append({**observation("D091", labels[2], "2024-09-07", 10), "geography_key": "country:US-TX:national"})
    original = deepcopy(rows)
    result = project_us_nndss_weekly([{"date": "2024-08-31", "disease_id": "D091", "cases": 99}], rows)
    assert [(r["date"], r["cases"], r["incidence_rate"]) for r in result] == [
        ("2024-03-09", 6, 6), ("2024-06-08", 2, 2), ("2024-08-24", 1, 1),
    ]
    assert rows == original


def test_coccidioidomycosis_prefers_published_total_across_label_changes():
    d = "D095"
    rows = [
        observation(d, "Coccidioidomycosis", "2022-03-05", 123),
        observation(d, "Coccidioidomycosis, total", "2024-03-23", 461),
        observation(d, "Coccidioidomycosis, Confirmed", "2024-03-23", 459),
        observation(d, "Coccidioidomycosis, Probable", "2024-03-23", 2),
        observation(d, "Coccidioidomycosis, Total", "2025-03-22", 400),
        observation(d, "Coccidioidomycosis, Confirmed", "2025-03-29", 409),
    ]
    result = project_us_nndss_weekly([], rows)
    assert [r["cases"] for r in result] == [123, 461, 400]
    assert len(result[0]["_series_context"]["source_series"]) == 5


def test_west_nile_keeps_history_and_preserves_unknown_and_unavailable_weeks():
    label, = US_NNDSS_WEEKLY_LABELS["D128"]
    rows = [
        observation("D128", label, "2024-03-02", None, "-"),
        observation("D128", label, "2024-03-09", None, "N"),
        observation("D128", label, "2024-03-16", 23, "-"),
        {**observation("D128", label, "2024-03-23", None, "-"), "source_value_raw": "U"},
    ]
    legacy = [{"disease_id": "D128", "cases": 4, "date": day} for day in ["2020-01-04", "2024-03-09"]]
    result = project_us_nndss_weekly(legacy, rows)
    assert [(r["date"], r["cases"]) for r in result] == [
        ("2020-01-04", 4), ("2024-03-02", 0), ("2024-03-16", 23),
    ]
    source, = result[1]["_series_context"]["source_series"]
    assert source["values"] == [None, None, 23, None]
    assert source["current_week_flags"] == ["-", "N", "-", "-"]
    assert source["source_values_raw"][-1] == "U"



def test_partial_denominator_cannot_generate_botulism_rate():
    labels = US_NNDSS_WEEKLY_LABELS["D091"]
    rows = [observation("D091", label, "2024-03-09", 1) for label in labels]
    rows[1]["population"] = None
    point, = project_us_nndss_weekly([], rows)
    assert point["cases"] == 3
    assert point["incidence_rate"] is None
    assert point["incidence_rate_source"] == "missing_population"


def test_zero_incidence_uses_week_population_source():
    label, = US_NNDSS_WEEKLY_LABELS["D128"]
    row = {**observation("D128", label, "2024-03-09", None, "-"),
           "incidence_rate_source": "missing_population", "population_source": "WPP"}
    point, = project_us_nndss_weekly([], [row])
    assert (point["incidence_rate"], point["incidence_rate_source"]) == (0, "wpp_computed")



def test_snapshot_with_only_a_narrow_dengue_or_confirmed_coccidioides_category():
    rows = [observation("D021", "Dengue virus infections, Severe dengue", "2024-03-09", 3),
            observation("D095", "Coccidioidomycosis, Confirmed", "2024-03-09", 4)]
    assert project_us_nndss_weekly([], rows) == []


def test_botulism_incidence_uses_components_after_week_alignment():
    rows = [
        {**observation("D091", label, "2021-01-09", 2),
         "dimensions": {"source_reporting_year": 2021, "source_reporting_week": 2}}
        for label in US_NNDSS_WEEKLY_LABELS["D091"]
    ]
    point, = project_us_nndss_weekly([], rows)
    assert (point["date"], point["cases"], point["incidence_rate"]) == ("2021-01-11", 6, 6)
