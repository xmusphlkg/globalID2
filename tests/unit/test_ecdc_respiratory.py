"""ECDC laboratory units, ISO calendar, strata and immutable replay contracts."""
import base64
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
from src.data.crawlers import ecdc_respiratory as crawler
from src.data.processors.ecdc_respiratory import ECDCRespiratoryUpdater
from src.services.ingestion.countries import ecdc_respiratory as connector

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/EU_HIGH_FREQUENCY"
DATASET = "data/nonSentinelTestsDetections.csv"


def document(raw):
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=crawler.HEADERS)
    writer.writeheader(); writer.writerows(raw); return stream.getvalue().encode()


def sample():
    return list(csv.DictReader((FIXTURES / "nonSentinelTestsDetections.csv").open()))[0]


def parse(raw, **options):
    return crawler.parse_csv(document(raw), country_code="AT", dataset=DATASET, as_of=options.pop("as_of", date(2026, 11, 17)), **options)


def test_real_three_laboratory_sources_keep_tests_detections_positivity_and_origins():
    rows = []
    for dataset in crawler.DATASETS:
        country = "BE" if "SARI" in dataset else "AT"
        parsed, _ = crawler.parse_csv((FIXTURES / Path(dataset).name).read_bytes(), country_code=country, dataset=dataset, as_of=date(2026, 11, 17))
        rows.extend(parsed)
    assert {r["Measure"] for r in rows} == {"laboratory_tests", "laboratory_detections", "test_positivity"}
    assert {r["SourceSurveillanceType"] for r in rows} == set(crawler.DATASETS.values())
    assert all(r["AggregationPolicy"] == "non_additive" for r in rows)
    assert all(r["ReportingBasis"] != "notification" and "not_population_cases" in r["PopulationScope"] for r in rows)
    assert all(r["Unit"] == ("percent" if r["Measure"] == "test_positivity" else "count") for r in rows)
    assert all(r["PublicReleaseEnabled"] == "true" for r in rows)


def test_closed_iso_week_crosses_calendar_year_without_relabelling_source_year():
    raw = sample(); raw["yearweek"] = "2026-W53"
    rows, _ = parse([raw], as_of=date(2027, 1, 4))
    assert rows[0]["PeriodStart"] == "2026-12-28"
    assert rows[0]["PeriodEnd"] == "2027-01-03"
    assert rows[0]["SourceYear"] == "2026" and rows[0]["SourceWeek"] == "53"
    rows, audit = parse([raw], as_of=date(2027, 1, 3))
    assert rows == [] and audit["withheld_open_week"] == 1


@pytest.mark.parametrize("change", [{"yearweek": "2025-W53"}, {"yearweek": "2026-45"}, {"value": "-1"}, {"value": "1.5"}, {"indicator": "new_measure"}, {"survtype": "new_network"}, {"pathogen": "new_virus"}])
def test_changed_calendars_numeric_contracts_and_source_identity_fail_closed(change):
    raw = sample(); raw.update(change)
    with pytest.raises(ValueError):
        parse([raw])


def test_missing_zero_and_suppression_and_duplicate_identity():
    for value, expected in [("", "source_missing"), ("0", "reported"), ("<5", "source_suppressed")]:
        raw = sample(); raw["value"] = value
        rows, _ = parse([raw]); assert rows[0]["MissingValueStatus"] == expected
        assert rows[0]["Cases"] == value
    with pytest.raises(ValueError, match="Duplicate"):
        parse([sample(), sample()])
    with pytest.raises(ValueError, match="header"):
        crawler.parse_csv(b"country,value\nAustria,2\n", country_code="AT", dataset=DATASET)


def test_counts_never_merge_total_subtype_or_age():
    first = sample(); second = dict(first, pathogensubtype="A(H3)"); third = dict(first, age="0-4")
    rows, _ = parse([first, second, third])
    assert len({r["SourceDiseaseCode"] for r in rows}) == 3
    assert len({r["Dimensions"] for r in rows}) == 3


def artifact(tmp_path, payload, *, url, kind, metadata):
    digest = hashlib.sha256(payload).hexdigest(); path = tmp_path / "data/raw/eu" / (digest + ".bin")
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path), sha256=digest, byte_size=len(payload), source_url=url, kind=kind, metadata_=metadata)


def frozen_artifacts(tmp_path):
    artifacts, entries = [], []
    paths = ["README.MD", *crawler.DATASETS]
    for path in paths:
        payload = (FIXTURES / Path(path).name).read_bytes()
        sha = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        entries.append({"path": path, "type": "blob", "sha": sha, "size": len(payload)})
        artifacts.append(artifact(tmp_path, payload, url=f"https://api.github.com/repos/{crawler.REPOSITORY}/git/blobs/{sha}", kind="ecdc_respiratory_blob", metadata={"path": path, "git_blob_sha1": sha, "git_tree_sha1": "tree", "retrieved_at": "2026-11-17T00:00:00Z"}))
    tree = json.dumps({"sha": "tree", "truncated": False, "tree": entries}).encode()
    artifacts.append(artifact(tmp_path, tree, url=crawler.TREE_URL, kind="ecdc_respiratory_tree", metadata={"retrieved_at": "2026-11-17T00:00:00Z"}))
    return artifacts


def test_organize_is_offline_checks_complete_frozen_tree_and_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("organize accessed network"))
    artifacts = frozen_artifacts(tmp_path)
    result = connector.organize(artifacts, updater=ECDCRespiratoryUpdater(country_code="AT"))
    assert result.rows and all(r["RawSHA256"] and r["SourceDictionarySHA256"] for r in result.rows)
    assert result.source_audit["withdrawal_authority"] is False
    with pytest.raises(ValueError, match="incomplete"):
        connector.organize(artifacts[1:], updater=ECDCRespiratoryUpdater(country_code="AT"))
    Path(artifacts[0].relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        connector.organize(artifacts, updater=ECDCRespiratoryUpdater(country_code="AT"))


def test_blob_decode_verifies_git_identity_and_size():
    payload = b"source\n"; sha = hashlib.sha1(b"blob 7\0" + payload).hexdigest()
    doc = {"encoding": "base64", "sha": sha, "content": base64.b64encode(payload).decode(), "size": 7}
    assert connector.blob_payload(doc, sha) == payload
    with pytest.raises(ValueError):
        connector.blob_payload(dict(doc, size=8), sha)


def test_all_country_source_ids_are_independent_and_release_is_authorized():
    updaters = [ECDCRespiratoryUpdater(country_code=code) for code in crawler.COUNTRY_NAMES]
    assert len({u.ontology_source_id for u in updaters}) == 30
    assert all(u.public_release_enabled and not u.mapping_notifications_enabled for u in updaters)
    with pytest.raises(ValueError):
        ECDCRespiratoryUpdater(country_code="GB")
