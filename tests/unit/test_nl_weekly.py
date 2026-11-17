from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from src.data.crawlers.nl_weekly import (
    RESP_PROFILES,
    RESP_SCOPE,
    VIRO_SCOPE,
    NetherlandsWeeklyCrawler,
    NLWeeklyContractError,
    parse_respiratory_resource,
    parse_virological_csv,
)
from src.data.processors.nl_weekly import (
    NLRespiratoryWeeklyUpdater,
    NLVirologicalWeeklyUpdater,
)
from src.services.ingestion.countries import nl_weekly

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/NL"


def viro():
    return (FIXTURES / "virological_weekly.csv").read_bytes()


def respiratory():
    with ZipFile(FIXTURES / "respiratory_weekly.zip") as archive:
        package = json.loads(archive.read("metadata.json"))
        payloads = {r["id"]: archive.read(r["id"]) for r in package["result"]["resources"]}
    return package, payloads


def test_virological_all_categories_values_and_denominators():
    rows, audit = parse_virological_csv(viro(), as_of=date(2026, 11, 16))
    assert len(rows) == 1323
    assert audit["source_categories"] == 49
    assert audit["source_weeks"][0] == "202645"
    assert audit["source_weeks"][-1] == "202619"
    newest = {r["RawDiseaseLabel"]: r for r in rows if r["SourceYearWeek"] == "202645"}
    assert newest["Influenza A virus"]["Cases"] == "65"
    assert newest["Norovirus"]["Cases"] == "38"
    assert newest["Influenza C virus"]["Cases"] == "0"
    assert all(r["ReportingLaboratories"] == "18" for r in newest.values())
    assert all(r["Date"] == "2026-01-01" and r["PeriodStart"] == r["PeriodEnd"] == "" for r in rows)
    assert len({(r["SourceDiseaseCode"], r["Dimensions"]) for r in rows}) == len(rows)


def test_network_crawler_is_constructible():
    crawler = NetherlandsWeeklyCrawler()
    crawler.session.close()


@pytest.mark.parametrize("original,replacement", [
    (b"202645;202644", b"202645;202645"),
    (b"AantalLabs;18", b"AantalLabs;"),
    (b"Influenza A virus;65", b"Influenza A virus;-65"),
    (b"Influenza A virus;65", b"Influenza A virus;1.5"),
])
def test_virological_rejects_unsafe_data(original, replacement):
    with pytest.raises(NLWeeklyContractError):
        parse_virological_csv(viro().replace(original, replacement), as_of=date(2026, 11, 16))


def test_blank_is_unknown_not_zero_and_future_release_rejected():
    rows, audit = parse_virological_csv(viro().replace(b"Influenza A virus;65", b"Influenza A virus;"), as_of=date(2026, 11, 16))
    assert len(rows) == 1323 and audit["source_blank_cells"] == 1
    missing = next(r for r in rows if r["RawDiseaseLabel"] == "Influenza A virus" and r["SourceYearWeek"] == "202645")
    assert missing["Cases"] == "" and missing["MissingValueStatus"] == "source_missing"
    with pytest.raises(NLWeeklyContractError):
        parse_virological_csv(viro(), as_of=date(2026, 11, 14))


def test_viro_rejects_period_gaps_and_impossible_laboratory_denominator():
    with pytest.raises(NLWeeklyContractError, match="consecutive"):
        parse_virological_csv(viro().replace(b"202644", b"202618"), as_of=date(2026, 11, 16))
    with pytest.raises(NLWeeklyContractError, match="zero reporting"):
        parse_virological_csv(viro().replace(b"AantalLabs;18;", b"AantalLabs;0;"), as_of=date(2026, 11, 16))


def parse_resource(name):
    package, payloads = respiratory()
    by_name = {r["name"]: r for r in package["result"]["resources"]}
    resource = by_name[name]
    config = json.loads(payloads[by_name[RESP_PROFILES[name][-1]]["id"]])
    return resource, config, payloads[resource["id"]], package


@pytest.mark.parametrize("name", list(RESP_PROFILES))
def test_all_reviewed_seasonal_resources_have_proven_metric_and_source_period(name):
    resource, config, payload, package = parse_resource(name)
    rows, audit = parse_respiratory_resource(payload, resource=resource, config=config,
        source_url=resource["url"], metadata_modified=package["result"]["metadata_modified"])
    assert rows and audit["dates_inferred"] is False
    assert all(json.loads(r["Dimensions"])["source_season"] == r["SourceSeason"] for r in rows)
    assert all(r["Date"] == r["SourceSeason"][:4] + "-01-01" for r in rows)
    assert all(r["DateRole"] == "source_season_first_year_storage_anchor_only" for r in rows)
    assert len({r["Dimensions"] for r in rows}) == len(rows)
    assert all(r["Unit"] == RESP_PROFILES[name][2] for r in rows)


