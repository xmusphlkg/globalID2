from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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
