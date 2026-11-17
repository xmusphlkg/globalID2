"""DDS catalog, explicit single-week selection and immutable PDF contracts."""
from contextlib import contextmanager
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pdfplumber
import pytest
import requests

from src.data.crawlers.th_dds import (
    DDSContractError, DDSDisease, closed_single_weeks, disease_groups, diseases,
    parse_weekly_pdf, parse_calendar_workbook, sanitize_form, week_dates,
)
from src.data.processors.th_dds import ThailandDDSWeeklyUpdater
from src.services.ingestion.countries.th_dds import organize

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/TH/dds"
FLU = DDSDisease("15", "ไข้หวัดใหญ่", "1")
ANTHRAX = DDSDisease("45", "แอนแทรกซ์", "6")
AS_OF = date(2026, 11, 15)
CALENDAR = parse_calendar_workbook((FIXTURE / "calendar_2026.xlsx").read_bytes(), year=2026)


def parse(payload, disease=FLU, **kwargs):
    return parse_weekly_pdf(payload, disease=disease, year=2026, week=46,
                            as_of=AS_OF, calendar=CALENDAR, **kwargs)


def test_public_catalog_enumerates_all_real_groups_including_empty_groups():
    groups = disease_groups((FIXTURE / "national_form.html").read_bytes())
    assert len(groups) == 11
    catalog = [d for group, _ in groups
               for d in diseases((FIXTURE / f"group_{group}.html").read_bytes(), group)]
    assert len(catalog) == len({d.code for d in catalog}) == 62
    assert {"02", "12", "13", "92", "93", "95", "96"} <= {d.code for d in catalog}
    assert diseases((FIXTURE / "group_2.html").read_bytes(), "2") == []
    assert diseases(b'<option value="all">All</option>', "all") == []
    with pytest.raises(DDSContractError, match="Duplicate"):
        diseases(b'<option value="15">Flu</option><option value="15">Flu</option>', "1")


def test_only_source_offered_closed_single_weeks_are_eligible():
    payload = (FIXTURE / "week_options_15_2026.html").read_bytes()
    assert closed_single_weeks(payload, year=2026, as_of=AS_OF, calendar=CALENDAR) == [46]
    assert week_dates(2026, 46, calendar=CALENDAR) == (date(2026, 11, 8), date(2026, 11, 14))
    for requested in ([47], [45], [46, 46]):
        with pytest.raises(DDSContractError, match="published, closed"):
            closed_single_weeks(payload, year=2026, as_of=AS_OF, requested=requested, calendar=CALENDAR)
    assert closed_single_weeks(b'<option value="1-53">Year</option>',
                               year=2025, as_of=AS_OF, calendar=CALENDAR) == []
    assert closed_single_weeks(payload, year=2026, as_of=date(2026, 11, 14), calendar=CALENDAR) == []


def test_official_calendar_short_year_edges_and_wrong_year_fail_closed():
    assert len(CALENDAR) == 53
    assert week_dates(2026, 1, calendar=CALENDAR) == (date(2026, 1, 1), date(2026, 1, 3))
    assert week_dates(2026, 53, calendar=CALENDAR) == (date(2026, 12, 27), date(2026, 12, 31))
    with pytest.raises(DDSContractError, match="Missing verified"):
        week_dates(2027, 1, calendar=CALENDAR)
    with pytest.raises(DDSContractError, match="Missing verified"):
        week_dates(2027, 1)
    # The workbook also contains a stale 2025 helper worksheet. It lacks an
    # explicit annual publication title and must not certify that year's dates.
    with pytest.raises(DDSContractError, match="unique explicitly titled"):
        parse_calendar_workbook((FIXTURE / "calendar_2026.xlsx").read_bytes(), year=2025)


