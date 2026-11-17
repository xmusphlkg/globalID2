"""Malawi official report contract: exact source week and auditable omissions."""
import json

import pytest

from src.data.crawlers import mw as source
from src.services.ingestion.countries.mw import _associate_media_posts, organize_documents


REPORT = {
    "post_url": "https://phim.health.gov.mw/idsr-epidemiological-bulletin-week-17/",
    "pdf_url": "https://phim.health.gov.mw/wp-content/uploads/2026/05/IDSR-Bulletin_Week-17_Malawi.pdf",
}


def test_discovery_uses_only_official_idrs_post_and_link():
    payload = json.dumps([
        {"id": 1, "title": "IDSR Epidemiological Bulletin – Week 17.", "url": REPORT["post_url"]},
        {"id": 2, "title": "IDSR training", "url": "https://phim.health.gov.mw/training/"},
    ]).encode()
    assert source.discover_posts(payload) == [{"post_id": 1, "title": "IDSR Epidemiological Bulletin – Week 17.", "post_url": REPORT["post_url"]}]
    html = f'<a href="{REPORT["pdf_url"]}">PDF</a><a href="https://other.example/idsr.pdf">Other</a>'.encode()
    assert source.discover_pdf(html, source.discover_posts(payload)[0])["pdf_url"] == REPORT["pdf_url"]


def test_media_discovery_recovers_unlinked_pdfs_and_excludes_districts():
    payload = json.dumps([
        {"id": 3, "date": "2026-06-05T12:00:00", "source_url": REPORT["pdf_url"],
         "title": {"rendered": "IDSR Bulletin Week 17"}},
        {"id": 4, "date": "2025-03-25T12:00:00",
         "source_url": "https://phim.health.gov.mw/wp-content/uploads/2025/03/Lilongwe_IDSR_bulletin_week_12-2025.pdf",
         "title": {"rendered": "Lilongwe IDSR bulletin"}},
        {"id": 5, "date": "2024-12-10T12:00:00",
         "source_url": "https://phim.health.gov.mw/wp-content/uploads/2024/12/IDSR_Bulletin_Week_47.pdf",
         "title": {"rendered": "IDSR Bulletin Week 47"}},
    ]).encode()
    reports = source.discover_media(payload, start_year=2025)
    assert len(reports) == 1
    assert reports[0]["media_id"] == 3
    assert reports[0]["post_url"] == "https://phim.health.gov.mw/wp-json/wp/v2/media/3"


def test_media_replacement_links_to_post_only_after_cover_period_matches(monkeypatch):
    linked = {"post_id": 4, "post_url": "https://phim.health.gov.mw/idsr-week-21/",
              "pdf_url": "https://phim.health.gov.mw/wp-content/uploads/2026/05/IDSR-Bulletin_Week-21_Malawi.pdf"}
    media = {"media_id": 5, "post_url": "https://phim.health.gov.mw/wp-json/wp/v2/media/5",
             "pdf_url": "https://phim.health.gov.mw/wp-content/uploads/2026/06/IDSR-Bulletin_Week-21_Malawi.pdf"}
    _associate_media_posts([linked, media])
    assert media["associated_post_url"] == linked["post_url"]
    monkeypatch.setattr("src.services.ingestion.countries.mw.parse_report_pdf",
                        lambda payload, report: ([{"Date": "2026-05-24", "SourceDiseaseCode": "idsr:cholera",
                                                  "GeographyKey": "country:MW:national", "DefinitionVersion": "v1",
                                                  "Cases": "10", "SourceURL": report["pdf_url"],
                                                  "SourceListingURL": report["post_url"]}],
                                                 {"year": int(payload.decode()), "week": 21}))
    rows, audit = organize_documents([(media, b"2026")], expected_reports=[media], failed_downloads=[])
    assert rows[0]["SourceListingURL"] == linked["post_url"]
    assert audit["reports"][0]["associated_post_url"] == linked["post_url"]
    rows, _ = organize_documents([(media, b"2025")], expected_reports=[media], failed_downloads=[])
    assert rows[0]["SourceListingURL"] == media["post_url"]


def test_table_preserves_suspected_cases_deaths_zeros_and_source_week(monkeypatch):
    cover = "REPUBLIC OF MALAWI\nWEEKLY IDSR EPIDEMIOLOGICAL BULLETIN\nPublished on 12 May, 2026 Epidemiological Week 17 (20 -26 April 2026)"
    table = """Table 1. Reported alerts of diseases and conditions of public health importance in Malawi.
Suspected cases Deaths
EPIDEMIC PRONE DISEASES
Diarrhoea with blood 620 0
Meningococcal meningitis 7 0
Cholera 255 0
Mpox 0 0
"""
    monkeypatch.setattr(source, "extract_pdf_pages", lambda payload: [cover, table])
    rows, detail = source.parse_report_pdf(b"%PDF test", REPORT)
    assert detail["week"] == 17
    assert len(rows) == 8
    by_code = {row["SourceDiseaseCode"]: row for row in rows}
    assert by_code["idsr:cholera:suspected:weekly:cases"]["Cases"] == "255"
    assert by_code["idsr:mpox:suspected:weekly:cases"]["Cases"] == "0"
    assert by_code["idsr:cholera:suspected:weekly:cases"]["Date"] == "2026-04-26"
    assert by_code["idsr:cholera:suspected:weekly:cases"]["PublicReleaseEnabled"] == "false"
    assert by_code["idsr:cholera:suspected:weekly:cases"]["RawSHA256"]