def test_respiratory_rejects_wrong_config_unit_and_source_identity():
    resource, config, payload, package = parse_resource("tab_nps_zorg_ari_inc_website")
    options = {"resource": resource, "source_url": resource["url"], "metadata_modified": package["result"]["metadata_modified"]}
    with pytest.raises(NLWeeklyContractError, match="resource"):
        parse_respiratory_resource(payload, config={**config, "dataSrc": "https://example.invalid"}, **options)
    with pytest.raises(NLWeeklyContractError, match="unit"):
        parse_respiratory_resource(payload, config={**config, "yAxis": {"title": "Percentage deelnemers"}}, **options)
    with pytest.raises(NLWeeklyContractError, match="season/week"):
        parse_respiratory_resource(payload, config={**config, "categoryStart": 46}, **options)


def test_respiratory_no_fabrication_from_blanks_and_duplicate_rejected():
    resource, config, _payload, package = parse_resource("tab_nps_zorg_ari_inc_website")
    options = {"resource": resource, "config": config, "source_url": resource["url"], "metadata_modified": package["result"]["metadata_modified"]}
    header = '"season";"week";"value"\n'
    source = header + '"2026/2027";"40";""\n"2026/2027";"41";"0"\n'
    rows, audit = parse_respiratory_resource(source.encode(), **options)
    assert len(rows) == 2 and rows[1]["Cases"] == "0" and audit["blank_cells"] == 1
    assert rows[0]["Cases"] == "" and rows[0]["MissingValueStatus"] == "source_missing"
    with pytest.raises(NLWeeklyContractError, match="Duplicate"):
        parse_respiratory_resource((source + '"2026/2027";"41";"1"\n').encode(), **options)


def test_future_source_season_is_not_an_observation():
    resource, config, _payload, package = parse_resource("tab_nps_zorg_ari_inc_website")
    with pytest.raises(NLWeeklyContractError, match="future source season"):
        parse_respiratory_resource(b'season;week;value\n2027/2028;40;3\n',
            resource=resource, config=config, source_url=resource["url"],
            metadata_modified=package["result"]["metadata_modified"])


def test_complete_respiratory_offline_replay_and_missing_artifact_fails(monkeypatch):
    package, payloads = respiratory()
    metadata = SimpleNamespace(kind="rivm_respiratory_metadata", data=json.dumps(package).encode())
    artifacts = [metadata]
    for r in package["result"]["resources"]:
        artifacts.append(SimpleNamespace(kind="rivm_respiratory_resource", source_url=r["url"],
            metadata_={"resource_id": r["id"], "resource_name": r["name"]}, data=payloads[r["id"]]))
    monkeypatch.setattr(nl_weekly, "read_artifact", lambda a: a.data)
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("Offline organize made a network request"))
    fetched = nl_weekly.organize(artifacts, updater=NLRespiratoryWeeklyUpdater(), source=RESP_SCOPE)
    assert fetched.source_audit["captured_csv_resources"] == 23
    assert fetched.source_audit["normalized_resources"] == 17
    assert len(fetched.source_audit["excluded_resources"]) == 6
    assert len({(r["SourceDiseaseCode"], r["Dimensions"]) for r in fetched.rows}) == len(fetched.rows)
    assert {r["Unit"] for r in fetched.rows} == {"count", "percent", "per_100000", "100billion_particles_per_100000"}
    with pytest.raises(NLWeeklyContractError, match="manifest"):
        nl_weekly.organize(artifacts[:-1], updater=NLRespiratoryWeeklyUpdater(), source=RESP_SCOPE)


def test_updaters_disable_public_release_and_notifications_and_keep_source_window():
    for updater, source in [(NLVirologicalWeeklyUpdater(), VIRO_SCOPE), (NLRespiratoryWeeklyUpdater(), RESP_SCOPE)]:
        assert updater.mapping_notifications_enabled is updater.public_release_enabled is False
        rows = [{"Date": "2022-01-01"}, {"Date": "2026-01-01"}]
        assert updater.prepare_rows(rows, source=source, months=[(2026, 11)]).rows == rows
