"""Real 506 source files and offline failure boundaries for Thailand onboarding."""
from datetime import date
import json
import io
import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
import requests

from src.data.crawlers.th import (
    CONTRACTS_BY_CODE, DEFAULT_SCOPE, Thailand506ContractError,
    _rtf_lines,
    discover_diseases, discover_monthly_report, parse_monthly_rtf,
    discover_monthly_reports, plan_history_reports, selected_contracts, validate_year_window,
)
from src.data.processors.th import ThailandDOE506MonthlyUpdater
from src.services.ingestion.countries import th

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/TH"
AUDIT = {item["code"]: item for item in json.loads((FIXTURES / "audit_2023.json").read_text())}


def fixture(name):
    with zipfile.ZipFile(FIXTURES / "reports_2023.zip") as archive:
        return archive.read(name)


def parse(code, payload=None, year=2023):
    return parse_monthly_rtf(
        fixture(code + ".rtf") if payload is None else payload,
        disease_code=code, year=year, source_url=AUDIT[code]["url"],
    )


@pytest.mark.parametrize("code", list(CONTRACTS_BY_CODE))
def test_every_published_506_category_has_a_real_validated_monthly_report(code):
    """All 72 categories are exercised, including zero and overlapping reports."""
    catalog = discover_diseases(fixture("index.html"))
    assert set(catalog) == set(CONTRACTS_BY_CODE)
    assert discover_monthly_report(fixture(code + "_history.html"), disease_code=code, year=2023) == AUDIT[code]["url"]
    rows = parse(code)
    assert len(rows) == 12
    assert [row["Month"] for row in rows] == list(map(str, range(1, 13)))
    assert sum(int(row["Cases"]) for row in rows) == AUDIT[code]["cases"]
    assert {row["RawDiseaseLabel"] for row in rows} == {AUDIT[code]["report_label"]}
    assert {row["SourceDiseaseCode"] for row in rows} == {code}
    assert {row["GeographyKey"] for row in rows} == {"country:TH:national"}
    assert {row["SourcePublishedAt"] for row in rows} == {"2024-09-12"}
    assert all(row["PublicReleaseEnabled"] == "false" for row in rows)
    assert all(row["DiseaseCode"] == "__source_native__" for row in rows)


def test_total_is_used_once_and_source_aggregates_keep_separate_identities():
    assert sum(int(row["Cases"]) for row in parse("15")) == 486973
    assert sum(int(row["Cases"]) for row in parse("262766")) == 160839
    assert parse("262766")[0]["SourceDiseaseCode"] != parse("26")[0]["SourceDiseaseCode"]
    assert all(row["Cases"] == "0" for row in parse("45"))


def _edit_national(payload, old, new):
    start = payload.index(b"\\cb1 Total\\plain\\tab")
    end = payload.index(b"\\par", start)
    segment = payload[start:end]
    assert old in segment
    return payload[:start] + segment.replace(old, new, 1) + payload[end:]


def test_missing_is_unknown_and_never_becomes_zero():
    payload = _edit_national(fixture("15.rtf"), b"\\cb1 10129\\plain", b"\\cb1 \\plain")
    january = parse("15", payload)[0]
    assert january["Cases"] == ""
    assert january["MissingValueStatus"] == "source_missing"
    assert january["Deaths"] == "0"


@pytest.mark.parametrize("replacement", [b"10129.5", b"-1", b"<5", b"1,01,29"])
def test_invalid_count_does_not_get_rounded_clamped_or_desuppressed(replacement):
    payload = _edit_national(fixture("15.rtf"), b"\\cb1 10129\\plain", b"\\cb1 " + replacement + b"\\plain")
    with pytest.raises(Thailand506ContractError, match="count"):
        parse("15", payload)


def test_annual_sum_mismatch_fails_closed():
    payload = _edit_national(fixture("15.rtf"), b"\\cb1 486973\\plain", b"\\cb1 486974\\plain")
    with pytest.raises(Thailand506ContractError, match="reconcile"):
        parse("15", payload)


