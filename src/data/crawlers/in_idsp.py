"""Official IDSP outbreak reports; these are event snapshots, not S/P/L totals."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from lxml import html

from .base import BaseCrawler

DEFAULT_SCOPE = "in_idsp_weekly"
DEFAULT_SOURCE_NAME = "India NCDC IDSP Weekly Outbreak Reports"
ONTOLOGY_SOURCE_ID = "SRC_IN_IDSP"
DIRECTORY_URL = "https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php"
PROGRAMME_URL = "https://ncdc.mohfw.gov.in/includes/About/CentresAndDivision/IDSP.php"
LEGACY_DIRECTORY_URL = "https://idsp.mohfw.gov.in/index4.php?lang=1&level=0&lid=3689&linkid=406"
HISTORY_START_YEAR = 2009
FORM_CASE_DEFINITIONS = {"S": "syndromic_suspected", "P": "presumptive", "L": "laboratory_confirmed"}
DEFINITION_VERSION = "in_idsp_outbreak_event_snapshot_v1"
_EVENT = re.compile(r"[A-Z]{2}/[A-Z]{2,5}/20\d{2}/\d{1,2}/\d+")


class IndiaIDSPContractError(ValueError):
    """Source bytes do not establish the required observation contract."""


class IndiaIDSPCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(delay=0, timeout=45, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged IDSP collector")

    def parse(self, content, **kwargs):
        raise NotImplementedError("Use parse_report_pdf on captured originals")


def discover_reports(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    tree = html.fromstring(payload)
    found = {}
    for anchor in tree.xpath("//a[@href]"):
        url = urljoin(DIRECTORY_URL, anchor.get("href", ""))
        parsed = urlparse(url)
        match = re.fullmatch(r"/uploads/weekly_outbreaks/(20\d{2})/week(\d{1,2})(?:_\d+)?\.pdf", parsed.path, re.I)
        if parsed.scheme != "https" or parsed.hostname != "ncdc.mohfw.gov.in" or not match:
            continue
        year, week = int(match[1]), int(match[2])
        if not 1 <= week <= 53:
            raise IndiaIDSPContractError("Invalid source archive week")
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        found[url] = {"pdf_url": url, "post_url": DIRECTORY_URL, "year": year, "week": week,
                      "title": " ".join(anchor.itertext()).strip()}
    if not found:
        raise IndiaIDSPContractError("No official IDSP outbreak PDFs in requested archive window")
    return sorted(found.values(), key=lambda item: (item["year"], item["week"], item["pdf_url"]))


def _report_period(cover):
    flat = " ".join(cover.split())
    # OCR commonly separates the zero in "20th" and the final k in "Week".
    flat = re.sub(r"(?<=\d)\s+(?=\d(?:st|nd|rd|th)\b)", "", flat)
    label = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+Wee\s*k", flat, re.I)
    report_year_match = re.search(
        r"reported\s+in\s+the\s+\d{1,2}(?:st|nd|rd|th)?\s*week[, ]+\s*(20\d{2})",
        flat, re.I,
    )
    period = re.search(r"\((\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})\s+to\s+"
                       r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})\)", flat, re.I)
    # Older covers omit the year from the dates and put it after the closing
    # parenthesis, or say only "ending <date>". The year is still explicit in
    # the cover and the seven-day range is derived backwards from that date.
    if not period:
        old = re.search(r"(?:\(?)(\d{1,2})(?:st|nd|rd|th)?\s+Week\s*\(\s*"
                        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+to\s+"
                        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s*\)\s*(20\d{2})", flat, re.I)
        ending = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+WEEK\s*\(ending\s+"
                           r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\)\s*(20\d{2})", flat, re.I)
        if old:
            label = label or re.match(r"(\d+)", old[1])
            period = (old[1], old[2], old[3], old[6], old[4], old[5], old[6])
            # Marker below selects the year-only branch.
            period_old = True
        elif ending:
            from datetime import timedelta
            label = label or re.match(r"(\d+)", ending[1])
            end = datetime.strptime(f"{ending[2]} {ending[3]} {ending[4]}", "%d %B %Y").date()
            report_year = int(report_year_match[1]) if report_year_match else end.year
            return report_year, int(label[1]), end - timedelta(days=6), end
        else:
            # Several 2012–2018 covers place the week label before a range,
            # omit the first month in a same-month range, or omit spaces before
            # month names. Accept only explicit day/month ranges with a stated
            # year; no ISO week arithmetic is used.
            compact_month = re.search(
                r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]+)\s*(?:to|[-–])\s*"
                r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]+)\s*\)\s*(20\d{2})",
                flat, re.I,
            )
            compact = re.search(
                r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:week|Week)\s*\(\s*"
                r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|[-–])\s*"
                r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]+)\s*\)\s*(20\d{2})",
                flat, re.I,
            )
            if compact_month:
                start_day, start_month, end_day, end_month, year_text = compact_month.groups()
                label = label or re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:week|Week)", flat, re.I)
                period = (None, start_day, start_month, year_text, end_day, end_month, year_text)
                period_old = True
                generic = same_month = compact = None
            elif compact:
                week_text, start_day, end_day, month, year_text = compact.groups()
                label = label or re.match(r"(\d+)", week_text)
                period = (None, start_day, month, year_text, end_day, month, year_text)
                period_old = True
                generic = same_month = None
            else:
                generic = re.search(
                r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]+)?\s*(?:,?\s*(20\d{2}))?\s*"
                r"(?:to|[-–])\s*(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]+)\s*(?:,?\s*(20\d{2}))?",
                flat, re.I,
            )
                same_month = re.search(
                r"(\d{1,2})(?:st|nd|rd|th)?\s*[-–]\s*(\d{1,2})(?:st|nd|rd|th)?\s*"
                r"([A-Za-z]+)\s*(?:,?\s*(20\d{2}))", flat, re.I,
            )
            if generic or same_month:
                match = generic or same_month
                if same_month and not generic:
                    start_day, end_day, month, year_text = match.groups()
                    start_month = end_month = month
                else:
                    start_day, start_month, year_a, end_day, end_month, year_b = match.groups()
                    year_text = year_a or year_b
                    if not start_month:
                        start_month = end_month
                if not year_text:
                    raise IndiaIDSPContractError("IDSP date range has no explicit year")
                label = label or re.search(r"(\d{1,2})", flat)
                period = (None, start_day, start_month, year_text, end_day, end_month, year_text)
                period_old = True
            else:
                period_old = False
    else:
        period_old = False
    if not label or not period:
        raise IndiaIDSPContractError("IDSP cover has no explicit supported reporting date range")
    try:
        if period_old:
            end = datetime.strptime(f"{period[4]} {period[5]} {period[6]}", "%d %B %Y").date()
            from datetime import timedelta
            start = end - timedelta(days=6)
        else:
            start = datetime.strptime(f"{period[1]} {period[2]} {period[3]}", "%d %B %Y").date()
            end = datetime.strptime(f"{period[4]} {period[5]} {period[6]}", "%d %B %Y").date()
    except ValueError as exc:
        raise IndiaIDSPContractError("Invalid IDSP cover date range") from exc
    if (end - start).days != 6 or not 1 <= int(label[1]) <= 53:
        raise IndiaIDSPContractError("IDSP cover is not an explicit seven-day report")
    report_year = int(report_year_match[1]) if report_year_match else end.year
    return report_year, int(label[1]), start, end


def _join_words(words):
    return " ".join(word["text"] for word in sorted(words, key=lambda w: (round(w["top"] / 3), w["x0"])))


def _events_from_pdf(pdf):
    """Read the verified 2026 ten-column layout; stop before late-report annexes.

    Column geometry keeps numbers in narratives out of the cases/deaths cells.
    Row fragments, including labels and IDs split over pages, stay together.
    Layouts outside this reviewed contract remain archived and unparsed.
    """
    events, current, initialized, late = [], None, False, False
    columns = None
    for page_number, page in enumerate(pdf.pages, 1):
        if page_number < 3:
            continue
        # ``extract_text`` internally extracts words again. The geometry
        # parser already needs words, so derive the small header/annex text
        # from that single extraction and avoid a second PDF layout pass.
        page_words = page.extract_words()
        text = " ".join(word["text"] for word in page_words)
        if not initialized:
            header_words = page_words
            centers = {}
            for label in ("Cases", "Deaths", "District", "Illness", "Start", "Reporting", "Status"):
                candidates = [w for w in header_words if w["text"] == label and w["top"] < 105]
                if candidates:
                    centers[label] = (candidates[0]["x0"] + candidates[0]["x1"]) / 2
            ids = [w for w in header_words if w["text"] in {"Unique", "ID.", "State-UT", "UT"} and w["top"] < 110]
            # The ID and state headers are split over two words in most PDFs;
            # their first table cells are stable enough to use the observed row
            # starts when a compound heading has no single token.
            if "Cases" in centers and "Deaths" in centers and "District" in centers and "Reporting" in centers and "Status" in centers:
                id_center = min((w["x0"] for w in header_words if w["text"] == "Unique" and w["top"] < 110), default=30) + 35
                state_center = min((w["x0"] for w in header_words if w["text"] in {"State-UT", "UT"} and w["top"] < 110), default=140) + 20
                disease_center = centers.get("Illness", centers.get("Cases", 350) - 65)
                onset_center = centers.get("Start", centers["Reporting"] - 45)
                ordered = [id_center, state_center, centers["District"], disease_center,
                           centers["Cases"], centers["Deaths"], onset_center,
                           centers["Reporting"], centers["Status"]]
                boundaries = [0.0] + [(ordered[i] + ordered[i + 1]) / 2 for i in range(8)] + [page.width]
                columns = list(zip(boundaries[:-1], boundaries[1:]))
                initialized = True
            if not initialized:
                continue
        words = [w for w in page_words if 40 < w["top"] < page.height - 15]
        late_words = [w for w in words if w["text"] == "PREVIOUS"]
        cutoff = min((w["top"] for w in late_words), default=float("inf"))
        if late_words and "DISEASE OUTBREAKS OF PREVIOUS WEEKS REPORTED LATE" in text:
            words = [w for w in words if w["top"] < cutoff - 3]
            late = True
        starts = sorted([w for w in words if w["x0"] < columns[0][1] and _EVENT.fullmatch(w["text"])], key=lambda w: w["top"])
        # Any source-ID-looking cell must be understood; malformed IDs cannot
        # silently attach their counts to a preceding outbreak.
        id_words = [w for w in words if w["x0"] < columns[0][1] and re.match(r"[A-Z]{2}/", w["text"])]
        if len(starts) != len(id_words):
            raise IndiaIDSPContractError("Unrecognized IDSP outbreak identifier")
        boundaries = [float("-inf")] + [w["top"] - 2 for w in starts] + [float("inf")]
        for position in range(len(boundaries) - 1):
            if position:
                if current is not None:
                    events.append(current)
                current = {"columns": [[] for _ in columns], "page": page_number}
            if current is None:
                continue
            band = [w for w in words if boundaries[position] <= w["top"] < boundaries[position + 1]]
            for index, (left, right) in enumerate(columns):
                fragment = _join_words([w for w in band if left <= (w["x0"] + w["x1"]) / 2 < right])
                if fragment:
                    current["columns"][index].append(fragment)
        if late:
            break
    if current is not None:
        events.append(current)
    if not events:
        raise IndiaIDSPContractError("No verifiable IDSP outbreak event rows")
    return events, late


def _legacy_text_events(pdf, year, week):
    """Recover older text tables whose geometry and headings changed.

    We only accept a row when both numeric cells and both source dates occur on
    one extracted line. This intentionally omits rows split across cells or
    pages rather than borrowing narrative numbers.
    """
    events, seen, late = [], set(), False
    id_pattern = re.compile(r"(?P<id>[A-Z]{2}/[A-Z]{2,5}/20\d{2}/\d{1,2}/\d+)")
    dated = re.compile(r"(?P<cases>\d[\d,]*)\s*(?:/\s*)?(?P<deaths>\d[\d,]*)\s+"
                       r"(?P<start>\d{1,2}[-./]\d{1,2}[-./]\s*\d{2,4})\s+"
                       r"(?P<reported>\d{1,2}[-./]\d{1,2}[-./]\s*\d{2,4})")
    for page_number, page in enumerate(pdf.pages, 1):
        for line in (page.extract_text() or "").splitlines():
            if re.search(r"DISEASE OUTBREAKS OF PREVIOUS WEEKS REPORTED LATE", line, re.I):
                late = True
            if late:
                continue
            id_match = id_pattern.search(line)
            match = dated.search(line)
            if not match:
                continue
            identifier = id_match.group("id") if id_match else f"IN/OLD/{year}/{week}/{page_number * 1000 + len(events) + 1}"
            if identifier in seen:
                continue
            if id_match:
                source_prefix = line[id_match.end():match.start()].strip()
                parts = source_prefix.split()
                state = " ".join(parts[:1]) or "source_unspecified"
                district = "source_unspecified"
                disease = source_prefix or "source_unspecified"
            else:
                source_prefix = line[:match.start()].strip()
                state = "source_unspecified"
                district = "source_unspecified"
                disease = source_prefix or "source_unspecified"
            # Some old tables use slash/dot dates, and some use two-digit years.
            def parse_date(token):
                token = token.replace(".", "-").replace("/", "-")
                for fmt in ("%d-%m-%Y", "%d-%m-%y"):
                    try:
                        return datetime.strptime(token, fmt).date()
                    except ValueError:
                        continue
                return None
            start, reported = parse_date(match["start"]), parse_date(match["reported"])
            if start is None or reported is None or start > reported:
                continue
            if start.year != year and reported.year != year:
                continue
            seen.add(identifier)
            events.append({"columns": [[identifier], [state], [district], [disease],
                [match["cases"]], [match["deaths"]], [match["start"]], [match["reported"]], ["source_text"]],
                "page": page_number})
    if not events:
        raise IndiaIDSPContractError("No verifiable IDSP outbreak event rows in legacy text layout")
    return events, late


def parse_report_pdf(payload, report):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise IndiaIDSPContractError("Incomplete or invalid IDSP PDF")
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            cover = pdf.pages[0].extract_text() or ""
            if (not re.search(r"WEEKLY OUTBREAK REPORT|DISEASE ALERTS/OUTBREAKS REPORTED", cover, re.I)
                    or not re.search(r"INTEGRATED DISEASE SURVEILLANCE (?:PROGRAM|PROJECT)", cover, re.I)):
                raise IndiaIDSPContractError("PDF does not establish official IDSP outbreak scope")
            year, week, start, end = _report_period(cover)
            if (year, week) != (int(report["year"]), int(report["week"])):
                raise IndiaIDSPContractError("PDF cover and archive year/week disagree")
            if year < 2025:
                try:
                    events, late = _legacy_text_events(pdf, year, week)
                except IndiaIDSPContractError:
                    events, late = _events_from_pdf(pdf)
            else:
                try:
                    events, late = _events_from_pdf(pdf)
                    if any(not _EVENT.fullmatch(" ".join(event["columns"][0]).replace(" ", ""))
                           for event in events):
                        raise IndiaIDSPContractError("Unrecognized event identifiers in geometry layout")
                except IndiaIDSPContractError:
                    # Geometry changed repeatedly across the archive. The fallback
                    # accepts only one-line numeric/date rows and leaves all other
                    # content in the immutable original/audit trail.
                    events, late = _legacy_text_events(pdf, year, week)
    except IndiaIDSPContractError:
        raise
    except Exception as exc:
        raise IndiaIDSPContractError(f"Unable to read IDSP PDF: {type(exc).__name__}") from exc
    rows, issues, seen = [], [], set()
    raw_sha256 = hashlib.sha256(payload).hexdigest()
    for event in events:
        cells = [" ".join(parts).strip() for parts in event["columns"]]
        identifier = cells[0].replace(" ", "")
        if not _EVENT.fullmatch(identifier) or identifier in seen:
            raise IndiaIDSPContractError("Invalid or duplicate outbreak identifier")
        seen.add(identifier)
        state_code, district_code, event_year, event_week, serial = identifier.split("/")
        if (int(event_year), int(event_week)) != (year, week):
            raise IndiaIDSPContractError("Outbreak ID and report period disagree")
        state, district, disease = cells[1:4]
        # A split/malformed column fails this event instead of borrowing values
        # from the narrative or replacing a missing value with zero.
        if not all((state, district, disease)) or not all(re.fullmatch(r"\d[\d,]*", token) for token in cells[4:6]):
            issues.append({"event_id": identifier, "reason": "ambiguous_or_missing_table_cells"})
            continue
        try:
            def source_date(value):
                value = re.sub(r"\s+", "", value).replace(".", "-").replace("/", "-")
                for fmt in ("%d-%m-%Y", "%d-%m-%y"):
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
                raise ValueError(value)
            onset = source_date(cells[6])
            reported = source_date(cells[7])
        except ValueError:
            issues.append({"event_id": identifier, "reason": "ambiguous_source_dates"})
            continue
        if onset > reported or reported > end:
            issues.append({"event_id": identifier, "reason": "inconsistent_source_dates"})
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", disease.casefold()).strip("_")
        for measure, token in zip(("cases", "deaths"), cells[4:6]):
            rows.append({
                "Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": disease,
                "SourceDiseaseCode": f"idsp:outbreak_snapshot:{slug}:{measure}",
                "DefinitionVersion": DEFINITION_VERSION, "Cases": str(int(token.replace(",", ""))),
                "Measure": measure, "Unit": "count", "Frequency": "weekly", "PeriodType": "snapshot",
                "ReportingBasis": "outbreak_event_snapshot", "CaseDefinition": "outbreak_reported_unspecified",
                "Geography": f"{district}, {state}, India",
                "GeographyKey": f"country:IN:source-state:{state_code}:source-district:{district_code}",
                "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"],
                "SourceListingURL": report["post_url"], "SourceReportYear": str(year), "SourceReportWeek": str(week),
                "SourcePeriodYear": str(year), "SourcePeriodWeek": str(week),
                "SourcePeriodStart": start.isoformat(), "SourcePeriodEnd": end.isoformat(),
                "SourceStateName": state, "SourceDistrictName": district,
                "SourceOutbreakStart": onset.isoformat(), "SourceOutbreakReportedOn": reported.isoformat(),
                "SourcePage": str(event["page"]), "RawSHA256": raw_sha256,
                "DatasetStatus": "provisional", "IsProvisional": "true", "PublicReleaseEnabled": "false",
                "License": "reuse_terms_unverified", "RevisionSemantics": "authoritative_revision",
                "Dimensions": json.dumps({"event_id": identifier,
                    "count_semantics": "reported_outbreak_event_snapshot_not_weekly_incidence",
                    "surveillance_form": "not_reported"}, sort_keys=True),
            })
    if not rows:
        raise IndiaIDSPContractError("No verified outbreak observations; missing cells are not zero")
    return rows, {"year": year, "week": week, "event_rows": len(events), "parsed_events": len(rows) // 2,
                  "issues": issues, "late_report_annex_excluded": late,
                  "routine_forms": {form: "not_available_from_outbreak_report" for form in FORM_CASE_DEFINITIONS},
                  "coverage": "District outbreak snapshots only; no national totals, S/P/L merging or inferred weekly incidence"}
