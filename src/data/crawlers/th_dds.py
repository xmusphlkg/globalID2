"""Public DDS situation reports: every published category, closed single weeks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup
import pdfplumber
from openpyxl import load_workbook

from .base import BaseCrawler

DEFAULT_SCOPE = "th_dds_weekly"
DEFAULT_SOURCE_NAME = "Thailand DDS Weekly National Situation"
ONTOLOGY_SOURCE_ID = "SRC_TH_DDS_WEEKLY"
BASE_URL = "https://doe.moph.go.th/dds-report/"
FORM_URL = BASE_URL + "situation/create"
NATION_URL = BASE_URL + "situation/nation/create"
STORE_URL = BASE_URL + "situation/store"
CALENDAR_INDEX_URL = "https://ddsdoe.ddc.moph.go.th/dds-portal/"
THAI_MONTHS = {name: index + 1 for index, name in enumerate((
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
))}


class DDSContractError(ValueError):
    """A DDS public report cannot be interpreted under the reviewed contract."""


@dataclass(frozen=True)
class DDSDisease:
    code: str
    label: str
    group: str


def options(payload):
    result = []
    for node in BeautifulSoup(payload, "html.parser").select("option[value]"):
        value = node["value"].strip()
        if not value or value == "all":
            continue
        result.append((value, " ".join(node.get_text(" ", strip=True).split())))
    if len({key for key, _ in result}) != len(result):
        raise DDSContractError("Duplicate public form option")
    return result


def disease_groups(payload):
    soup = BeautifulSoup(payload, "html.parser")
    selected = soup.select_one('select[name="f_dz_gr"]')
    if selected is None:
        raise DDSContractError("Public situation disease-group selector missing")
    groups = options(str(selected))
    if not groups or any(not code.isdigit() for code, _ in groups):
        raise DDSContractError("Invalid public disease-group inventory")
    return groups


def diseases(payload, group):
    result = []
    for code, label in options(payload):
        if not re.fullmatch(r"\d{2}", code) or not label:
            raise DDSContractError("Invalid DDS source disease code/label")
        result.append(DDSDisease(code, label, str(group)))
    return result


def extract_calendar_workbook(payload):
    """Extract the official single-workbook RAR without publishing archive paths."""
    executable = shutil.which("unrar-free")
    if not executable:
        raise DDSContractError("DDS official calendar requires the unrar-free system package; no dates were inferred")
    if not payload.startswith(b"Rar!") or len(payload) > 10_000_000:
        raise DDSContractError("Invalid official DDS calendar RAR")
    with tempfile.TemporaryDirectory(prefix="th-dds-calendar-") as directory:
        path = Path(directory) / "calendar.rar"
        path.write_bytes(payload)
        try:
            result = subprocess.run([executable, "-P", "-i", "/dev/null", str(path)],
                check=True, capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            raise DDSContractError("Official DDS calendar RAR extraction failed") from exc
    if not result.stdout.startswith(b"PK") or len(result.stdout) > 20_000_000:
        raise DDSContractError("Official DDS calendar did not yield one XLSX workbook")
    return result.stdout


def parse_calendar_workbook(payload, *, year):
    """Read the explicitly titled annual table; ignore stale helper worksheets."""
    year = int(year)
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:
        raise DDSContractError("Official DDS calendar XLSX is unreadable") from exc
    try:
        sheets = [sheet for sheet in workbook if re.search(
            rf"ปี\s*{year + 543}(?:\D|$)", str(sheet.cell(1, 1).value))]
        if len(sheets) != 1:
            raise DDSContractError(f"Official DDS calendar has no unique explicitly titled year {year}")
        weeks = {}
        for values in sheets[0].iter_rows(values_only=True):
            for offset in (0, 5):
                if len(values) <= offset + 3 or not isinstance(values[offset], int):
                    continue
                number, start, end = values[offset], values[offset + 1], values[offset + 3]
                if not isinstance(start, datetime) or not isinstance(end, datetime) or number in weeks:
                    raise DDSContractError("Official DDS calendar contains invalid or duplicate boundaries")
                weeks[number] = (start.date(), end.date())
        ordered = sorted(weeks)
        if not ordered or ordered != list(range(1, len(ordered) + 1)):
            raise DDSContractError("Official DDS calendar has missing weeks")
        if weeks[1][0] != date(year, 1, 1) or weeks[ordered[-1]][1] != date(year, 12, 31):
            raise DDSContractError("Official DDS calendar does not cover the exact source year")
        previous = None
        for number in ordered:
            start, end = weeks[number]
            if (start.year != year or end.year != year or start > end or (end - start).days > 6
                or (previous and start != previous + timedelta(days=1))
                or (number != 1 and start.weekday() != 6)
                or (number != ordered[-1] and end.weekday() != 5)):
                raise DDSContractError("Official DDS calendar boundaries are inconsistent")
            previous = end
        return weeks
    finally:
        workbook.close()


def week_dates(year, week, *, calendar=None):
    """Use captured official annual boundaries, including short year-edge weeks."""
    try:
        start, end = calendar[int(week)]
        if start.year != int(year) or end.year != int(year):
            raise KeyError(week)
        return start, end
    except (KeyError, TypeError, ValueError) as exc:
        raise DDSContractError(f"Missing verified official DDS calendar for year {year}, week {week}; dates were not inferred") from exc


def closed_single_weeks(payload, *, year, as_of, requested=None, calendar=None):
    published = []
    for value, _ in options(payload):
        match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", value)
        if not match:
            raise DDSContractError("Unrecognized published DDS reporting interval")
        first, last = map(int, match.groups())
        if first == last:
            if week_dates(year, first, calendar=calendar)[1] < as_of:
                published.append(first)
    if requested is None:
        return [max(published)] if published else []
    wanted = [int(week) for week in requested]
    if len(wanted) != len(set(wanted)) or any(week not in published for week in wanted):
        raise DDSContractError("Requested week is not a published, closed single-week option")
    return sorted(wanted)


def sanitize_form(payload):
    """Archive public form structure without transient CSRF/session values."""
    soup = BeautifulSoup(payload, "html.parser")
    for node in soup.select('input[name="_token"], meta[name="csrf-token"]'):
        node.decompose()
    return str(soup).encode("utf-8")


def parse_weekly_pdf(payload, *, disease, year, week, basis="onset", as_of=None,
                     retrieved_at="", calendar=None):
    if basis not in {"onset", "gen"}:
        raise DDSContractError("DDS basis must be onset or gen")
    if not payload.startswith(b"%PDF"):
        raise DDSContractError("Expected a public DDS PDF report")
    start, end = week_dates(year, week, calendar=calendar)
    if as_of is not None and end >= as_of:
        raise DDSContractError("The source epidemiological week is not closed")
    with pdfplumber.open(io.BytesIO(payload)) as document:
        if not document.pages:
            raise DDSContractError("Empty DDS report")
        text = document.pages[0].extract_text() or ""
    lines = text.splitlines()
    compact = lambda value: re.sub(r"\s+", "", value)
    # Code 20's live no-case PDF uses “รหัส 20” instead of the form's
    # disease label. Accept only the exact explicit source code, retaining
    # this discrepancy as evidence; never infer identity from a count alone.
    report_label = next((label for label in (disease.label, f"รหัส {disease.code}")
        if lines and compact(lines[0]) == compact(f"สถานการณ์โรค{label} ประเทศไทย")), None)
    if report_label is None:
        raise DDSContractError("PDF source disease or national geography does not match the request")
    basis_text = "ข้อมูลตามวันและสถานที่เริ่มป่วย" if basis == "onset" else "ข้อมูลตามวันและสถานที่รายงาน"
    if basis_text not in text:
        raise DDSContractError("PDF onset/report-date basis does not match the request")
    buddhist = int(year) + 543
    zero_pattern = rf"ไม่พบผู้ป่วยโรค{re.escape(report_label)}\s+ในประเทศไทย\s+ช่วงสัปดาห์ที่\s+(\d+)\s+ถึง\s+(\d+)\s+ปี\s+(\d+)"
    zero = re.search(zero_pattern, text)
    published_at = ""
    if zero:
        if tuple(map(int, zero.groups())) != (int(week), int(week), buddhist):
            raise DDSContractError("Zero report covers a different source interval")
        cases, deaths = "0", ""  # no-case narrative does not report a death metric
        report_status = "explicit_no_cases"
    else:
        heading = re.search(r"สัปดาห์ที่\s+(\d+)\s+ปี\s+พ\.ศ\.\s+(\d+)\s*\((\d+)\s+(\S+)\s+(\d+)\s*[–-]\s*(\d+)\s+(\S+)\s+(\d+)\)", text)
        if not heading:
            raise DDSContractError("Single-week heading with explicit calendar dates is missing")
        number, report_year, day1, month1, year1, day2, month2, year2 = heading.groups()
        try:
            actual_start = date(int(year1) - 543, THAI_MONTHS[month1], int(day1))
            actual_end = date(int(year2) - 543, THAI_MONTHS[month2], int(day2))
        except (KeyError, ValueError) as exc:
            raise DDSContractError("Invalid Thai-calendar reporting dates") from exc
        if (int(number), int(report_year), actual_start, actual_end) != (int(week), buddhist, start, end):
            raise DDSContractError("PDF source epidemiological week/calendar boundaries changed")
        summary = re.search(r"พบผู้ป่วยสะสม\s+([\d,]+)\s+ราย\s+อัตราป่วย\s+[\d.]+\s+ต่อแสนประชากร\s+เสียชีวิต\s+([\d,]+)\s+ราย", text)
        if not summary:
            raise DDSContractError("Explicit national case/death summary is missing")
        for value in summary.groups():
            if not re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", value):
                raise DDSContractError("Invalid national notification count")
        cases, deaths = (str(int(value.replace(",", ""))) for value in summary.groups())
        stamp = re.search(r"ข้อมูล ณ วันที่\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+เวลา\s+(\d{2}:\d{2})", text)
        if not stamp:
            raise DDSContractError("DDS data-as-of date missing")
        day, month, publication_year, clock = stamp.groups()
        published_at = date(int(publication_year) - 543, int(month), int(day)).isoformat() + f"T{clock}:00+07:00"
        report_status = "reported_counts"
    return {
        "Date": end.isoformat(), "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
        "Year": str(year), "EpiWeek": str(week), "Frequency": "weekly",
        "RawDiseaseLabel": disease.label, "SourceDiseaseCode": disease.code,
        "ReportDiseaseLabel": report_label,
        "ReportIdentityEvidence": "exact_form_label" if report_label == disease.label else "explicit_source_code",
        "SourceDiseaseGroup": disease.group, "Cases": cases, "Deaths": deaths,
        "Measure": "case_notifications", "Unit": "count", "MissingValueStatus": "reported",
        "GeographyKey": "country:TH:national", "Geography": "Thailand",
        "DefinitionVersion": f"th_dds_situation_single_week_{basis}_v1",
        "ReportingBasis": "dds_notification_onset_date_and_place" if basis == "onset" else "dds_notification_report_date_and_place",
        "TimeBasis": basis, "Source": DEFAULT_SOURCE_NAME, "SourceScope": DEFAULT_SCOPE,
        "SourceURL": STORE_URL, "SourcePublishedAt": published_at, "RetrievedAt": retrieved_at,
        "DatasetStatus": "revisable", "ReportStatus": report_status,
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "RawSHA256": hashlib.sha256(payload).hexdigest(), "PublicReleaseEnabled": "false",
        "CaseDefinition": "Public DDS national situation report, source disease category and onset/report date-place basis retained; notification counts are not confirmed infection estimates.",
        "Dimensions": json.dumps({"date_place_basis": basis}, sort_keys=True),
    }


class ThailandDDSCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=90, delay=0.15)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged th_dds connector to capture report bytes")

    def parse(self, response):
        raise NotImplementedError("DDS parsing requires explicit source disease, year and week")