@pytest.mark.parametrize("old,new", [
    (b"2566(2023)", b"2567(2024)"),
    (b"by Month  Thailand", b"by Month  Bangkok"),
    (b"12/9/2567", b"12/9/2566"),
    (b"\\cb1 Total\\plain\\tab", b"\\cb1 ZONE:01\\plain\\tab"),
])
def test_wrong_year_geography_or_unclosed_report_is_rejected(old, new):
    with pytest.raises(Thailand506ContractError):
        parse("15", fixture("15.rtf").replace(old, new))


@pytest.mark.parametrize("name,code", [("mcd_Influenza_67.rtf", "15"), ("tmcd_DHF_Total_67.rtf", "262766")])
def test_real_2024_files_are_preclosure_snapshots(name, code):
    with pytest.raises(Thailand506ContractError, match="before.*closed"):
        parse(code, (FIXTURES / name).read_bytes(), year=2024)


def test_source_code_is_not_inferred_from_a_foreign_disease_file():
    with pytest.raises(Thailand506ContractError, match="disease code"):
        parse("71", fixture("15.rtf"))


def test_metric_order_and_unsupported_rtf_controls_are_rejected():
    assert _rtf_lines(fixture("15.rtf"))[0][3] == "รหัส"
    with pytest.raises(Thailand506ContractError, match="metric order"):
        parse("15", fixture("15.rtf").replace(b"\\cb1 cases\\plain", b"\\cb1 deaths\\plain"))
    with pytest.raises(Thailand506ContractError, match="Unsupported RTF"):
        parse("15", fixture("15.rtf")[:-1] + b"\\u1000}")


def test_source_catalog_drift_and_external_report_links_fail_closed():
    with pytest.raises(Thailand506ContractError, match="Unreviewed"):
        discover_diseases(b'<a href="disease.php?ds=99">New disease</a>')
    with pytest.raises(Thailand506ContractError, match="Source label changed"):
        discover_diseases(b'<a href="disease.php?ds=15">New label</a>')
    with pytest.raises(Thailand506ContractError, match="found 0"):
        discover_monthly_report(b'<a href="https://example.com/surdata/y66/mcd_Influenza_66.rtf">66</a>', disease_code="15", year=2023)


def artifacts_for(codes):
    artifacts = [SimpleNamespace(metadata_={"endpoint": "index"}, kind="html_report", payload=fixture("index.html"))]
    artifacts.append(SimpleNamespace(metadata_={"endpoint": "collection_audit"}, kind="archive_json", payload=json.dumps({"expected": [[code, 2023] for code in codes], "collection_errors": []}).encode()))
    for code in codes:
        artifacts.append(SimpleNamespace(metadata_={"endpoint": "history", "disease_code": code}, kind="html_report", payload=fixture(code + "_history.html")))
        artifacts.append(SimpleNamespace(metadata_={"endpoint": "monthly", "disease_code": code, "year": 2023}, kind="monthly_rtf", payload=fixture(code + ".rtf"), source_url=AUDIT[code]["url"]))
    return artifacts


def test_organize_is_offline_and_keeps_bad_disease_audit_without_discarding_good_reports(monkeypatch):
    artifacts = artifacts_for(["15", "71"])
    artifacts[-1].payload = _edit_national(artifacts[-1].payload, b"\\cb1 65671\\plain", b"\\cb1 65672\\plain")
    monkeypatch.setattr(th, "read_artifact", lambda artifact: artifact.payload)
    monkeypatch.setattr(th.ThailandDOE506Crawler, "get", lambda *args, **kwargs: pytest.fail("organize attempted network access"))
    result = th.organize(artifacts, updater=ThailandDOE506MonthlyUpdater(), disease_codes=["15", "71"])
    assert len(result.rows) == 12
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["valid_reports"] == 1
    assert result.source_audit["failed_reports"][0]["disease_code"] == "71"
    assert result.source_audit["authoritative_replacement"] is False


