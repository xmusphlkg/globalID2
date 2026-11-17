"""WHO Afghanistan outbreak reports, independent of the MoPH NDSR feed.

Only the report's current-week matrix column is read. Its explicit date range
is the time boundary; eight-week historical columns have no inferred dates.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

from dateutil.parser import parse as parse_date
from lxml import html

from .af import AfghanistanNDSRContractError, _count
from .base import BaseCrawler

DEFAULT_SCOPE = "af_who_outbreaks_weekly"
DEFAULT_SOURCE_NAME = "Afghanistan WHO Weekly Infectious Disease Outbreak Situation Reports"
ONTOLOGY_SOURCE_ID = "SRC_AF_WHO_OUTBREAKS"
INDEX_URL = "https://www.emro.who.int/afg/information-resources/infectious-disease-outbreak-situation-reports.html"
HISTORY_START_YEAR = 2025
DEFINITION_VERSION = "af_who_outbreaks_current_week_matrix_v1"
GEOGRAPHY_KEY = "source:AF:who_outbreaks:reporting_sites"

DISEASES = {
    "ari-pneumonia": ("ari_pneumonia", "ARI-Pneumonia"),
    "measles": ("measles", "Measles"),
    "dengue fever": ("dengue_fever", "Dengue fever"),
    "cchf": ("cchf", "CCHF"),
    "malaria": ("malaria", "Malaria"),
    "awd with dehydration": ("awd_with_dehydration", "AWD with dehydration"),
    "covid-19": ("covid_19", "COVID-19"),
}


def discover_reports(payload, *, start_year=HISTORY_START_YEAR, end_year=None, max_reports=12, weeks=None,
                     return_conflicts=False):
    if max_reports is not None and not 1 <= int(max_reports) <= 260:
        raise AfghanistanNDSRContractError("max_reports must be between 1 and 260")
    selected_weeks = None if weeks is None else {int(week) for week in weeks}
    if selected_weeks is not None and (not selected_weeks or not selected_weeks <= set(range(1, 54))):
        raise AfghanistanNDSRContractError("Invalid requested WHO source weeks")
    tree = html.fromstring(payload)
    if tree.xpath('//title[contains(., "404")]'):
        raise AfghanistanNDSRContractError("WHO directory returned a soft 404 page")
    reports = {}
    conflicts = []
    for anchor in tree.xpath("//a[@href]"):
        title = " ".join(anchor.text_content().split())
        week = re.search(r"Epidemiological\s+Week\s*(\d{1,2})\b", title, re.I)
        if not week or not 1 <= int(week[1]) <= 53:
            continue
        if selected_weeks is not None and int(week[1]) not in selected_weeks:
            continue
        url = urljoin(INDEX_URL, anchor.get("href", "").strip())
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != "www.emro.who.int"
                or not parsed.path.startswith("/images/stories/afghanistan/")
                or not parsed.path.casefold().endswith(".pdf")):
            continue
        filename = unquote(parsed.path.rsplit("/", 1)[-1])
        years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", filename) or re.findall(r"(?<!\d)(20\d{2})(?!\d)", title)
        if not years:
            continue
        year = int(years[-1])
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        import requests
        url = requests.Request("GET", url).prepare().url
        report = {"pdf_url": url, "post_url": INDEX_URL, "title": title,
                  "listed_year": year, "listed_week": int(week[1]), "discovery": "official_link"}
        previous = reports.get(url)
        if previous and (previous["listed_year"], previous["listed_week"]) != (year, int(week[1])):
            conflicts.append({"pdf_url": url, "first": previous, "conflicting": report,
                              "status": "conflicting_official_link_periods"})
            # Preserve the first official listing. The PDF cover remains the
            # authority and will reject a mismatching link during replay.
            continue
        reports[url] = report
    selected = sorted(reports.values(), key=lambda r: (-r["listed_year"], -r["listed_week"], r["pdf_url"]))
    selected = selected if max_reports is None else selected[:int(max_reports)]
    return (selected, conflicts) if return_conflicts else selected


def report_period(cover, report):
    identity = re.search(r"AFGHANISTAN\s+INFECTIOUS DISEASE OUTBREAKS\s+SITUATION REPORT|Afghanistan\s*:\s*Infectious Disease Outbreaks", cover, re.I)
    week_match = re.search(r"Epidemiological\s+week\s*#?\s*(\d{1,2})\s*,?\s*(20\d{2})?", cover, re.I)
    no_match = re.search(r"No\.\s*(?:\d+\s*/\s*)?(?:\d+\s*)?\(([^)]+)\)", cover, re.I)
    legacy_interval = re.search(r"Epidemiological\s+week\s*#?\s*\d{1,2}\s*,?\s*20\d{2}\s*\(([^)]+)\)", cover, re.I)
    if not identity or not week_match or (not no_match and not legacy_interval):
        raise AfghanistanNDSRContractError("WHO report identity and explicit week/date interval not established")
    week, stated_year = int(week_match[1]), week_match[2]
    year = int(stated_year) if stated_year else int(report["listed_year"])
    if not 1 <= week <= 53:
        raise AfghanistanNDSRContractError("WHO report week/number conflict")
    if (year, week) != (int(report["listed_year"]), int(report["listed_week"])):
        raise AfghanistanNDSRContractError("WHO PDF period differs from its official link")
    interval_text = no_match[1] if no_match else legacy_interval[1]
    parts = re.split(r"\s+(?:to|through)\s+|\s*[-–—]\s*", interval_text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        raise AfghanistanNDSRContractError("WHO date interval is not a two-sided source interval")
    try:
        # A few 2023 reports state the year in the report header but omit it
        # from the final side of a January interval. The source year is
        # explicit, so this only resolves that local date notation.
        end = parse_date(parts[1], dayfirst=True, fuzzy=False,
                         default=datetime(year, 12, 31)).date()
        start = parse_date(parts[0], dayfirst=True, fuzzy=False, default=datetime(end.year, end.month, 1)).date()
        if start > end and not re.search(r"20\d{2}", parts[0]):
            start = start.replace(year=start.year - 1)
    except (ValueError, OverflowError) as exc:
        raise AfghanistanNDSRContractError("Invalid WHO report interval") from exc
    if (end - start).days != 6 or end.year != year:
        raise AfghanistanNDSRContractError("WHO source interval is not seven days within the stated reporting year")
    return year, week, start, end


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise AfghanistanNDSRContractError("Incomplete WHO outbreak PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        if len(pdf.pages) > 100:
            raise AfghanistanNDSRContractError("Unexpected WHO outbreak PDF length")
        return [page.extract_text() or "" for page in pdf.pages]


def _matrix_count(token):
    """Accept source footnote stars attached to a numeric matrix cell."""
    return _count(re.sub(r"\*+$", "", str(token).strip()))


def parse_report_pages(pages, report, *, raw_sha256=""):
    year, week, start, end = report_period(pages[0], report)
    rows, audit_tables, parsed_tables = [], [], set()
    for page_number, page in enumerate(pages, 1):
        markers = list(re.finditer(r"Table\s+(\d+)\s*:?\s*(?:summary of )?(?:the )?([^\n]+?)\s+(?:outbreak|indicators)\s+in the last\s+(?:eight|8)\s+weeks(?:\s+in Afghanistan)?[^\n]*", page, re.I))
        for i, marker in enumerate(markers):
            table_number, disease = int(marker[1]), " ".join(marker[2].casefold().split())
            if disease not in DISEASES:
                audit_tables.append({"page": page_number, "table": table_number, "status": "unsupported_category", "label": disease})
                continue
            block = page[marker.end(): markers[i + 1].start() if i + 1 < len(markers) else len(page)]
            # Some originals extract the matrix below the prose and figures.
            # Locate its exact header within this named table's block, then
            # read indicator rows only up to CFR; narrative numbers stay out.
            headers = list(re.finditer(r"^Indicators\s+((?:W\d{1,2}(?:-\d{2})?\s+){1,8})(?:Trend lines?|$)", block, re.M | re.I))
            if len(headers) != 1:
                audit_tables.append({"page": page_number, "table": table_number, "category": disease, "status": "unreadable_matrix"})
                continue
            header = headers[0]
            columns = re.findall(r"W(\d{1,2})(?:-(\d{2}))?", header[1], re.I)
            targets = [j for j, (w, y) in enumerate(columns) if int(w) == week and (not y or 2000 + int(y) == year)]
            if len(targets) != 1 or targets[0] != len(columns) - 1:
                raise AfghanistanNDSRContractError("Current WHO reporting week is not the unique final matrix column")
            if disease in parsed_tables:
                raise AfghanistanNDSRContractError("Duplicate WHO disease matrix in one original")
            parsed_tables.add(disease)
            category, label = DISEASES[disease]
            observed = set()
            for line in block[header.end():].splitlines():
                if re.match(r"CFR\s*\(%\)", line.strip(), re.I):
                    break
                match = re.fullmatch(r"(?:(Suspected|Confirmed|Number of)\s+)?(cases|deaths)\s+(.+)", line.strip(), re.I)
                if not match:
                    continue
                classification = (match[1] if match[1] and match[1].casefold() != "number of" else "source_defined").casefold()
                measure = match[2].casefold()
                tokens = match[3].split()
                # Asterisks annotate revisions in some COVID tables. They are
                # discarded only as standalone footnote markers, never digits.
                tokens = [token for token in tokens if token != "*"]
                if len(tokens) != len(columns):
                    audit_tables.append({"page": page_number, "table": table_number, "category": disease, "measure": measure, "status": "ambiguous_matrix_cells"})
                    continue
                numbers = [_matrix_count(token) for token in tokens]
                if (classification, measure) in observed:
                    raise AfghanistanNDSRContractError("Duplicate case/death row in WHO weekly matrix")
                observed.add((classification, measure))
                value, missing = numbers[targets[0]]
                rows.append({
                    "Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
                    "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
                    "SourceReportYear": str(year), "SourceReportWeek": str(week),
                    "SourceYear": str(year), "SourceWeek": str(week),
                    "DiseaseCode": "__source_native__", "RawDiseaseLabel": f"{label} ({classification})",
                    "SourceDiseaseCode": f"who_outbreaks:{category}:{classification}:weekly:{measure}",
                    "Cases": value, "MissingValueStatus": missing, "Measure": measure, "Unit": "count",
                    "Frequency": "weekly", "PeriodType": "weekly", "ReportingBasis": "sentinel_notification",
                    "DefinitionVersion": DEFINITION_VERSION, "Geography": "Afghanistan reporting surveillance sites",
                    "GeographyKey": GEOGRAPHY_KEY, "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": report["pdf_url"], "SourceListingURL": INDEX_URL,
                    "SourcePage": str(page_number), "SourceTable": str(table_number),
                    "RawSHA256": raw_sha256, "DatasetStatus": "provisional_revisable", "IsProvisional": "true",
                    "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                    "PublicReleaseEnabled": "false", "License": "who_source_terms_pending_review",
                    "AggregationPolicy": "non_additive", "Dimensions": json.dumps({
                        "case_classification": classification, "source_week": week, "source_year": year,
                        "source_calendar": "explicit_report_interval", "population": "reporting_surveillance_sites",
                        "extraction": "current_week_exact_matrix_cell", "matrix_indicator": " ".join(line.strip().split()[:2]) if match[1] else measure,
                    }, sort_keys=True),
                })
            audit_tables.append({"page": page_number, "table": table_number, "category": disease,
                                 "status": "parsed_current_week_matrix" if observed else "no_supported_case_death_rows", "measures": sorted({measure for _, measure in observed})})
    if not rows:
        raise AfghanistanNDSRContractError("No verified current-week WHO case/death matrix cells")
    return rows, {"year": year, "week": week, "period_start": start.isoformat(), "period_end": end.isoformat(),
                  "numerical_contract_live_verified": True, "coverage_complete": False,
                  "observations": len(rows), "tables": audit_tables,
                  "excluded": ["eight-week history without explicit dates", "cumulative counts", "figures", "laboratory tests", "narratives", "province/district breakdowns"]}


def parse_report_pdf(payload, report):
    return parse_report_pages(extract_pdf_pages(payload), report, raw_sha256=hashlib.sha256(payload).hexdigest())


class AfghanistanWHOOutbreaksCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(**({"timeout": 30, "max_retries": 1, "delay": 0.1} | kwargs))

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged WHO Afghanistan outbreak collection")

    def parse(self, response):
        raise NotImplementedError("Use preserved WHO PDF originals")