async def test_new_year_without_official_calendar_has_durable_failure_audit(monkeypatch):
    from src.services.ingestion.countries import th_dds
    captured = []

    class Writer:
        async def write(self, payload, **kwargs):
            captured.append((payload, kwargs))

    crawler = SimpleNamespace(get=lambda url: SimpleNamespace(content=b'<a href="download/rar/outbreak_week_y69">Calendar</a>'),
        session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(th_dds, "ThailandDDSCrawler", lambda: crawler)
    with pytest.raises(DDSContractError, match="calendar unavailable for 2027"):
        await th_dds.collect(Writer(), year=2027, as_of=date(2027, 1, 5))
    audit = next(json.loads(payload) for payload, args in captured
                 if args["metadata"]["endpoint"] == "calendar_failure_audit")
    assert audit["year"] == 2027 and audit["dates_inferred"] is False
    assert "no unique calendar" in audit["error"]


def test_real_weekly_pdf_keeps_calendar_basis_and_national_summary():
    payload = (FIXTURE / "influenza_2026_w46.pdf").read_bytes()
    row = parse(payload, retrieved_at="2026-11-15T06:30:00+07:00")
    assert (row["Cases"], row["Deaths"]) == ("18921", "0")
    assert (row["PeriodStart"], row["PeriodEnd"]) == ("2026-11-08", "2026-11-14")
    assert row["SourcePublishedAt"] == "2026-11-15T06:28:00+07:00"
    assert row["TimeBasis"] == "onset"
    assert row["DefinitionVersion"] == "th_dds_situation_single_week_onset_v1"
    assert row["GeographyKey"] == "country:TH:national"
    assert row["RawSHA256"] == hashlib.sha256(payload).hexdigest()
    assert row["PublicReleaseEnabled"] == "false"


def test_real_explicit_no_case_pdf_does_not_invent_deaths_or_source_timestamp():
    row = parse((FIXTURE / "anthrax_2026_w46.pdf").read_bytes(), ANTHRAX)
    assert (row["Cases"], row["Deaths"], row["SourcePublishedAt"]) == ("0", "", "")
    assert row["ReportStatus"] == "explicit_no_cases"


def test_source_pdf_code_only_label_is_preserved_and_requires_exact_matching_code():
    payload = (FIXTURE / "polio_code20_2026_w46.pdf").read_bytes()
    row = parse(payload, DDSDisease("20", "โปลิโอ", "3"))
    assert row["RawDiseaseLabel"] == "โปลิโอ"
    assert row["ReportDiseaseLabel"] == "รหัส 20"
    assert row["ReportIdentityEvidence"] == "explicit_source_code"
    with pytest.raises(DDSContractError, match="disease"):
        parse(payload, DDSDisease("21", "ไข้หัด", "3"))


@pytest.mark.parametrize("old,new", [
    ("ประเทศไทย", "จังหวัดเชียงใหม่"), ("ไข้หวัดใหญ่", "อหิวาตกโรค"),
    ("เริ่มป่วย", "รายงาน"), ("สัปดาห์ที่ 46", "สัปดาห์ที่ 45"),
    ("8 พฤศจิกายน", "9 พฤศจิกายน"), ("2569", "2568"),
    ("18,921", "18,92"), ("พบผู้ป่วยสะสม", "พบผู้ป่วย"),
])
def test_wrong_disease_geography_basis_interval_calendar_and_counts_fail_closed(monkeypatch, old, new):
    payload = (FIXTURE / "influenza_2026_w46.pdf").read_bytes()
    with pdfplumber.open(io.BytesIO(payload)) as document:
        text = document.pages[0].extract_text()
    assert old in text

    @contextmanager
    def opened(_):
        yield SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text.replace(old, new))])

    monkeypatch.setattr("src.data.crawlers.th_dds.pdfplumber.open", opened)
    with pytest.raises(DDSContractError):
        parse(payload)


def test_sanitized_public_form_never_archives_session_csrf_values():
    payload = b'<meta name="csrf-token" content="secret"><input name="_token" value="secret"><select name="level"><option value="nation">National</option></select>'
    clean = sanitize_form(payload)
    assert b"secret" not in clean
    assert b"nation" in clean


def _artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifacts = []

    def add(payload, endpoint, **metadata):
        path = Path("data/raw/test") / str(len(artifacts))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        item = SimpleNamespace(relative_path=str(path), byte_size=len(payload),
            source_url="https://ddsdoe.ddc.moph.go.th/dds-portal/download/rar/outbreak_week_y69",
            sha256=hashlib.sha256(payload).hexdigest(), metadata_={"endpoint": endpoint, **metadata})
        artifacts.append(item)

    raw_calendar = (FIXTURE / "calendar_2026.rar").read_bytes()
    add(raw_calendar, "calendar_rar", year=2026)
    add((FIXTURE / "calendar_2026.xlsx").read_bytes(), "calendar_workbook", year=2026,
        parent_raw_sha256=hashlib.sha256(raw_calendar).hexdigest())
    catalog = []
    groups = disease_groups((FIXTURE / "national_form.html").read_bytes())
    add((FIXTURE / "national_form.html").read_bytes(), "national_form")
    for group, _ in groups:
        raw = (FIXTURE / f"group_{group}.html").read_bytes()
        add(raw, "disease_group", group=group)
        catalog.extend(vars(d) for d in diseases(raw, group))
    add(b'<option value="2026">2569</option>', "disease_years", disease_code="15")
    add((FIXTURE / "week_options_15_2026.html").read_bytes(), "week_options",
        disease_code="15", year=2026, basis="onset")
    add((FIXTURE / "influenza_2026_w46.pdf").read_bytes(), "weekly_report",
        disease_code="15", year=2026, week=46, basis="onset", retrieved_at="2026-11-15T06:30:00+07:00")
    add(json.dumps({"as_of": "2026-11-15", "year": 2026, "basis": "onset", "catalog": catalog,
        "group_codes": [code for code, _ in groups], "collection_errors": [],
        "requested_reports": [{"disease_code": "15", "year": 2026, "week": 46, "basis": "onset"}],
    }, ensure_ascii=False).encode(), "collection_plan")
    return artifacts


def test_offline_organization_reports_unrepresented_source_categories(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path, monkeypatch)
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: pytest.fail("organize attempted HTTP"))
    result = organize(artifacts, updater=ThailandDDSWeeklyUpdater())
    assert len(result.rows) == 1
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["source_inventory_categories"] == 62
    assert result.source_audit["validated_categories"] == 1
    assert len(result.source_audit["failed_reports"]) == 61
    assert result.source_audit["authoritative_replacement"] is False
    assert ThailandDDSWeeklyUpdater.mapping_notifications_enabled is False


def test_inventory_missing_artifact_and_integrity_fail_closed(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path, monkeypatch)
    with pytest.raises(DDSContractError, match="group artifact"):
        organize([a for a in artifacts if a.metadata_.get("group") != "2"],
                 updater=ThailandDDSWeeklyUpdater())
    report = next(a for a in artifacts if a.metadata_["endpoint"] == "weekly_report")
    Path(report.relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        organize(artifacts, updater=ThailandDDSWeeklyUpdater())