def test_full_organize_covers_all_categories_and_duplicate_artifacts_are_rejected(monkeypatch):
    artifacts = artifacts_for(list(CONTRACTS_BY_CODE))
    monkeypatch.setattr(th, "read_artifact", lambda artifact: artifact.payload)
    result = th.organize(artifacts, updater=ThailandDOE506MonthlyUpdater())
    assert len(result.rows) == 864
    assert result.source_audit["valid_reports"] == 72
    assert result.source_audit["failed_reports"] == []
    assert result.source_latest_date == date(2023, 12, 1)
    with pytest.raises(Thailand506ContractError, match="Duplicate"):
        th.organize([*artifacts, artifacts[-1]], updater=ThailandDOE506MonthlyUpdater())


def test_request_bounds_and_public_release_contract():
    assert len(selected_contracts()) == 72
    for codes in [[], ["99"], ["15", "15"]]:
        with pytest.raises(Thailand506ContractError):
            selected_contracts(codes)
    for start, end in [(2002, 2003), (2023, 2022), (date.today().year, date.today().year)]:
        with pytest.raises(Thailand506ContractError):
            validate_year_window(start, end)
    updater = ThailandDOE506MonthlyUpdater()
    assert updater.source_scope == DEFAULT_SCOPE
    assert updater.public_release_enabled is False
    assert updater.license_review_status == "unverified"


async def test_collect_archives_each_available_response_and_reports_individual_network_failure(monkeypatch):
    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, payload, *, source_url, kind, metadata):
            self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata))

    class Crawler:
        session = SimpleNamespace(close=lambda: None)

        def get(self, url):
            if url == th.INDEX_URL:
                content = fixture("index.html")
            elif "dcontent=old" in url:
                code = url.split("ds=")[1].split("&")[0]
                content = fixture(code + "_history.html")
            elif url == AUDIT["15"]["url"]:
                raise requests.HTTPError("Upstream temporarily unavailable")
            elif url == AUDIT["71"]["url"]:
                content = fixture("71.rtf")
            else:
                pytest.fail(f"Unexpected request {url}")
            return SimpleNamespace(content=content, url=url)

    monkeypatch.setattr(th, "ThailandDOE506Crawler", Crawler)
    writer = Writer()
    artifacts = await th.collect(writer, disease_codes=["15", "71"], start_year=2023, end_year=2023)
    assert len([a for a in artifacts if a.kind == "monthly_rtf"]) == 1
    audit = json.loads(artifacts[-1].payload)
    assert audit["expected"] == [["15", 2023], ["71", 2023]]
    assert audit["collection_errors"][0]["disease_code"] == "15"
    monkeypatch.setattr(th, "read_artifact", lambda artifact: artifact.payload)
    result = th.organize(artifacts, updater=ThailandDOE506MonthlyUpdater(), disease_codes=["15", "71"], start_year=2023, end_year=2023)
    assert result.source_audit["status"] == "partial"
    assert len(result.rows) == 12


def test_dynamic_discovery_refreshes_latest_advertised_years_without_hardcoded_2023():
    histories = {code: fixture(code + "_history.html") for code in CONTRACTS_BY_CODE}
    links = discover_monthly_reports(histories["15"], disease_code="15")
    assert min(links) == 2003
    assert max(links) == 2025
    plan = plan_history_reports(histories, as_of="2026-11-15")
    assert plan["selected_years"] == [2025, 2024, 2023]
    assert len(plan["selected_reports"]) == 216
    assert plan["next_report_offset"] is None
    assert plan["total_discovered_reports"] == 216


