"""Afghanistan MoPH NDSR originals with conservative weekly table extraction.

No ISO calendar is inferred, and no values are read from outbreak summaries,
cumulative columns or charts. The strict numerical contract has not yet been
validated against a live original: source access was unavailable on onboarding.
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

from .base import BaseCrawler

DEFAULT_SCOPE = "af_ndsr_weekly"
DEFAULT_SOURCE_NAME = "Afghanistan MoPH NDSR Weekly Epidemiological Report"
ONTOLOGY_SOURCE_ID = "SRC_AF_MOPH_NDSR"
INDEX_URL = "https://moph.gov.af/en/reports"
WHO_INDEX_URL = "https://www.emro.who.int/afg/programmes/communicable-disease-surveillance-response-dews.html"
SEED_PDF_URL = "https://moph.gov.af/sites/default/files/2026-09/NDSR%20WER%2033-%202026_0.pdf"
HISTORY_START_YEAR = 2025
DEFINITION_VERSION = "af_ndsr_explicit_current_week_table_candidate_v1"
NUMERICAL_CONTRACT_VERIFIED = False


class AfghanistanNDSRContractError(ValueError):
    """An original does not establish the supported numerical contract."""


def official_pdf_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc not in {"moph.gov.af", "www.moph.gov.af"}
            or not parsed.path.casefold().endswith(".pdf")
            or not parsed.path.startswith("/sites/default/files/")):
        raise AfghanistanNDSRContractError("Expected a directly published MoPH PDF URL")
    import requests
    return requests.Request("GET", url).prepare().url


def discover_reports(payload, source_url=INDEX_URL, *, start_year=HISTORY_START_YEAR,
                     end_year=None):
    """Discover existing links; never construct report URLs from week numbers."""
    parsed_source = urlparse(source_url)
    if parsed_source.hostname not in {"moph.gov.af", "www.moph.gov.af", "www.emro.who.int"}:
        raise AfghanistanNDSRContractError("Unrecognized official directory host")
    tree = html.fromstring(payload)
    reports, directories = {}, set()
    for anchor in tree.xpath("//a[@href]"):
        url = urljoin(source_url, anchor.get("href", "").strip())
        parsed = urlparse(url)
        title = " ".join(anchor.text_content().split())
        text = unquote(parsed.path) + " " + title
        if not re.search(r"\bNDSR\b|weekly.{0,30}epidemiolog|epidemiolog.{0,30}weekly", text, re.I):
            continue
        if parsed.path.casefold().endswith(".pdf"):
            try:
                url = official_pdf_url(url)
            except AfghanistanNDSRContractError:
                continue
            # The filename's stated report year wins over the upload folder.
            name_years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", unquote(parsed.path.rsplit("/", 1)[-1]))
            years = name_years or re.findall(r"(?<!\d)(20\d{2})(?!\d)", title)
            year = int(years[-1]) if years else None
            if year is not None and (year < int(start_year) or (end_year is not None and year > int(end_year))):
                continue
            reports[url] = {"pdf_url": url, "post_url": source_url, "title": title,
                            "listed_year": year, "discovery": "official_link"}
        elif (parsed.scheme == "https" and parsed.hostname in {"moph.gov.af", "www.moph.gov.af"}
              and parsed.netloc == parsed_source.netloc and not parsed.fragment
              and parsed.path != parsed_source.path):
            directories.add(url)
    return sorted(directories), sorted(reports.values(), key=lambda r: r["pdf_url"])


def report_period(cover):
    """Require a source-stated week, year and complete seven-day date interval."""
    if not re.search(r"\bNDSR\b|National Disease Surveillance (?:and|&) Response", cover, re.I):
        raise AfghanistanNDSRContractError("Missing NDSR report identity")
    if not re.search(r"Afghanistan|Ministry of Public Health", cover, re.I):
        raise AfghanistanNDSRContractError("Missing Afghanistan MoPH report identity")
    match = re.search(r"(?:Epidemiological\s+)?Week\s*(\d{1,2})\s*[,/-]?\s*(20\d{2})\s*\(([^)]+)\)", cover, re.I)
    if not match or not 1 <= int(match[1]) <= 53:
        raise AfghanistanNDSRContractError("No explicit NDSR source week/date range")
    interval = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", match[3], flags=re.I)
    parts = re.split(r"\s+(?:to|through)\s+|\s*[-–—]\s*", interval, maxsplit=1, flags=re.I)
    if len(parts) != 2 or not re.search(r"\b20\d{2}\b", parts[1]):
        raise AfghanistanNDSRContractError("Date interval must explicitly state its year")
    try:
        end = parse_date(parts[1], dayfirst=True, fuzzy=False).date()
        start = parse_date(parts[0], dayfirst=True, fuzzy=False,
                           default=datetime(end.year, end.month, 1)).date()
        if start > end and not re.search(r"\b20\d{2}\b", parts[0]):
            start = start.replace(year=start.year - 1)
    except (ValueError, OverflowError) as exc:
        raise AfghanistanNDSRContractError("Invalid stated source dates") from exc
    year = int(match[2])
    if (end - start).days != 6 or end.year not in {year, year + 1}:
        raise AfghanistanNDSRContractError("Source interval is not a seven-day report period")
    return year, int(match[1]), start, end


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise AfghanistanNDSRContractError("Incomplete or invalid MoPH PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        if len(pdf.pages) > 100:
            raise AfghanistanNDSRContractError("Unexpected NDSR report length")
        return [{"text": page.extract_text() or "", "tables": page.extract_tables()}
                for page in pdf.pages]


def _clean(value):
    return " ".join(str(value or "").split())


def _count(value):
    token = _clean(value)
    if token in {"", "-", "–", "—", "NR", "N/A"}:
        return "", "source_not_reported"
    if not re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", token):
        raise AfghanistanNDSRContractError(f"Ambiguous weekly count: {token!r}")
    return str(int(token.replace(",", ""))), ""


def parse_report_pages(pages, report, *, raw_sha256=""):
    """Candidate contract: explicitly labelled current-week national tables.

    It is intentionally narrow. A different table layout is an audited parse
    failure, never permission to guess column positions or national totals.
    """
    cover = "\n".join(page["text"] for page in pages[:2])
    year, week, start, end = report_period(cover)
    if report.get("listed_year") is not None and int(report["listed_year"]) != year:
        raise AfghanistanNDSRContractError("PDF year differs from linked report year")
    rows = []
    for page_number, page in enumerate(pages, 1):
        context = _clean(page["text"])
        if not re.search(r"(?:national|Afghanistan).{0,120}(?:current week|during week|weekly (?:reported )?cases)|(?:current week|during week|weekly (?:reported )?cases).{0,120}(?:national|Afghanistan)", context, re.I):
            continue
        if re.search(r"\bprovince\b|\bdistrict\b|\boutbreaks?\b", context, re.I):
            continue
        for table in page.get("tables", []):
            if not table:
                continue
            headers = [_clean(value).casefold() for value in table[0]]
            # Ambiguous bare Cases/Deaths next to cumulative or subgroup
            # columns cannot establish the current-week population.
            if headers not in (["disease", "current week cases", "current week deaths"],
                               ["disease", "weekly cases", "weekly deaths"]):
                continue
            for cells in table[1:]:
                if len(cells) != 3:
                    raise AfghanistanNDSRContractError("Weekly national table column count changed")
                label = _clean(cells[0])
                if not label or label.casefold() in {"total", "grand total"}:
                    continue
                if re.search(r"cumulative|outbreak|laboratory|positive|samples|reporting sites", label, re.I):
                    raise AfghanistanNDSRContractError("Non-case category in weekly notification table")
                slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
                if not slug:
                    raise AfghanistanNDSRContractError("Unrecognized source disease label")
                for measure, value in zip(("cases", "deaths"), cells[1:]):
                    number, status = _count(value)
                    rows.append({
                        "Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
                        "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
                        "SourceReportYear": str(year), "SourceReportWeek": str(week),
                        "SourceYear": str(year), "SourceWeek": str(week),
                        "DiseaseCode": "__source_native__", "RawDiseaseLabel": label,
                        "SourceDiseaseCode": f"ndsr:{slug}:weekly:{measure}",
                        "Cases": number, "MissingValueStatus": status,
                        "Measure": measure, "Unit": "count", "Frequency": "weekly", "PeriodType": "weekly",
                        "ReportingBasis": "source_defined_notification", "DefinitionVersion": DEFINITION_VERSION,
                        "Geography": "Afghanistan reporting surveillance sites",
                        "GeographyKey": "source:AF:ndsr:reporting_sites",
                        "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"],
                        "SourceListingURL": report.get("post_url", ""), "SourcePage": str(page_number),
                        "RawSHA256": raw_sha256, "DatasetStatus": "provisional",
                        "IsProvisional": "true", "RevisionSemantics": "authoritative_revision",
                        "AuthoritativeRevision": "true", "PublicReleaseEnabled": "false",
                        "License": "reuse_terms_unverified", "AggregationPolicy": "non_additive",
                        "Dimensions": json.dumps({"source_week": week, "source_year": year,
                            "source_calendar": "explicit_report_interval", "population": "reporting_surveillance_sites",
                            "case_definition": label, "contract_live_verified": NUMERICAL_CONTRACT_VERIFIED}, sort_keys=True),
                    })
    if not rows:
        raise AfghanistanNDSRContractError("No supported explicitly labelled national current-week case/death table; original retained")
    keys = [(row["SourceDiseaseCode"], row["Date"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise AfghanistanNDSRContractError("Duplicate or conflicting weekly national table category")
    return rows, {"year": year, "week": week, "period_start": start.isoformat(),
                  "period_end": end.isoformat(), "observations": len(rows),
                  "numerical_contract_live_verified": NUMERICAL_CONTRACT_VERIFIED,
                  "coverage_complete": False,
                  "excluded": ["cumulative totals", "outbreak counts", "laboratory surveillance", "province and district tables", "figures"]}


def parse_report_pdf(payload, report):
    return parse_report_pages(extract_pdf_pages(payload), report,
                              raw_sha256=hashlib.sha256(payload).hexdigest())


class AfghanistanNDSRCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(**({"timeout": 25, "max_retries": 1, "delay": 0.1} | kwargs))

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged NDSR collector")

    def parse(self, response):
        raise NotImplementedError("Use captured NDSR report originals")
