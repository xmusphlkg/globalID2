"""
NZ Monthly Notifiable Disease PDF Parser

Extracts the national surveillance data table from NZ PHF Science
(formerly ESR) monthly PDF reports.

The PDF reports contain a table with the same structure as the Excel
National workbook:
  - Disease name
  - Current month cases
  - Cumulative total since 1 Jan
  - Current 12-month rate
  - Previous year same month cases
  - Previous year cumulative
  - Previous year rate

This table typically appears on page 7 or later in the PDF, identified
by the header pattern "Current Year - YYYY" and column headers
"Disease", "Cases", "Cumulative", "Rate".
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from src.core import get_logger

logger = get_logger(__name__)

# Footnote markers to strip
_FOOTNOTE_PATTERN = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+$")
_SUPERSCRIPT_PATTERN = re.compile(r"[\u00b9\u00b2\u00b3\u2074-\u2079\u2070]+$")


@dataclass
class NZPdfParseResult:
    """Result of parsing a NZ monthly PDF."""
    rows: List[Dict[str, str]]
    year: int
    month: int
    page_found: Optional[int]
    diseases_parsed: int


def _clean_disease_name(raw: str) -> str:
    """Strip footnote markers and normalize whitespace."""
    cleaned = _FOOTNOTE_PATTERN.sub("", raw).strip()
    cleaned = _SUPERSCRIPT_PATTERN.sub("", cleaned).strip()
    # Remove trailing digits that are footnote refs (e.g. "Gastroenteritis3")
    cleaned = re.sub(r"(\d)$", "", cleaned).strip()
    return " ".join(cleaned.split())


def _safe_int(value: Optional[str]) -> Optional[int]:
    """Parse a string to int, handling commas and whitespace."""
    if value is None:
        return None
    txt = value.strip().replace(",", "").replace(" ", "")
    if not txt or txt == "-" or txt == "–":
        return None
    try:
        return int(float(txt))
    except (ValueError, TypeError):
        return None


def _safe_float(value: Optional[str]) -> Optional[float]:
    """Parse a string to float."""
    if value is None:
        return None
    txt = value.strip().replace(",", "").replace(" ", "")
    if not txt or txt == "-" or txt == "–":
        return None
    try:
        return float(txt)
    except (ValueError, TypeError):
        return None


def _is_national_table_header(row: List[Optional[str]]) -> bool:
    """Check if a row looks like the national table header."""
    if not row or len(row) < 4:
        return False
    text = " ".join(str(cell or "") for cell in row).lower()
    return ("current year" in text or "disease" in text) and (
        "cases" in text or "cumulative" in text or "rate" in text
    )


def _is_disease_row(row: List[Optional[str]]) -> bool:
    """Check if a row contains disease data (name + numeric values)."""
    if not row or len(row) < 4:
        return False
    first_cell = str(row[0] or "").strip()
    if not first_cell:
        return False
    # Skip headers, footnotes, and empty rows
    if first_cell.lower().startswith(("disease", "current year", "previous year", "¹", "²", "³", "note")):
        return False
    if first_cell.startswith(("Other notifiable", "Total")):
        return False
    # Must have at least one numeric value in columns 1-3
    for cell in row[1:4]:
        if _safe_int(str(cell or "")) is not None:
            return True
    return False


def parse_nz_pdf(
    pdf_bytes: bytes,
    year: int,
    month: int,
) -> NZPdfParseResult:
    """
    Parse a NZ monthly notifiable disease PDF report.

    Extracts the national surveillance data table.

    Args:
        pdf_bytes: Raw PDF file content.
        year: Report year.
        month: Report month.

    Returns:
        NZPdfParseResult with extracted disease rows.
    """
    import pdfplumber

    rows: List[Dict[str, str]] = []
    page_found: Optional[int] = None
    report_date = date(year, month, 1)

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # Strategy: find the national table by looking for the characteristic
            # 7-column table with "Current Year" header
            for page_idx, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 5:
                        continue

                    # Check if this is the national summary table
                    # It should have 7 columns and contain "Current Year"
                    if not _looks_like_national_table(table):
                        continue

                    # Found it - parse the disease rows
                    page_found = page_idx + 1
                    parsed = _parse_national_table(table, year, month)
                    if parsed:
                        rows = parsed
                        break

                if rows:
                    break

    except Exception as e:
        logger.error(f"[NZ-PDF] Failed to parse PDF for {year}-{month:02d}: {e}")

    if not rows:
        # Fallback: try to find any table with disease-like data
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                rows, page_found = _fallback_parse(pdf, year, month)
        except Exception as e:
            logger.error(f"[NZ-PDF] Fallback parse also failed for {year}-{month:02d}: {e}")

    logger.info(
        f"[NZ-PDF] Parsed {len(rows)} disease rows from {year}-{month:02d} "
        f"(page {page_found or '?'})"
    )

    return NZPdfParseResult(
        rows=rows,
        year=year,
        month=month,
        page_found=page_found,
        diseases_parsed=len(rows),
    )


def _looks_like_national_table(table: List[List[Optional[str]]]) -> bool:
    """
    Determine if a table is the national surveillance summary.

    Characteristics:
    - 7 columns (or close to it)
    - Contains "Current Year" in first few rows
    - Has disease names in column 0
    """
    if not table:
        return False

    # Check column count (should be ~7 for national table)
    col_counts = [len(row) for row in table if row]
    if not col_counts:
        return False
    typical_cols = max(set(col_counts), key=col_counts.count)
    if typical_cols < 5 or typical_cols > 10:
        return False

    # Check for "Current Year" header in first 3 rows
    header_text = ""
    for row in table[:3]:
        header_text += " ".join(str(cell or "") for cell in row).lower()

    if "current year" not in header_text:
        return False

    # Check that we have disease-like rows
    disease_rows = sum(1 for row in table[2:] if _is_disease_row(row))
    return disease_rows >= 10  # Should have at least 10 diseases


def _parse_national_table(
    table: List[List[Optional[str]]],
    year: int,
    month: int,
) -> List[Dict[str, str]]:
    """Parse the national surveillance table into structured rows."""
    rows: List[Dict[str, str]] = []
    report_date = date(year, month, 1)

    # Find the data start (skip header rows)
    data_start = 0
    for i, row in enumerate(table):
        if row and str(row[0] or "").strip().lower() == "disease":
            data_start = i + 1
            break
        if i >= 3:
            # If no explicit "Disease" header found, start after row 2
            data_start = 2
            break

    if data_start == 0:
        data_start = 2  # Default: skip first 2 header rows

    for row in table[data_start:]:
        if not row or not _is_disease_row(row):
            continue

        disease_raw = str(row[0] or "").strip()
        disease_name = _clean_disease_name(disease_raw)
        if not disease_name:
            continue

        # Columns: Disease | Cases | Cumulative | Rate | PrevCases | PrevCumulative | PrevRate
        cases = _safe_int(row[1] if len(row) > 1 else None)
        cumulative = _safe_int(row[2] if len(row) > 2 else None)
        rate = _safe_float(row[3] if len(row) > 3 else None)
        prev_cases = _safe_int(row[4] if len(row) > 4 else None)
        prev_cumulative = _safe_int(row[5] if len(row) > 5 else None)
        prev_rate = _safe_float(row[6] if len(row) > 6 else None)

        if cases is None and cumulative is None:
            continue

        rows.append({
            "Date": report_date.isoformat(),
            "RawDiseaseLabel": disease_name,
            "Cases": str(cases if cases is not None else 0),
            "CumulativeTotal": str(cumulative) if cumulative is not None else "",
            "Rate": str(rate) if rate is not None else "",
            "PrevYearCases": str(prev_cases) if prev_cases is not None else "",
            "PrevYearCumulative": str(prev_cumulative) if prev_cumulative is not None else "",
            "PrevYearRate": str(prev_rate) if prev_rate is not None else "",
            "Year": str(year),
            "Month": str(month),
            "Source": "NZ PHF Science Monthly Notifiable Disease Surveillance (PDF)",
        })

    return rows


def _fallback_parse(pdf, year: int, month: int) -> Tuple[List[Dict[str, str]], Optional[int]]:
    """
    Fallback parsing strategy: look for any table with known disease names.
    """
    known_diseases = {
        "campylobacteriosis", "cryptosporidiosis", "dengue", "giardiasis",
        "hepatitis", "legionellosis", "leptospirosis", "listeriosis",
        "malaria", "measles", "meningococcal", "mumps", "pertussis",
        "salmonellosis", "shigellosis", "tuberculosis", "typhoid",
        "yersiniosis",
    }

    for page_idx, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 10:
                continue

            # Count how many rows have a known disease name
            disease_count = 0
            for row in table:
                if row and row[0]:
                    name_lower = str(row[0]).strip().lower()
                    if any(d in name_lower for d in known_diseases):
                        disease_count += 1

            if disease_count >= 8:
                # This looks like our table
                parsed = _parse_national_table(table, year, month)
                if parsed:
                    return parsed, page_idx + 1

    return [], None


def parse_nz_xls(
    xls_bytes: bytes,
    year: int,
    month: int,
) -> List[Dict[str, str]]:
    """
    Parse a NZ monthly .xls (old Excel format) national workbook.

    Uses xlrd to read the legacy .xls format. The structure is identical
    to the .xlsx files used from 2020 onwards.
    """
    import xlrd

    rows: List[Dict[str, str]] = []
    report_date = date(year, month, 1)

    try:
        wb = xlrd.open_workbook(file_contents=xls_bytes)
        # Find the national sheet (usually named with "National" or "Nat")
        ws = None
        for name in wb.sheet_names():
            if "national" in name.lower() or "nat" in name.lower():
                ws = wb.sheet_by_name(name)
                break
        if ws is None:
            ws = wb.sheet_by_index(0)

        # Find data start row and detect column offset
        # Some files (e.g. 2018-06) have an empty column 0 with data starting at column 1
        data_start = None
        col_offset = 0
        for row_idx in range(min(ws.nrows, 10)):
            for try_col in (0, 1):
                if try_col >= ws.ncols:
                    continue
                cell_val = ws.cell_value(row_idx, try_col)
                if cell_val and "disease" in str(cell_val).lower():
                    data_start = row_idx + 1
                    col_offset = try_col
                    break
            if data_start is not None:
                break

        if data_start is None:
            data_start = 3  # Default fallback

        for row_idx in range(data_start, ws.nrows):
            disease_raw = ws.cell_value(row_idx, col_offset)
            if not disease_raw or not str(disease_raw).strip():
                continue

            disease_str = str(disease_raw).strip()
            if disease_str.startswith(("¹", "²", "³", "⁴", "⁵", "Other notifiable", "Note")):
                continue

            disease_name = _clean_disease_name(disease_str)
            if not disease_name:
                continue

            # Column layout relative to col_offset
            cases = _safe_int(str(ws.cell_value(row_idx, col_offset + 1)) if ws.ncols > col_offset + 1 else None)
            cumulative = _safe_int(str(ws.cell_value(row_idx, col_offset + 2)) if ws.ncols > col_offset + 2 else None)
            rate = _safe_float(str(ws.cell_value(row_idx, col_offset + 3)) if ws.ncols > col_offset + 3 else None)
            prev_cases = _safe_int(str(ws.cell_value(row_idx, col_offset + 4)) if ws.ncols > col_offset + 4 else None)
            prev_cumulative = _safe_int(str(ws.cell_value(row_idx, col_offset + 5)) if ws.ncols > col_offset + 5 else None)
            prev_rate = _safe_float(str(ws.cell_value(row_idx, col_offset + 6)) if ws.ncols > col_offset + 6 else None)

            if cases is None and cumulative is None:
                continue

            rows.append({
                "Date": report_date.isoformat(),
                "RawDiseaseLabel": disease_name,
                "Cases": str(cases if cases is not None else 0),
                "CumulativeTotal": str(cumulative) if cumulative is not None else "",
                "Rate": str(rate) if rate is not None else "",
                "PrevYearCases": str(prev_cases) if prev_cases is not None else "",
                "PrevYearCumulative": str(prev_cumulative) if prev_cumulative is not None else "",
                "PrevYearRate": str(prev_rate) if prev_rate is not None else "",
                "Year": str(year),
                "Month": str(month),
                "Source": "NZ PHF Science Monthly Notifiable Disease Surveillance",
            })

    except Exception as e:
        logger.error(f"[NZ-XLS] Failed to parse .xls for {year}-{month:02d}: {e}")

    logger.info(f"[NZ-XLS] Parsed {len(rows)} disease rows for {year}-{month:02d}")
    return rows