def test_history_batches_resume_without_repeating_or_skipping_and_pin_source_plan_hash():
    histories = {code: fixture(code + "_history.html") for code in CONTRACTS_BY_CODE}
    first = plan_history_reports(histories, mode="history", max_reports=144, as_of="2026-11-15")
    assert first["total_discovered_reports"] == 72 * 23
    assert first["next_report_offset"] == 144
    assert first["selected_years"] == list(range(2025, 2002, -1))
    second = plan_history_reports(histories, mode="history", max_reports=144,
                                  report_offset=144, resume_plan_hash=first["plan_hash"], as_of="2026-11-15")
    keys = lambda plan: {(item["disease_code"], item["year"]) for item in plan["selected_reports"]}
    assert not keys(first) & keys(second)
    assert {item["year"] for item in second["selected_reports"]} == {2023, 2022}
    histories["15"] = histories["15"].replace(b"y66/mcd_Influenza_66.rtf", b"y66/mcd_Influenza_changed_66.rtf")
    with pytest.raises(Thailand506ContractError, match="discovery changed"):
        plan_history_reports(histories, mode="history", max_reports=144,
                             report_offset=144, resume_plan_hash=first["plan_hash"], as_of="2026-11-15")


def test_history_plan_bounds_and_final_cursor():
    histories = {"15": fixture("15_history.html")}
    plan = plan_history_reports(histories, disease_codes=["15"], start_year=2003, end_year=2023,
                                mode="history", max_reports=10, report_offset=20)
    assert plan["next_report_offset"] is None
    assert plan["remaining_reports"] == 0
    assert plan["selected_reports"][0]["year"] == 2003
    for args in [{"report_offset": -1}, {"max_reports": 0}, {"max_reports": 361}, {"report_offset": 10000}, {"recent_years": 0}]:
        with pytest.raises(Thailand506ContractError):
            plan_history_reports(histories, disease_codes=["15"], **args)


@pytest.mark.parametrize("year", [2003, 2019, 2020])
def test_real_legacy_report_title_and_singular_death_heading(year):
    audit = {item["year"]: item for item in json.loads((FIXTURES / "legacy_samples.json").read_text())}
    with zipfile.ZipFile(FIXTURES / "legacy_samples.zip") as archive:
        payload = archive.read(f"{year}/15.rtf")
    rows = parse_monthly_rtf(payload, disease_code="15", year=year, source_url=audit[year]["url"])
    assert len(rows) == 12
    assert sum(int(row["Cases"]) for row in rows) == audit[year]["cases"]
    assert rows[0]["SourcePublishedAt"] == audit[year]["published"]


async def test_local_history_bundle_preserves_source_bytes_urls_and_missing_reports(monkeypatch, tmp_path):
    import scripts.import_thailand_history as importer

    (tmp_path / "index.html").write_bytes(fixture("index.html"))
    (tmp_path / "15_history.html").write_bytes(fixture("15_history.html"))
    (tmp_path / "71_history.html").write_bytes(fixture("71_history.html"))
    (tmp_path / "2023").mkdir()
    (tmp_path / "2023/15.rtf").write_bytes(fixture("15.rtf"))

    class Writer:
        def __init__(self):
            self.artifacts = []

        async def write(self, payload, *, source_url, kind, metadata):
            self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata))

    connector = importer.LocalHistoryConnector(tmp_path)
    writer = Writer()
    artifacts = await connector.collect(writer, source=DEFAULT_SCOPE, year=2023)
    assert len(artifacts) == 1
    assert artifacts[0].kind == "th_history_bundle"
    monkeypatch.setattr(importer, "read_artifact", lambda artifact: artifact.payload)
    result = connector.organize(artifacts, updater=ThailandDOE506MonthlyUpdater(), source=DEFAULT_SCOPE, year=2023)
    assert len(result.rows) == 12
    assert result.rows[0]["SourceURL"] == AUDIT["15"]["url"]
    assert result.rows[0]["RawSHA256"] == hashlib.sha256(fixture("15.rtf")).hexdigest()
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["valid_reports"] == 1
    # An altered member cannot pass the per-source raw manifest check.
    with zipfile.ZipFile(io.BytesIO(artifacts[0].payload)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["2023_15.rtf"] += b"corrupt"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    artifacts[0].payload = stream.getvalue()
    with pytest.raises(ValueError, match="member hash mismatch"):
        connector.organize(artifacts, updater=ThailandDOE506MonthlyUpdater(), source=DEFAULT_SCOPE, year=2023)
