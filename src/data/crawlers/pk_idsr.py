"""Official Pakistan NIH weekly IDSR originals and source-native suspected cases.

The observed reports identify source year/week without week boundaries. Dates
below are year storage anchors only; the source week remains a holding dimension.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from urllib.parse import urljoin, urlparse

from lxml import html

from .base import BaseCrawler

DEFAULT_SCOPE = "pk_nih_idsr_weekly"
DEFAULT_SOURCE_NAME = "Pakistan NIH IDSR Weekly Bulletin"
ONTOLOGY_SOURCE_ID = "SRC_PK_NIH_IDSR"
INDEX_URL = "https://nih.org.pk/public/phb/weekly-bulletin"
SECONDARY_INDEX_URL = "https://phb.nih.org.pk/integratedisease-surveillance-and-response"
HISTORY_START_YEAR = 2025
AGGREGATE_GEOGRAPHY_KEY = "source:PK:nih_idsr:reporting_areas"
AREAS = ("AJK", "Balochistan", "GB", "ICT", "KP", "Punjab", "Sindh", "Total")


class PakistanIDSRContractError(ValueError):
    """Published evidence does not establish the expected report contract."""


class PakistanNIHIDSRCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(delay=0.1, timeout=45, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged Pakistan IDSR collector")

    def parse(self, response, **kwargs):
        raise NotImplementedError("Use parse_report_pdf with captured originals")


def discover_reports(payload, *, start_year=HISTORY_START_YEAR, end_year=None,
                     weeks=None, max_reports=12):
    if max_reports is not None and not 1 <= int(max_reports) <= 260:
        raise PakistanIDSRContractError("max_reports must be between 1 and 260")
    selected_weeks = None if weeks is None else {int(week) for week in weeks}
    if selected_weeks is not None and (not selected_weeks or not selected_weeks <= set(range(1, 54))):
        raise PakistanIDSRContractError("Invalid requested source weeks")
    tree = html.fromstring(payload)
    reports = {}
    for anchor in tree.xpath("//a[@href]"):
        title = " ".join(anchor.text_content().split())
        period = re.fullmatch(r"IDSRS? Week (\d{1,2}) Bulletin \((20\d{2})\)", title, re.I)
        if not period:
            continue
        week, year = int(period[1]), int(period[2])
        if not 1 <= week <= 53:
            raise PakistanIDSRContractError("Official listing has an invalid source week")
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        if selected_weeks is not None and week not in selected_weeks:
            continue
        # Both actual newlines and literal backslash-n occur in official hrefs.
        href = re.sub(r"[\r\n\t]", "", anchor.get("href", "").replace("\\n", "")).strip()
        # Some archived cards retain a title but have no downloadable original.
        # Keep the report absent from the download plan instead of inventing a URL.
        if not href or href == "#":
            continue
        url = urljoin(INDEX_URL, href)
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in {"nih.org.pk", "www.nih.org.pk"}
                or not parsed.path.lower().endswith(".pdf") or "/wp-content/uploads/" not in parsed.path):
            raise PakistanIDSRContractError("Unexpected IDSR PDF host/path in official listing")
        # requests uses this same URL quoting, so manifests match final URLs.
        import requests
        url = requests.Request("GET", url).prepare().url
        reports[url] = {"title": title, "year": year, "week": week,
                        "pdf_url": url, "post_url": INDEX_URL}
    result = sorted(reports.values(), key=lambda item: (-item["year"], -item["week"], item["pdf_url"]))
    return result if max_reports is None else result[:int(max_reports)]


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise PakistanIDSRContractError("Incomplete or invalid NIH PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        # The supported national/province table occurs before district tables;
        # subsequent laboratory and narrative sections are deliberately excluded.
        return [page.extract_text() or "" for page in pdf.pages[:10]]


def parse_report_pages(pages, report, *, raw_sha256=""):
    cover = re.sub(r"He\s+alth", "Health", "\n".join(pages[:3]))
    if not re.search(r"Public Health Bulletin\s*[-–]\s*Pakistan", cover, re.I):
        raise PakistanIDSRContractError("PDF does not establish Pakistan PHB identity")
    # PDF text extraction splits Health on the current cover.
    periods = {(int(year), int(week)) for week, year in re.findall(
        r"Bulletin\s*[-–]\s*Pakistan[.,]?\s*,?\s*Week\s+(\d{1,2})\s*,\s*(20\d{2})", cover, re.I)}
    if periods != {(int(report["year"]), int(report["week"]))}:
        raise PakistanIDSRContractError("PDF year/week differs from official listing")
    year, week = next(iter(periods))
    matches = []
    for page_number, page in enumerate(pages, 1):
        marker = re.search(r"Table\s+1\s*:\s*Province/Area\s+wise\s+distribution\s+of\s+most\s+frequently\s+reported\s+suspected\s+cases\s+during\s+Week\s+(\d{1,2}),\s*Pakistan\.", page, re.I)
        if marker:
            if int(marker[1]) != week:
                raise PakistanIDSRContractError("Table week differs from report cover")
            matches.append((page_number, page[marker.end():]))
    if len(matches) != 1:
        raise PakistanIDSRContractError("Expected one supported province/area suspected-case table")
    page_number, table = matches[0]
    lines = [line.strip() for line in table.splitlines() if line.strip()]
    if not lines or lines.pop(0) != "Diseases " + " ".join(AREAS):
        raise PakistanIDSRContractError("Province/area table columns changed")
    categories = []
    pending = ""
    for line in lines:
        if re.match(r"Page\s*\|", line):
            break
        cells = line.rsplit(None, 8)
        if len(cells) == 9 and all(re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+|NR|[-–—]", cell) for cell in cells[1:]):
            label = " ".join((pending + " " + cells[0]).split())
            pending = ""
            numbers = [None if token in {"NR", "-", "–", "—"} else int(token.replace(",", "")) for token in cells[1:]]
            if numbers[-1] is not None and sum(value for value in numbers[:-1] if value is not None) != numbers[-1]:
                raise PakistanIDSRContractError(f"Published total does not reconcile for {label}")
            categories.append((label, numbers, cells[1:]))
        elif not re.search(r"\d", line) and re.fullmatch(r"[A-Za-z /()&.-]+", line):
            pending = " ".join((pending + " " + line).split())
        else:
            raise PakistanIDSRContractError(f"Unrecognized table row: {line!r}")
    if pending or len(categories) < 10:
        raise PakistanIDSRContractError("Incomplete suspected-case table")
    slugs = [re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") for label, _, _ in categories]
    if len(set(slugs)) != len(slugs):
        raise PakistanIDSRContractError("Duplicate disease category in suspected-case table")
    rows = []
    for slug, (label, numbers, tokens) in zip(slugs, categories):
        for area, value, token in zip(AREAS, numbers, tokens):
            aggregate = area == "Total"
            dimensions = {"source_year": year, "source_week": week, "source_area": area,
                          "case_classification": "suspected", "table": "Table 1",
                          "geography_role": "reported_area_aggregate" if aggregate else "province_or_area",
                          "calendar_verified": False}
            rows.append({
                "Date": f"{year}-01-01", "Year": str(year), "Month": "1",
                "SourceYear": str(year), "SourceWeek": str(week),
                "SourcePeriodYear": str(year), "SourcePeriodWeek": str(week),
                "SourceReportYear": str(year), "SourceReportWeek": str(week),
                "SourcePeriodStart": "", "SourcePeriodEnd": "",
                "CalendarStatus": "unverified_source_week_calendar", "WeekCalendarVerified": "false",
                "TimeBasis": "source_year_week_unverified_calendar",
                "DateRole": "source_year_storage_anchor_only", "TimestampSemantics": "source_year_storage_anchor_only",
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": label,
                "SourceDiseaseCode": f"idsr:{slug}:suspected:weekly:cases",
                "Cases": "" if value is None else str(value),
                "MissingValueStatus": "source_not_reported" if token == "NR" else "source_missing" if value is None else "",
                "Measure": "cases", "Unit": "count", "Frequency": "weekly", "PeriodType": "source_week",
                "ReportingBasis": "notification", "DefinitionVersion": "pk_nih_idsr_table1_suspected_source_week_v1",
                "Geography": "Pakistan IDSR reporting areas (published total)" if aggregate else area,
                "GeographyKey": AGGREGATE_GEOGRAPHY_KEY if aggregate else f"source:PK:nih_idsr:area:{area.casefold()}",
                "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"], "SourceListingURL": INDEX_URL,
                "SourcePage": str(page_number), "RawSHA256": raw_sha256,
                "DatasetStatus": "provisional", "IsProvisional": "true",
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
                "Dimensions": json.dumps(dimensions, sort_keys=True),
            })
    return rows, {"year": year, "week": week, "table_page": page_number,
                  "categories": len(categories), "observations": len(rows),
                  "calendar_verified": False, "dates_inferred": False,
                  "excluded_sections": ["district tables", "laboratory positives/tests", "sentinel charts", "outbreak narratives"],
                  "death_counts_available_in_supported_table": False,
                  "total_scope": "reported_areas_not_complete_national_incidence"}


def parse_report_pdf(payload, report):
    return parse_report_pages(extract_pdf_pages(payload), report,
                              raw_sha256=hashlib.sha256(payload).hexdigest())
