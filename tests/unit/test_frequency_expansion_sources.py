"""Real WHO/PHAC/SPF source contracts and complete offline artifact replay."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers import who_flunet as flu, ca_respiratory_weekly as ca, spf_odisse_weekly as spf
from src.data.processors.who_flunet import WHOFluNetUpdater
from src.data.processors.ca_respiratory_weekly import CARespiratoryWeeklyUpdater
from src.data.processors.spf_odisse_weekly import SPFOdisseWeeklyUpdater
from src.services.ingestion.countries import who_flunet as flu_connector, ca_respiratory_weekly as ca_connector
from src.services.global_high_frequency_review import reviewed_global_high_frequency_record

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/FREQUENCY_EXPANSION"


def fixture(name):
    return json.loads((FIXTURES / name).read_bytes())


def flu_parse(raw=None, *, country="ET", as_of=date(2026, 11, 17)):
    records = fixture("flunet-et.json")["value"] if raw is None else raw
    fields = flu.detection_fields(fixture("flunet-dictionary.json")["value"])
    return flu.parse_records([(r, {}) for r in records], country_code=country, fields=fields, as_of=as_of)


def test_actual_country_flunet_is_not_malaysia_and_keeps_detection_denominators():
    rows, audit = flu_parse()
    latest = {r["SourceDiseaseCode"]: r for r in rows if r["Date"] == "2026-09-28"}
    assert latest["INF_ALL"]["Cases"] == "4"
    assert latest["INF_ALL"]["SourceInfluenzaSpecimensProcessed"] == 59
    assert latest["INF_ALL"]["GeographyKey"] == "country:ET:national"
    assert {r["Measure"] for r in rows} == {"laboratory_detections"}
    assert {r["AggregationPolicy"] for r in rows} == {"non_additive"}
    assert audit["missing_detection_cells"] > 0
    bn, _ = flu_parse(fixture("flunet-bn.json")["value"], country="BN")
    assert all(r["GeographyKey"] == "country:BN:national" for r in bn)


@pytest.mark.parametrize("change", [{"COUNTRY_CODE": "KEN"}, {"ISO2": "KE"}, {"ISO_WEEK": 2},
                                   {"ISOYW": 202601}, {"ORIGIN_SOURCE": "unknown"},
                                   {"INF_ALL": -1}, {"INF_ALL": 2.5}, {"INF_ALL": True}])
def test_country_iso_calendar_origin_and_counts_fail_closed(change):
    raw = fixture("flunet-et.json")["value"][-1]
    raw.update(change)
    with pytest.raises(ValueError):
        flu_parse([raw])


def test_flunet_missing_zero_suppression_and_duplicate_origin_are_distinct():
    raw = fixture("flunet-et.json")["value"][-1]
    raw.update(INF_ALL=None, INF_A=0, INF_B="<5")
    rows, _ = flu_parse([raw])
    values = {r["SourceDiseaseCode"]: r for r in rows}
    assert values["INF_ALL"]["MissingValueStatus"] == "source_missing"
    assert values["INF_A"]["Cases"] == "0"
    assert values["INF_B"]["MissingValueStatus"] == "source_suppressed"
    with pytest.raises(ValueError, match="Duplicate"):
        flu_parse([raw, raw])
    other_origin = {**raw, "ORIGIN_SOURCE": "NONSENTINEL"}
    rows, _ = flu_parse([raw, other_origin])
    assert {json.loads(r["Dimensions"])["surveillance_origin"] for r in rows} == {"SENTINEL", "NONSENTINEL"}


def test_open_weeks_and_country_contracts_do_not_create_false_availability():
    raw = fixture("flunet-et.json")["value"][-1]
    with pytest.raises(ValueError, match="No reported closed-week"):
        flu_parse([raw], as_of=date(2026, 10, 4))
    assert flu_parse([raw], as_of=date(2026, 10, 5))[0]
    with pytest.raises(ValueError, match="ISO2"):
        flu.country_contract("CA-ON")
    with pytest.raises(ValueError, match="No reported"):
        flu_parse([], country="GB")


def test_malaysia_source_identity_and_definition_do_not_change():
    raw = json.loads((FIXTURES.parent / "MY/who_flunet_sample.json").read_bytes())["value"]
    rows, _ = flu_parse(raw, country="MY")
    assert {r["DefinitionVersion"] for r in rows} == {"my_who_flunet_detections_v1"}
    assert WHOFluNetUpdater("MY").ontology_source_id == "SRC_MY_WHO_FLUNET"
    for updater in (WHOFluNetUpdater("ET"), CARespiratoryWeeklyUpdater(), SPFOdisseWeeklyUpdater("GP")):
        assert updater.public_release_enabled
        assert updater.mapping_notifications_enabled is False


def test_actual_phac_national_rows_do_not_sum_province_or_nested_viruses():
    rows, audit = ca.parse_csv((FIXTURES / "ca-weekly.csv").read_bytes(), as_of=date(2026, 11, 17))
    assert len(rows) == 300
    assert audit["source_records"] == 800 and audit["excluded_subnational_records"] == 700
    assert len({r["SourceVirus"] for r in rows}) == 10
    assert {r["Measure"] for r in rows} == {"laboratory_detections", "laboratory_tests", "laboratory_positivity"}
    assert {r["Unit"] for r in rows} == {"count", "percent"}
    assert {r["GeographyKey"] for r in rows} == {"country:CA:national"}
    assert all(r["AggregationPolicy"] == "non_additive" for r in rows)
    assert ca.parse_value("N/A") == ("", "source_missing")
    assert ca.parse_value("0") == ("0", "reported")
    for value in ("-1", "1.3", "nan"):
        with pytest.raises(ValueError):
            ca.parse_value(value)
    with pytest.raises(ValueError):
        ca.parse_value("100.1", percent=True)


def spf_parse(country="GP", records=None, metadata=None):
    selected = [r for r in fixture("spf-records.json") if r["country"] == country]
    records = [(r["dataset"], r["raw"], {}) for r in selected] if records is None else records
    return spf.parse_records(records, country_code=country,
                             metadata=metadata or fixture("spf-metadata.json"), as_of=date(2026, 11, 17))


@pytest.mark.parametrize("country", ["GP", "MQ", "GF"])
def test_actual_spf_activity_rates_keep_territory_age_and_inconsistent_denominator(country):
    rows = spf_parse(country)
    assert len(rows) == 18
    assert {r["Measure"] for r in rows} == {"healthcare_activity_rate"}
    assert {r["Unit"] for r in rows} == {"per_100000_passages", "per_100000_medical_acts"}
    assert {r["GeographyKey"] for r in rows} == {f"country:{country}:national"}
    assert len({r["SourceDataset"] for r in rows}) == 3
    assert all("__age_" in r["SourceDiseaseCode"] for r in rows)
    hospital = [r for r in rows if "_hospit_" in r["SourceMetricField"]]
    assert all(r["DenominatorConsistencyStatus"] == "field_passages_vs_narrative_admissions_disagree" for r in hospital)
    assert all(r["AggregationPolicy"] == "non_additive" for r in rows)


@pytest.mark.parametrize("change", [{"region": "02"}, {"semaine": "2026-S99"},
                                   {"sursaud_cl_age_gene": ""}, {"date_complet": "2026-11-03"}])
def test_spf_wrong_geography_calendar_or_stratum_fails(change):
    sample = next(r for r in fixture("spf-records.json") if r["country"] == "GP")
    raw = {**sample["raw"], **change}
    with pytest.raises(ValueError):
        spf_parse(records=[(sample["dataset"], raw, {})])


def test_spf_missing_rates_not_zero_and_changed_denominator_fails():
    assert spf.rate_value(None) == ("", "source_missing")
    assert spf.rate_value(0) == ("0", "reported")
    for value in (True, -0.1, float("nan")):
        with pytest.raises(ValueError):
            spf.rate_value(value)
    metadata = fixture("spf-metadata.json")
    dataset = next(iter(metadata))
    next(f for f in metadata[dataset]["fields"] if f["name"] == spf.metric_fields(dataset)[0])["description"] = "per population"
    with pytest.raises(ValueError, match="denominator"):
        spf_parse(metadata=metadata)


def artifact(tmp_path, body, *, metadata, url):
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "data/raw/fixtures" / f"{digest}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path), sha256=digest, byte_size=len(payload),
                           source_url=url, metadata_=metadata)


def test_country_flunet_and_phac_organize_are_offline_and_fail_incomplete_capture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("organize used network"))
    captured = "2026-11-17T00:00:00Z"
    arts = []
    for dataset, url, body in (("flunet_dictionary", flu.DICTIONARY_URL, fixture("flunet-dictionary.json")),
                               ("flunet_et", flu.data_url("ET"), fixture("flunet-et.json"))):
        arts.append(artifact(tmp_path, body, url=url, metadata={"endpoint": "who_odata_page", "dataset": dataset,
                             "page_number": 0, "requested_url": url, "retrieved_at": captured}))
    result = flu_connector.organize(arts, updater=WHOFluNetUpdater("ET"))
    assert result.rows and all(r["RawSHA256"] and r["SourceDictionarySHA256"] for r in result.rows)
    with pytest.raises(ValueError, match="Missing"):
        flu_connector.organize(arts[:1], updater=WHOFluNetUpdater("ET"))
    with pytest.raises(ValueError, match="Unexpected"):
        flu_connector.organize(arts, updater=WHOFluNetUpdater("KE"))
    ca_arts = [artifact(tmp_path, (FIXTURES / file).read_bytes(), url=url,
                        metadata={"endpoint": endpoint, "requested_url": url, "retrieved_at": captured})
               for endpoint, url, file in (("phac_landing", ca.LANDING_URL, "ca-landing.html"),
                                           ("phac_script", ca.SCRIPT_URL, "ca-binding.js"),
                                           ("phac_csv", ca.DATA_URL, "ca-weekly.csv"))]
    assert len(ca_connector.organize(ca_arts, updater=CARespiratoryWeeklyUpdater()).rows) == 300
    Path(ca_arts[-1].relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        ca_connector.organize(ca_arts, updater=CARespiratoryWeeklyUpdater())


def public_record(row, target):
    country = row["GeographyKey"].split(":")[1]
    source = (f"SRC_{country}_SPF_ODISSE_WEEKLY" if row.get("SourceDataset") else
              "SRC_CA_PHAC_RESPIRATORY" if row.get("SourceVirus") else f"SRC_{country}_WHO_FLUNET")
    return {"source_system": source, "reviewed_source_code": row["SourceDiseaseCode"],
            "definition_version": row["DefinitionVersion"], "disease_id": target,
            "geography_key": row["GeographyKey"], "temporal_granularity": row["Frequency"],
            "metric_type": row["Measure"], "series_unit": row["Unit"], "observation_unit": row["Unit"],
            "reporting_basis": row["ReportingBasis"], "aggregation_policy": "non_additive",
            "mapping_relation": "narrower", "comparability": "conditional",
            "dimensions": json.loads(row["Dimensions"])}


def test_typed_publication_accepts_actual_source_metrics_without_erasing_dimensions():
    flunet = next(r for r in flu_parse()[0] if r["SourceDiseaseCode"] == "INF_ALL")
    ca_rows, _ = ca.parse_csv((FIXTURES / "ca-weekly.csv").read_bytes(), as_of=date(2026, 11, 17))
    ca_selected = [r for r in ca_rows if r["SourceVirus"] == "SARS-CoV-2"]
    spf_selected = [r for r in spf_parse() if r["SourceDataset"] in spf.DATASETS
                    and spf.DATASETS[r["SourceDataset"]][0] == "covid_19"]
    for row, country, target in [(flunet, "ET", "D038"), *[(r, "CA", "D004") for r in ca_selected],
                                  *[(r, "GP", "D004") for r in spf_selected]]:
        record = public_record(row, target)
        result = reviewed_global_high_frequency_record(record, country)
        assert result is not None
        assert result["dimensions"] == record["dimensions"]
        assert result["metric_type"] == row["Measure"]
        assert result["projection_policy"] == "source_observations_only"
        assert "projection_dimension_key" not in result
        assert "projection_policy" not in record


@pytest.mark.parametrize("changes", [
    {"metric_type": "case_notifications"}, {"observation_unit": "percent"},
    {"mapping_relation": "exact"}, {"comparability": "comparable"},
    {"disease_id": "D014"}, {"geography_key": "country:KE:national"},
    {"projection_dimension_key": "all"}, {"projection_metric_type": "case_notifications"},
    {"dimensions": {}}, {"aggregation_policy": "additive"},
])
def test_typed_publication_rejects_cases_projection_unit_and_mapping_contamination(changes):
    row = next(r for r in flu_parse()[0] if r["SourceDiseaseCode"] == "AH5")
    record = public_record(row, "D038")
    record.update(changes)
    assert reviewed_global_high_frequency_record(record, "ET") is None


def test_static_ontology_series_use_original_code_and_holding_categories_stay_unprojected():
    row = next(r for r in flu_parse()[0] if r["SourceDiseaseCode"] == "INF_ALL")
    record = public_record(row, "D038")
    digest = hashlib.sha256(f"{record['source_system']}|INF_ALL|{row['DefinitionVersion']}".encode()).hexdigest()[:20]
    record["source_series_code"] = "SER_HF_ET_" + digest
    record.pop("reviewed_source_code")
    assert reviewed_global_high_frequency_record(record, "ET") is not None
    row = next(r for r in spf_parse() if spf.DATASETS[r["SourceDataset"]][0] == "influenza_like_illness")
    assert reviewed_global_high_frequency_record(public_record(row, "D038"), "GP") is None
