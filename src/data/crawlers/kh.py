"""Cambodia CDC CamEWARN: linked archives, exact tables and honest chart gaps.

The source calendar is read from each report. Earlier reports contain complete
weekly matrices; recent reports usually contain raster charts. Chart heights,
report-completeness counts and unlabelled calendar gaps are never case counts.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from threading import Lock
import zipfile
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from dateutil.parser import parse as parse_date

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "kh_camewarn_weekly"
DEFAULT_SOURCE_NAME = "Cambodia CDC CamEWARN Weekly Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_KH_CAMEWARN"
INDEX_URL = "https://cdcmoh.gov.kh/surveillance/camewarn"
HISTORY_START_YEAR = 2015
DEFINITION_VERSION = "kh_camewarn_source_calendar_syndromic_v1"
_PDFIUM_LOCK = Lock()  # PDFium is not thread-safe; table parsing is independent.
CHART_LABELS = (
    "ACUTE DIARRHEA", "FEVER WITH RASH", "ACUTE FLACCID PARALYSIS",
    "SEVERE RESPIRATORY INFECTION", "SUSPECT DENGUE FEVER",
    "ACUTE HEMORRHAGIC FEVER", "MENINGITIS OR ENCEPHALITIS", "ACUTE JAUNDICE",
    "DIPHTHERIA", "RABIES", "NEONATAL TETANUS",
)


class CambodiaContractError(ValueError):
    """Source period, table or archive cannot be verified."""


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.href = None; self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href"); self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.parts).strip())); self.href = None


def discover_reports(payload: bytes, *, start_year=HISTORY_START_YEAR, end_year=None):
    """Return only published archive/PDF links, retaining erroneous label aliases."""
    parser = _Links(); parser.feed(payload.decode("utf-8", errors="strict"))
    targets = {}
    for href, label in parser.links:
        url = urljoin(INDEX_URL, unescape(href))
        if urlparse(url).hostname != "cdcmoh.gov.kh" or "/Camewarn/Weekly_reports/" not in url:
            continue
        if not re.search(r"\.(pdf|zip|rar)$", url, re.I):
            continue
        years = re.findall(r"20\d{2}", url)
        if not years:
            raise CambodiaContractError("Published CamEWARN link has no source year")
        year = int(years[-1])
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        entry = targets.setdefault(url, {"url": url, "year": year,
            "format": url.rsplit(".", 1)[-1].lower(), "labels": []})
        if label not in entry["labels"]:
            entry["labels"].append(label)
    if not targets:
        raise CambodiaContractError("No linked CamEWARN reports in requested year range")
    return sorted(targets.values(), key=lambda x: (x["year"], x["url"]))


def archive_members(payload: bytes, *, kind: str):
    """Read PDF bytes without extracting archive paths to the filesystem."""
    if kind == "pdf":
        if not payload.startswith(b"%PDF"):
            raise CambodiaContractError("Published PDF did not return PDF bytes")
        return [("", payload)]
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            result = []
            for entry in archive.infolist():
                if not entry.filename.lower().endswith(".pdf"):
                    continue
                if entry.file_size > 40_000_000:
                    raise CambodiaContractError("Unexpectedly large CamEWARN member")
                result.append((entry.filename, archive.read(entry)))
            if not result:
                raise CambodiaContractError("Published archive contains no PDFs")
            return result
    if kind == "rar":
        # RAR is an upstream format. Never silently treat an unreadable archive
        # as an empty year; the audit explicitly records unavailable decoders.
        command = shutil.which("bsdtar")
        if not command:
            raise CambodiaContractError("RAR archive preserved; bsdtar is required to decode its PDF members")
        with tempfile.TemporaryDirectory(prefix="kh-camewarn-") as directory:
            path = Path(directory) / "reports.rar"; path.write_bytes(payload)
            listing = subprocess.run([command, "-tf", str(path)], check=True, capture_output=True, text=True)
            result = []
            for name in listing.stdout.splitlines():
                if name.lower().endswith(".pdf"):
                    content = subprocess.run([command, "-xOf", str(path), "--", name], check=True, capture_output=True).stdout
                    if len(content) > 40_000_000:
                        raise CambodiaContractError("Unexpectedly large CamEWARN RAR member")
                    result.append((name, content))
            if not result:
                raise CambodiaContractError("Published RAR contains no PDFs")
            return result
    raise CambodiaContractError(f"Unsupported CamEWARN format: {kind}")


def report_period(text: str, *, expected_year: int):
    match = re.search(r"\bWeek\s*(\d{1,2})(?:\s*[-–]\s*(20\d{2}))?\s*\(([^)]+)\)", text, re.I)
    if not match:
        raise CambodiaContractError("No explicit source reporting week/date interval")
    week = int(match[1]); stated_year = int(match[2]) if match[2] else expected_year
    if stated_year != expected_year or not 1 <= week <= 53:
        raise CambodiaContractError("Header year/week conflicts with published report identity")
    interval = re.sub(r"\s+", " ", match[3]).strip()
    parts = re.split(r"\s*[-–—]\s*", interval, maxsplit=1)
    if len(parts) != 2:
        raise CambodiaContractError("Invalid explicit reporting date interval")
    try:
        end = parse_date(parts[1], dayfirst=True, fuzzy=True, default=datetime(expected_year, 1, 1)).date()
        start = parse_date(parts[0], dayfirst=True, fuzzy=True, default=datetime(end.year, end.month, 1)).date()
        if start > end and not re.search(r"20\d{2}", parts[0]):
            start = start.replace(year=start.year - 1)
    except (ValueError, OverflowError) as exc:
        raise CambodiaContractError("Invalid source calendar dates") from exc
    if (end - start).days != 6 or start.year not in {expected_year - 1, expected_year} or end.year not in {expected_year, expected_year + 1}:
        raise CambodiaContractError(f"Source week is not an explicit seven-day interval: {interval}")
    return week, start, end


def _code(label):
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _label(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().rstrip(" (*†‡").strip()


def _count(value):
    value = re.sub(r"\s+", "", str(value or ""))
    if value in {"", "-", "–", "…", "*"}:
        return ""
    if not re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", value):
        raise CambodiaContractError(f"Invalid weekly table count {value!r}")
    return str(int(value.replace(",", "")))


def _row(label, value, *, report_year, target_week, report_week, start, end, source_url, digest, member, extraction):
    code = _code(label)
    return {
        "Date": start.isoformat(), "Year": str(start.year), "Month": str(start.month),
        "Week": str(target_week), "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
        "DiseaseCode": "__source_native__", "SourceDiseaseCode": code, "RawDiseaseLabel": label,
        "Cases": value, "MissingValueStatus": "source_missing" if value == "" else "",
        "Frequency": "weekly", "PeriodType": "weekly", "Measure": "cases",
        "ReportingBasis": "syndromic_notification", "DefinitionVersion": DEFINITION_VERSION,
        "Geography": "Cambodia", "GeographyKey": "country:KH:national",
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": source_url, "RawSHA256": digest,
        "SourceReportYear": str(report_year), "SourceReportWeek": str(report_week),
        "SourceWeek": str(target_week), "ArchiveMember": member,
        "DatasetStatus": "provisional_revisable", "IsProvisional": "true",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "PublicReleaseEnabled": "false", "License": "unverified",
        "Dimensions": json.dumps({"source_calendar": "report_explicit_interval",
            "source_reporting_year": report_year, "source_reporting_week": target_week,
            "extraction": extraction, "chart_only": extraction == "chart_only",
            "population_scope": "reporting_health_facilities", "source_case_definition": label}, sort_keys=True),
    }


def parse_report_pdf(payload: bytes, *, source_url: str, expected_year: int, member=""):
    """Exact weekly matrix cells; chart-only values remain explicitly unknown."""
    import pdfplumber
    if not payload.startswith(b"%PDF"):
        raise CambodiaContractError("Report is not a PDF")
    digest = hashlib.sha256(payload).hexdigest()
    rows = {}; found = set(); tables_count = 0
    import pypdfium2
    with _PDFIUM_LOCK, pypdfium2.PdfDocument(payload) as document:
        texts = []
        for page in document:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range().replace("\r\n", "\n"))
            textpage.close(); page.close()
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        text = "\n".join(texts)
        if not re.search(r"CAMEWARN|Weekly Surveillance Report", text, re.I):
            raise CambodiaContractError("Report does not identify CamEWARN surveillance")
        report_week, period_start, period_end = report_period(texts[0], expected_year=expected_year)
        calendar_start = period_start - timedelta(weeks=report_week - 1)
        for page, page_text in zip(pdf.pages, texts):
            # These exact tables are case counts. Completeness/timeliness and
            # per-facility subtables do not satisfy this national matrix header.
            if not re.search(r"Number of cases reported by CAMEWARN in Cambodia", page_text, re.I):
                continue
            table_years = re.findall(r"Weeks?\s+[^\n]*?\b(20\d{2})\b", page_text, re.I)
            if not table_years or any(int(year) != expected_year for year in table_years):
                raise CambodiaContractError("National weekly matrix year conflicts with source report")
            for table in page.extract_tables():
                if len(table) < 3 or _label(table[0][0]).lower() != "disease":
                    continue
                header = table[1]
                week_columns = [(i, int(str(cell))) for i, cell in enumerate(header)
                                if cell is not None and re.fullmatch(r"\d{1,2}", str(cell).strip())]
                if not week_columns or any(not 1 <= week <= 53 for _, week in week_columns):
                    raise CambodiaContractError("National matrix lacks source week columns")
                tables_count += 1
                for cells in table[2:]:
                    label = _label(cells[0]); code = _code(label)
                    if not label or not re.search(r"[A-Za-z]", label):
                        raise CambodiaContractError("Unidentified disease row in national weekly matrix")
                    found.add(code)
                    for i, week in week_columns:
                        if week > report_week:
                            continue  # Unpublished future cells are not unknown observations.
                        if i >= len(cells):
                            raise CambodiaContractError("Truncated national matrix row")
                        value = _count(cells[i]); start = calendar_start + timedelta(weeks=week - 1)
                        key = (week, code)
                        row = _row(label, value, report_year=expected_year, target_week=week,
                            report_week=report_week, start=start, end=start + timedelta(days=6),
                            source_url=source_url, digest=digest, member=member, extraction="national_weekly_matrix")
                        if key in rows and rows[key]["Cases"] != value:
                            raise CambodiaContractError("Conflicting duplicate national matrix cells")
                        rows[key] = row
        if not tables_count:
            # Presence of an explicit disease heading establishes a reported
            # category, never its plotted magnitude. Do not create absent weeks.
            upper = text.upper()
            for label in CHART_LABELS:
                if label not in upper:
                    continue
                # Older reports contain case-definition mentions for categories
                # absent from their charts. Require a real heading line.
                pattern = r"(?:^|\n)\s*(?:\d+[.)]\s*)?\*?" + re.escape(label) + r"\s*(?:\n|$)"
                chart_line = any(label in line and re.fullmatch(r"[A-Z *]+", line.strip())
                                 for line in text.splitlines())
                if not re.search(pattern, upper) and not chart_line:
                    continue
                found.add(_code(label))
                rows[(report_week, _code(label))] = _row(label.title(), "", report_year=expected_year,
                    target_week=report_week, report_week=report_week, start=period_start, end=period_end,
                    source_url=source_url, digest=digest, member=member, extraction="chart_only")
        if not rows:
            raise CambodiaContractError("No verifiable national disease table or chart headings")
    return list(rows.values()), {"report_year": expected_year, "report_week": report_week,
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "categories": sorted(found), "national_matrix_tables": tables_count,
        "rows": len(rows), "observed_values": sum(row["Cases"] != "" for row in rows.values()),
        "member": member, "source_url": source_url, "raw_sha256": digest}


def merge_report_rows(reports):
    """Latest available reporting week revises earlier cells; ties must agree."""
    selected = {}; conflicts = []
    for report_rows in reports:
        for row in report_rows:
            key = (row["SourceReportYear"], row["SourceWeek"], row["SourceDiseaseCode"])
            previous = selected.get(key)
            if previous:
                a, b = int(previous["SourceReportWeek"]), int(row["SourceReportWeek"])
                if b < a:
                    continue
                if b == a and (previous["Cases"], previous["Date"]) != (row["Cases"], row["Date"]):
                    conflicts.append({"key": key, "first_url": previous["SourceURL"], "second_url": row["SourceURL"]})
                    continue
            selected[key] = row
    if conflicts:
        raise CambodiaContractError("Conflicting same-edition CamEWARN reports: " + json.dumps(conflicts))
    return sorted(selected.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"]))


class CambodiaCamEWARNCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=120, delay=0.2, **kwargs)

    async def crawl(self, **kwargs):
        response = await asyncio.to_thread(self.get, INDEX_URL)
        return self.parse(response)

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=INDEX_URL,
            raw_data={"reports": discover_reports(response.content)})]
