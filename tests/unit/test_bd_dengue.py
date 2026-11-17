"""Real DGHS PDFs: hospital admission semantics and deterministic staged replay."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest
import requests

from src.data.crawlers.bd_dengue import (
    DEFAULT_SCOPE, discover_reports, extract_summary_text, parse_report, parse_summary_text, parse_dashboard_text,
)
from src.data.processors.bd_dengue import BangladeshDGHSDengueUpdater
from src.services.ingestion.countries.bd_dengue import collect, organize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/BD"
PDF = (FIXTURES / "dghs_dengue_2026-11-15.pdf").read_bytes()
LISTING = (FIXTURES / "dghs_dengue_directory.html").read_bytes()


def test_real_dghs_pdf_retains_daily_hospital_measures_and_cumulative_intervals():
    rows, audit = parse_report(PDF, report_date="2026-11-15", as_of=date(2026, 11, 17))
    assert len(rows) == 36
    national = {r["SourceDiseaseCode"]: r for r in rows if r["GeographyKey"] == "country:BD:national"}
    admissions = national["dengue__daily_admissions"]
    assert admissions["Cases"] == "1952"
    assert admissions["Measure"] == "hospital_admissions"
    assert admissions["PeriodType"] == "daily"
    assert national["dengue__daily_deaths"]["Cases"] == "8"
    assert national["dengue__cumulative_admissions"]["Cases"] == "162509"
    assert national["dengue__cumulative_admissions"]["PeriodStart"] == "2026-01-01"
    assert national["dengue__cumulative_admissions"]["PeriodType"] == "cumulative"
    assert national["dengue__cumulative_deaths"]["Cases"] == "499"
    assert len({r["GeographyKey"] for r in rows}) == 9
    assert sum(int(r["Cases"]) for r in rows if r["GeographyKey"] != "country:BD:national" and r["SourceDiseaseCode"] == "dengue__daily_admissions") == 1952
    assert any(r["Cases"] == "0" and r["Measure"] == "deaths" for r in rows)
    assert all(r["RawSHA256"] == hashlib.sha256(PDF).hexdigest() for r in rows)
    assert audit["incidence_inferred"] is False


@pytest.mark.parametrize("mutation", ["date", "region", "daily_count", "balance", "attribution", "columns", "missing_division"])
def test_changed_or_incomplete_pdf_contract_is_rejected(mutation):
    text = extract_summary_text(PDF)
    if mutation == "date":
        text = text.replace("15-11-26", "14-11-26")
    elif mutation == "region":
        text = text.replace("ঢাকা রর্ভাগ", "অজানা রর্ভাগ")
    elif mutation == "daily_count":
        text = text.replace("৮৭৩ ৫", "৮৭৪ ৫")
    elif mutation == "balance":
        text = text.replace("৬৩৯৪১ ২৫৭০", "৬৩৯৪০ ২৫৭১").replace("১৫৬১৯৪ ৫৮১৬", "১৫৬১৯৩ ৫৮১৬")
    elif mutation == "attribution":
        text = text.replace("controlroomdghs@yahoo.com", "other@example.com")
    elif mutation == "columns":
        text = text.replace("৮৭৩ ৫", "৮৭৩ —")
    elif mutation == "missing_division":
        text = "\n".join(line for line in text.splitlines() if not line.startswith("৮ "))
    with pytest.raises(ValueError):
        parse_summary_text(text, report_date="2026-11-15")


def test_pdf_future_date_and_mismatched_title_are_rejected():
    with pytest.raises(ValueError, match="retrieval"):
        parse_report(PDF, report_date="2026-11-15", as_of=date(2026, 11, 14))
    with pytest.raises(ValueError, match="differs"):
        parse_report(PDF, report_date="2026-11-14")


def test_real_directory_returns_bengali_dated_publication_links():
    reports, pages = discover_reports(LISTING)
    assert len(reports) == 10 and pages == 8
    assert reports[0]["date"] == "2026-11-15"
    assert "/office-dghs/" in reports[0]["url"]


@pytest.mark.parametrize("mutation", ["foreign_host", "ambiguous_pdf", "wrong_date", "wrong_category"])
def test_directory_rejects_unreviewed_publications(mutation):
    soup = BeautifulSoup(LISTING, "lxml")
    tr = soup.select('table#noticeTable tbody tr')[1]
    link = tr.select_one('td[data-column="files"] a')
    if mutation == "foreign_host":
        link["href"] = "https://example.com/a.pdf"
    elif mutation == "ambiguous_pdf":
        link.insert_after(BeautifulSoup(str(link), "lxml").find("a"))
    elif mutation == "wrong_date":
        tr.select_one('td[data-column="publish_date"]').string = "১৪-১১-২০২৬"
    else:
        tr.select_one('td[data-column="title"]').string = "COVID-19 (১৫/১১/২০২৬)"
    with pytest.raises(ValueError):
        discover_reports(str(soup).encode())


async def test_collect_then_offline_replay_enforces_hashes_and_directory_linkage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Writer:
        artifacts = None
        def __init__(self):
            self.artifacts = []
        async def write(self, payload, *, source_url, kind, metadata):
            path = Path("data/raw/bd") / str(len(self.artifacts))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.artifacts.append(SimpleNamespace(source_url=source_url, kind=kind, metadata_=metadata,
                relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest()))

    class Crawler:
        def __init__(self):
            self.session = SimpleNamespace(close=lambda: None)
        def get(self, url):
            return SimpleNamespace(content=PDF if url.endswith(".pdf") else LISTING)

    writer = Writer()
    await collect(writer, crawler_type=Crawler, max_reports=1)
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: pytest.fail("organize made network request"))
    updater = BangladeshDGHSDengueUpdater()
    result = organize(writer.artifacts, updater=updater)
    assert len(result.rows) == 36
    assert result.source_latest_date == date(2026, 11, 15)
    assert result.source_audit["coverage_complete"] is False
    assert result.source_audit["failed_downloads"] == []
    assert result.source_audit["parse_failures"] == []
    assert updater.series_geography_key is None and updater.series_geography_from_rows
    assert not updater.public_release_enabled and not updater.mapping_notifications_enabled
    pdf_artifact = next(a for a in writer.artifacts if a.kind == "cda_pdf")
    Path(pdf_artifact.relative_path).write_bytes(b"altered")
    with pytest.raises(ValueError, match="integrity"):
        organize(writer.artifacts, updater=updater)


def test_updater_filters_by_real_daily_observation_window():
    rows, _ = parse_report(PDF, report_date="2026-11-15")
    updater = BangladeshDGHSDengueUpdater()
    assert len(updater.prepare_rows(rows, start_year=2026, months=[(2026, 11)]).rows) == 36
    with pytest.raises(ValueError, match="requested window"):
        updater.prepare_rows(rows, months=[(2026, 10)])
    with pytest.raises(ValueError, match="Unsupported"):
        updater.prepare_rows(rows, source="iedcr_nisb")


def test_english_dashboard_retains_explicit_city_corporation_and_division_counts():
    text = (FIXTURES / "dghs_dashboard_2026-10-10.txt").read_text()
    rows, audit = parse_dashboard_text(text, report_date="2026-10-10")
    assert len(rows) == 12
    assert rows[0]["Cases"] == "1631" and rows[0]["Measure"] == "hospital_admissions"
    assert rows[1]["Cases"] == "4" and rows[1]["Measure"] == "deaths"
    assert sum(int(row["Cases"]) for row in rows[2:]) == 1631
    assert next(r["Cases"] for r in rows if r["GeographyKey"] == "country:BD:source_region:dncc") == "92"
    assert next(r["Cases"] for r in rows if r["GeographyKey"] == "country:BD:source_region:dscc") == "406"
    assert not any(r["GeographyKey"] == "country:BD:subdivision:BD-C" for r in rows)
    assert audit["regional_status"] == "ten_explicit_categories_verified_against_national"
    assert {r["PeriodType"] for r in rows} == {"daily"}


def test_english_dashboard_missing_region_categories_are_unknown_and_explicit_national_zero_is_retained():
    text = (FIXTURES / "dghs_dashboard_2026-09-04.txt").read_text()
    rows, audit = parse_dashboard_text(text, report_date="2026-09-04")
    assert len(rows) == 2
    assert rows[0]["Cases"] == "512" and rows[1]["Cases"] == "0"
    assert {r["GeographyKey"] for r in rows} == {"country:BD:national"}
    assert audit["regional_status"] == "partial_category_labels_not_imported"


@pytest.mark.parametrize("mutation", ["national_date", "national_value", "regional_sum", "regional_identity", "source_identity"])
def test_english_dashboard_ambiguous_or_changed_labels_are_rejected(mutation):
    text = (FIXTURES / "dghs_dashboard_2026-10-10.txt").read_text()
    if mutation == "national_date":
        text = text.replace("10-Oct-2026", "11-Oct-2026")
    elif mutation == "national_value":
        text = text.replace("1,631\nAdmitted", "—\nAdmitted")
    elif mutation == "regional_sum":
        text = text.replace("219\n234", "220\n234")
    elif mutation == "regional_identity":
        text = text.replace("DNCC DSCC", "DSCC DNCC")
    else:
        text = text.replace("Directorate General of Health Services (DGHS)", "Unknown agency")
    with pytest.raises(ValueError):
        parse_dashboard_text(text, report_date="2026-10-10")


@pytest.mark.parametrize("day,expected_admissions", [("2026-11-07", "1674"), ("2026-10-26", "2218")])
def test_reviewed_embedded_font_variant_and_labelled_national_total(day, expected_admissions):
    text = (FIXTURES / f"dghs_summary_{day}.txt").read_text()
    rows, _ = parse_summary_text(text, report_date=day)
    assert len(rows) == 36
    assert rows[0]["Cases"] == expected_admissions


def test_adjacent_regional_pdf_numeric_labels_can_share_a_text_line():
    text = (FIXTURES / "dghs_dashboard_2026-10-10.txt").read_text().replace("219\n234", "219 234")
    rows, audit = parse_dashboard_text(text, report_date="2026-10-10")
    assert len(rows) == 12 and audit["national_daily_admissions"] == 1631


def test_duplicate_comma_requires_both_explicit_regional_and_age_sex_totals():
    text = (FIXTURES / "dghs_dashboard_2026-09-30.txt").read_text()
    rows, audit = parse_dashboard_text(text, report_date="2026-09-30")
    assert rows[0]["Cases"] == "1948"
    assert rows[0]["SourceNationalAdmissionLabel"] == "1,,948"
    assert rows[0]["ExtractionRepair"] == "duplicate_comma_confirmed_by_regional_and_age_sex_totals"
    assert audit["national_label_repaired"] is True
    for wrong in [text.replace("212\n343", "213\n343"), text.replace("Grand Total 1,199 749 1,948", "Grand Total 1,199 749 1,949")]:
        with pytest.raises(ValueError):
            parse_dashboard_text(wrong, report_date="2026-09-30")
