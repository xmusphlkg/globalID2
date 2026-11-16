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


@pytest.mark.parametrize("changed_codes", [[], ["CN-GD"]])
def test_explicit_population_export_queues_dirty_incremental_release(monkeypatch,tmp_path,changed_codes):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import sys
    from scripts import refresh_official_population as refresh
    from src.services.data_release_service import data_release_service
    class LocalSource:
        def __init__(self,path):
            self.output=path
            path.mkdir(parents=True,exist_ok=True)
            self.sources=[]
    data=refresh.SubdivisionPopulation("CN-GD",2024,100,"NBS","2024-12-31","https://example.org/nbs","year_end")
    apply=AsyncMock(return_value={"changed_country_codes":changed_codes,"changed_rows":len(changed_codes),"task_uuid":"audit" if changed_codes else None})
    release=AsyncMock(return_value={"status":"queued","task_uuid":"release"})
    monkeypatch.setattr(refresh,"PublicSource",LocalSource)
    monkeypatch.setattr(refresh,"fetch_nbs",lambda *args:[data])
    monkeypatch.setattr(refresh,"apply_rows",apply)
    monkeypatch.setattr(data_release_service,"trigger_job",release)
    monkeypatch.setattr(sys,"argv",["refresh_official_population.py","--sources","CN","--apply","--export","--start-year","2024","--output",str(tmp_path)])
    refresh.main()
    release.assert_awaited_once_with("site-release",manual=True,trigger="official-population-cli")
    assert apply.await_args.args[0]==[data]
    import json
    assert json.loads((tmp_path/"latest.json").read_text())["changes"]["changed_country_codes"]==changed_codes


def test_ibge_discovers_all_state_totals_without_separate_registry_api():
    from types import SimpleNamespace
    from scripts import refresh_official_population as refresh
    class Source:
        def __init__(self):
            self.urls = []
        def fetch(self, name, url):
            self.urls.append(url)
            census = '6579' not in url
            year = '2022' if '4714' in url else '2000' if census else '2026'
            variable = '93' if census else '9324'
            data = [{}, {'NC': '1', 'MC': '45', 'D1C': '1', 'D1N': 'Brasil', 'D2C': variable, 'D3C': year, 'V': '2700'}]
            data += [{'NC': '3', 'MC': '45', 'D1C': ident, 'D1N': abbreviation, 'D2C': variable, 'D3C': year, 'V': '100'} for ident, abbreviation in refresh.IBGE_UF.items()]
            return SimpleNamespace(json=lambda: data)
    source = Source()
    rows, states = refresh.fetch_ibge(source)
    assert len(states) == 27 and len(rows) == 84
    assert states['35']['sigla'] == 'SP'
    assert all(url.startswith(refresh.IBGE_BASE) for url in source.urls)
    assert '/p/all' in source.urls[0]


def test_abs_discovers_latest_release_and_its_workbook(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scripts import refresh_official_population as refresh
    latest = refresh.ABS_PAGE + '/jun-2027'
    workbook = 'https://www.abs.gov.au/new-release/310104.xlsx'
    class Source:
        output = tmp_path
        def fetch(self, name, url):
            if name == 'abs_latest.html':
                return SimpleNamespace(url=refresh.ABS_PAGE, text=f'<a href="{refresh.ABS_PAGE}/mar-2027">old</a><a href="{latest}">new</a>')
            if name == 'abs_release.html':
                assert url == latest
                return SimpleNamespace(url=latest, text=f'<a href="{workbook}">Table 4</a>')
            assert name == 'abs_310104.xlsx' and url == workbook
    load = lambda path, url: ('dynamically-discovered', str(path), url)
    monkeypatch.setattr(refresh, 'load_abs_population', load)
    assert refresh.fetch_abs(Source()) == ('dynamically-discovered', str(tmp_path / 'abs_310104.xlsx'), workbook)
