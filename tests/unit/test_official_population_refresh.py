from copy import deepcopy

import pytest

from scripts.refresh_official_population import CN_CODES, parse_ibge, parse_nbs


def nbs_fixture():
    return [
        {
            "code": "2025YY",
            "values": [
                {
                    "areaCode": prefix + "0000000000",
                    "value": "100.25",
                    "du_name": "万人",
                }
                for prefix in CN_CODES
            ],
        }
    ]


def test_nbs_converts_unit_and_requires_all_provinces_for_published_year():
    expected = {"CN-" + code for code in CN_CODES.values()}
    data = nbs_fixture()
    rows = parse_nbs(data, {2025, 2026}, expected)
    assert len(rows) == 31
    assert {r.population for r in rows} == {1_002_500}
    assert {r.year for r in rows} == {2025}  # unpublished 2026 is not filled
    data[0]["values"].pop()
    with pytest.raises(ValueError, match="Partial NBS"):
        parse_nbs(data, {2025}, expected)


@pytest.mark.parametrize(
    "field,value", [("du_name", "人"), ("kj1", "城镇"), ("value", "-1")]
)
def test_nbs_rejects_changed_units_dimensions_and_invalid_values(field, value):
    data = nbs_fixture()
    data[0]["values"][0][field] = value
    with pytest.raises(ValueError):
        parse_nbs(data, {2025}, {"CN-" + code for code in CN_CODES.values()})


def test_nbs_rejects_duplicate_location_year_and_national_geography_mismatch():
    data = nbs_fixture()
    data[0]["values"].append(deepcopy(data[0]["values"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_nbs(data, {2025}, {"CN-" + code for code in CN_CODES.values()})
    with pytest.raises(ValueError, match="national geography"):
        parse_nbs(nbs_fixture(), {2025}, {"CN"}, national=True)


def ibge_fixture():
    return [
        {},
        {"NC": "1", "MC": "45", "D1C": "1", "D2C": "9324", "D3C": "2026", "V": "300"},
        {"NC": "3", "MC": "45", "D1C": "35", "D2C": "9324", "D3C": "2026", "V": "100"},
        {"NC": "3", "MC": "45", "D1C": "33", "D2C": "9324", "D3C": "2026", "V": "200"},
    ]


STATES = {"35": {"sigla": "SP"}, "33": {"sigla": "RJ"}}


def test_ibge_keeps_local_geography_reference_and_source():
    rows = parse_ibge(ibge_fixture(), STATES)
    assert {r.country_code: r.population for r in rows} == {
        "BR": 300,
        "BR-SP": 100,
        "BR-RJ": 200,
    }
    assert all(r.source == "IBGE" and r.reference_date == "2026-07-01" for r in rows)


@pytest.mark.parametrize(
    "field,value", [("MC", "percent"), ("NC", "6"), ("D4C", "4"), ("V", "X")]
)
def test_ibge_rejects_wrong_units_geography_demographic_groups_or_partial_data(
    field, value
):
    data = ibge_fixture()
    data[-1][field] = value
    with pytest.raises(ValueError):
        parse_ibge(data, STATES)


def test_ibge_census_reference_is_kept_distinct_from_annual_estimates():
    data = ibge_fixture()
    for row in data[1:]:
        row["D2C"] = "93"
        row["D3C"] = "2022"
    rows = parse_ibge(data, STATES, census=True)
    assert all(
        r.reference_date == "2022-08-01" and r.basis == "census_resident_population"
        for r in rows
    )