def test_legacy_header_and_split_cell_are_not_misread_as_reversed_counts(monkeypatch):
    cover = "REPUBLIC OF MALAWI\nEpidemiological Week 43 (19th to 25th October 2026)"
    table = """Table 1. Reported alerts of diseases/conditions of public health importance in Malawi.
Suspected Deaths
cases
EPIDEMIC PRONE DISEASES
SARI 0
53
Typhoid Fever 156 1
"""
    monkeypatch.setattr(source, "extract_pdf_pages", lambda payload: [cover, table])
    monkeypatch.setattr(source, "_recover_split_rows", lambda payload, page, lines, existing: ([], lines))
    rows, _ = source.parse_report_pdf(b"%PDF test", REPORT)
    assert len(rows) == 2
    assert {row["RawDiseaseLabel"] for row in rows} == {"Typhoid Fever"}


def test_early_case_only_table_has_no_invented_death_observations(monkeypatch):
    cover = "REPUBLIC OF MALAWI\nEpidemiological Week 7 (10 -16 February 2025)"
    table = """Table 1. Reported alerts of diseases/conditions of public health importance in Malawi, Epi-week 7.
Suspected cases
EPIDEMIC PRONE DISEASES
Cholera 1
Mpox 0
5. Ongoing outbreaks and emergencies
"""
    monkeypatch.setattr(source, "extract_pdf_pages", lambda payload: [cover, table])
    rows, detail = source.parse_report_pdf(b"%PDF test", REPORT)
    assert detail["year"] == 2025
    assert len(rows) == 2
    assert {row["Measure"] for row in rows} == {"cases"}
    assert {row["Cases"] for row in rows} == {"0", "1"}


def test_two_column_section_key_remains_stable_for_existing_history():
    page = """Table 1. Reported alerts of diseases/conditions of public health importance in Malawi.
Suspected cases Deaths
EPIDEMIC PRONE DISEASES
Cholera 1 0
ERADICATION/ELIMINATION
Measles 2 0
CONDITIONS OF PUBLIC HEALTH IMPORTANCE
Rabies 3 0
"""
    _, rows, _ = source._table_rows([page])
    assert rows[-1]["section"] == "eradication/elimination"


def test_split_cell_geometry_reads_columns_instead_of_text_order():
    words = [
        {"text": "Table", "x0": 80, "top": 200},
        {"text": "SARI", "x0": 100, "top": 258},
        {"text": "0", "x0": 492, "top": 258},
        {"text": "53", "x0": 376, "top": 263},
        {"text": "Typhoid", "x0": 100, "top": 276},
        {"text": "156", "x0": 373, "top": 276},
    ]
    assert source._split_table_cell(words, "SARI", 210) == ("53", "0")
    assert source._split_table_cell(words, "Maternal death", 210) is None


def test_conflicting_same_week_editions_are_quarantined(monkeypatch):
    def parse(payload, report):
        return ([{"Date": "2026-04-26", "SourceDiseaseCode": "idsr:cholera:suspected:weekly:cases",
                  "GeographyKey": "country:MW:national", "DefinitionVersion": "v1",
                  "Cases": str(payload[0]), "SourceURL": report["pdf_url"]}], {"year": 2026, "week": 17})
    monkeypatch.setattr("src.services.ingestion.countries.mw.parse_report_pdf", parse)
    second = REPORT | {"pdf_url": REPORT["pdf_url"] + "?edition=2"}
    rows, audit = organize_documents([(REPORT, b"1"), (second, b"2")],
                                      expected_reports=[REPORT, second], failed_downloads=[])
    assert rows == []
    assert len(audit["conflicting_observations"]) == 1


def test_media_upload_date_cannot_override_pdf_cover_year(monkeypatch):
    monkeypatch.setattr("src.services.ingestion.countries.mw.parse_report_pdf",
                        lambda payload, report: ([{"Date": "2024-12-15", "SourceDiseaseCode": "idsr:cholera",
                                                  "GeographyKey": "country:MW:national", "DefinitionVersion": "v1",
                                                  "Cases": "10", "SourceURL": report["pdf_url"]}],
                                                 {"year": 2024, "week": 50}))
    rows, audit = organize_documents([(REPORT, b"pdf")], expected_reports=[REPORT],
                                      failed_downloads=[], start_year=2025)
    assert rows == []
    assert audit["reports"][0]["status"] == "outside_requested_years"


def test_non_national_cover_is_rejected(monkeypatch):
    monkeypatch.setattr(source, "extract_pdf_pages", lambda payload: [
        "Lilongwe district bulletin, Epidemiological Week 17 (20 -26 April 2026)"])
    with pytest.raises(source.MalawiIDSRContractError, match="national Malawi scope"):
        source.parse_report_pdf(b"%PDF test", REPORT)


def test_missing_national_table_fails_without_inventing_zero(monkeypatch):
    monkeypatch.setattr(source, "extract_pdf_pages", lambda payload: ["REPUBLIC OF MALAWI\nEpidemiological Week 17 (20 -26 April 2026)"])
    with pytest.raises(source.MalawiIDSRContractError, match="no supported national disease table"):
        source.parse_report_pdf(b"%PDF test", REPORT)
