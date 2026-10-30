from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.country_library import (
    get_country_display_name,
    get_country_profile,
    validate_standard_country_registry,
)
from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    scope_from_data_source,
    source_options_for_country,
)


def test_nz_country_registration_and_source_scope_are_complete():
    profile = get_country_profile("NZ")

    assert profile.name_en == "New Zealand"
    assert profile.language == "en-NZ"
    assert profile.timezone == "Pacific/Auckland"
    assert get_country_display_name("NZ", "zh") == "新西兰"
    assert not any("NZ" in warning for warning in validate_standard_country_registry())
    assert canonicalize_task_source("all", country_code="NZ") == "phf_monthly"
    assert canonicalize_task_source("nz", country_code="NZ") == "phf_monthly"
    assert scope_from_data_source(
        "NZ PHF Science Monthly Notifiable Disease Surveillance (PDF)"
    ) == "phf_monthly"
    assert canonical_data_source_label(
        "NZ PHF Science Monthly Notifiable Disease Surveillance"
    ) == "New Zealand PHF Science Monthly Notifiable Diseases"
    assert source_options_for_country("NZ") == [
        {
            "value": "phf_monthly",
            "label_en": "New Zealand PHF Science Monthly Notifiable Diseases",
            "label_zh": "新西兰 PHF Science 法定传染病月度监测",
        }
    ]


def test_nz_monthly_zip_prefers_national_pdf_over_rolling(monkeypatch):
    from src.data.crawlers.nz import NewZealandPHFCrawler
    import src.data.parsers.nz_pdf_parser as nz_pdf_parser
    from src.data.parsers.nz_pdf_parser import NZPdfParseResult

    calls: list[str] = []

    def fake_parse_nz_pdf(pdf_bytes: bytes, year: int, month: int) -> NZPdfParseResult:
        selected = pdf_bytes.decode("ascii")
        calls.append(selected)
        cases = "432" if selected == "NAT" else "2870"
        rows = [{
            "Date": f"{year}-{month:02d}-01",
            "RawDiseaseLabel": "Pertussis",
            "Cases": cases,
            "CumulativeTotal": "",
            "Rate": "",
            "Year": str(year),
            "Month": str(month),
            "Source": "NZ PHF Science Monthly Notifiable Disease Surveillance (PDF)",
        }]
        return NZPdfParseResult(
            rows=rows,
            year=year,
            month=month,
            page_found=1,
            diseases_parsed=1,
        )

    monkeypatch.setattr(nz_pdf_parser, "parse_nz_pdf", fake_parse_nz_pdf)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("02Feb/201802_Feb18.docx.pdf", "REPORT")
        zf.writestr("02Feb/201802FebDHB.pdf", "DHB")
        zf.writestr("02Feb/201802FebNat.pdf", "NAT")
        zf.writestr("02Feb/201802FebRolling.pdf", "ROLLING")

    rows = NewZealandPHFCrawler()._parse_zip_content(io.BytesIO(archive.getvalue()), 2018, 2)

    assert calls == ["NAT"]
    assert rows[0]["RawDiseaseLabel"] == "Pertussis"
    assert rows[0]["Cases"] == "432"


def test_nz_national_xlsx_handles_blank_column_before_values():
    from openpyxl import Workbook

    from src.data.crawlers.nz import NewZealandPHFCrawler

    wb = Workbook()
    ws = wb.active
    ws.append([None, "National Notifiable Disease Surveillance Data May 2018"])
    ws.append([None, None, "Current Year - 2018", None, None, "Previous Year - 2017"])
    ws.append([None, None, "May Cases", "Since Jan1", "12 Month Rate", "May Cases"])
    ws.append(["Pertussis", None, 204, 1603, 67.3, 130, 517, 25])

    rows = NewZealandPHFCrawler()._parse_national_sheet(ws, 2018, 5)

    assert rows == [{
        "Date": "2018-05-01",
        "RawDiseaseLabel": "Pertussis",
        "Cases": "204",
        "CumulativeTotal": "1603",
        "Rate": "67.3",
        "PrevYearCases": "130",
        "PrevYearCumulative": "517",
        "PrevYearRate": "25.0",
        "Year": "2018",
        "Month": "5",
        "Source": "NZ PHF Science Monthly Notifiable Disease Surveillance",
    }]


def test_dhb_annual_rows_fall_back_when_raw_github_fails(monkeypatch):
    import csv
    import io
    import requests

    from src.data.crawlers.nz import DHB_JURISDICTIONS, DHB_TOTAL_CSV_MIRRORS, NewZealandPHFCrawler

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "Disease",
            "Report Year",
            "DHBName",
            "Case Count",
            "Case rate per 100,000",
        ],
    )
    writer.writeheader()
    for name in DHB_JURISDICTIONS:
        writer.writerow({
            "Disease": "Pertussis",
            "Report Year": "2024",
            "DHBName": name,
            "Case Count": "3",
            "Case rate per 100,000": "1.2",
        })
    payload = buffer.getvalue()
    calls: list[str] = []

    class Response:
        text = payload
        content = payload.encode("utf-8")

    def fake_get(url, **kwargs):
        del kwargs
        calls.append(url)
        if url == DHB_TOTAL_CSV_MIRRORS[0]:
            raise requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING")
        return Response()

    crawler = NewZealandPHFCrawler()
    monkeypatch.setattr(crawler, "get", fake_get)
    rows, source_url = crawler.fetch_dhb_annual_rows()

    assert calls == list(DHB_TOTAL_CSV_MIRRORS)
    assert source_url == DHB_TOTAL_CSV_MIRRORS[1]
    assert len(rows) == len(DHB_JURISDICTIONS)
    assert rows[0]["SourceURL"] == DHB_TOTAL_CSV_MIRRORS[1]
    assert rows[0]["Cases"] == "3"
