"""NCDC's complete public WER and situation-report archives.

Discovery is deliberately independent from numerical coverage. A downloadable
PDF, a graph and a cumulative count are not interchangeable weekly case facts.
Every discovered report is audited even when its template cannot be parsed.
"""
from __future__ import annotations

import hashlib
import json
import io
import re
import calendar
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse

from lxml import html

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "ncdc_surveillance"
DEFAULT_SOURCE_NAME = "Nigeria NCDC Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_NG_NCDC"
WEEKLY_URL = "https://ncdc.gov.ng/reports/weekly"
SITREP_URL = "https://ncdc.gov.ng/diseases/sitreps"
HISTORY_START_YEAR = 2016


class NigeriaNCDCContractError(ValueError):
    """An unverified period or table must not create a case observation."""


def _document(payload):
    if not payload.strip():
        raise NigeriaNCDCContractError("Empty NCDC catalogue response")
    return html.fromstring(payload)


def discover_weekly_pages(payload, *, base_url=WEEKLY_URL):
    urls = list(dict.fromkeys(urljoin(base_url, a) for a in _document(payload).xpath("//a/@href") if re.search(r"/reports/weekly/\d+/\d+/\d+$", a)))
    if not urls:
        raise NigeriaNCDCContractError("NCDC weekly archive contains no report links")
    return urls


def discover_categories(payload, *, base_url=SITREP_URL):
    urls = list(dict.fromkeys(urljoin(base_url, a) for a in _document(payload).xpath("//a/@href") if re.search(r"[?&]cat=\d+", a)))
    if not urls:
        raise NigeriaNCDCContractError("NCDC situation archive contains no disease categories")
    return urls


def discover_reports(payload, *, listing_url, kind):
    d = _document(payload)
    reports = []
    for a in d.xpath("//a[contains(@href,'.pdf')]"):
        url = urljoin(listing_url, a.get("href"))
        if urlparse(url).hostname != "ncdc.gov.ng":
            continue
        if kind == "weekly" and "/docs/wers/" in url:
            m = re.search(r"/weekly/(\d+)/(\d+)/(\d+)$", listing_url)
            if not m:
                raise NigeriaNCDCContractError("Unexpected weekly report route")
            reports.append({"url": url, "listing_url": listing_url, "kind": kind, "year": int(m[1]), "week": int(m[3])})
        elif kind == "sitrep" and "/files/sitreps/" in url:
            reports.append({"url": url, "listing_url": listing_url, "kind": kind, "category_id": parse_qs(urlparse(listing_url).query).get("cat", [""])[0], "download_name": a.get("download", ""), "title": " ".join(a.getparent().getparent().text_content().split())})
    if kind == "weekly" and len(reports) != 1:
        raise NigeriaNCDCContractError("Expected one NCDC weekly PDF download")
    return list({r["url"]: r for r in reports}.values())


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF"):
        raise NigeriaNCDCContractError("NCDC response is not a PDF")
    # PDFium is fast enough to validate the entire public archive. It is not
    # thread-safe; organization calls this parser sequentially/offline.
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(payload)
    pages = []
    try:
        for page in document:
            textpage = page.get_textpage()
            try:
                pages.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
    if not any(page.strip() for page in pages):
        raise NigeriaNCDCContractError("Scanned PDF has no verifiable text layer")
    return pages


def _slug(label):
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


_DISEASES = [
    ("lassa_fever", r"Lassa\s+Fever"),
    ("cerebrospinal_meningitis", r"Cerebrospinal\s+Meningitis(?:\s*\(CSM\))?|\bCSM\b"),
    ("yellow_fever", r"Yellow\s+Fever"),
    ("cholera", r"Cholera"),
    ("measles", r"Measles"),
    ("mpox", r"Monkeypox|Mpox"),
    ("acute_flaccid_paralysis", r"Acute\s+Flaccid\s+Paralysis(?:\s*\(A(?:FP|PF)\))?|\bAFP\b"),
    ("covid_19", r"Coronavirus\s+Disease(?:\s*\(COVID.?19\))?|COVID.?19"),
    ("diphtheria", r"Diphtheria"),
    ("influenza_sentinel", r"National\s+(?:Influenza\s+Sentinel|Sentinel\s+influenza)\s+Surveillance"),
    ("rubella", r"Rubella"),
    ("dengue", r"Dengue(?:\s+Fever)?"),
    ("ebola", r"Ebola"),
    ("guinea_worm", r"Guinea\s+Worm(?:\s+Disease)?"),
    ("polio", r"Polio(?:myelitis)?"),
]


def disease_identity(label):
    label = " ".join(label.split())
    for code, pattern in _DISEASES:
        if re.fullmatch(pattern, label, re.I):
            return code
    # New table categories remain source-native; they are not auto-mapped.
    return _slug(label)


def _count(token):
    if token.strip() in {"", "-", "—", "NA", "N/A"}:
        return None
    if not re.fullmatch(r"\d[\d,]*", token.strip()):
        raise NigeriaNCDCContractError(f"Invalid NCDC count {token!r}")
    return int(token.replace(",", ""))


def _row(label, classification, value, *, year, week, report, period="weekly", measure="cases", family="wer", geography="Nigeria", geography_key="country:NG:national", source_hash="", period_end=None, frequency=None, period_start=None):
    try:
        when = date.fromisoformat(period_end) if period_end else date.fromisocalendar(int(year), int(week), 7)
    except ValueError as exc:
        raise NigeriaNCDCContractError("Invalid report epidemiological week") from exc
    code = disease_identity(label)
    basis = "sentinel_surveillance" if code == "influenza_sentinel" else "notification"
    if period != "weekly":
        basis = "cumulative" if period.startswith("cumulative") else "notification"
    source_code = ":".join([family, code, classification, period, measure])
    return {
        "Date": when.isoformat(), "Year": str(when.year), "Month": str(when.month),
        "DiseaseCode": "__source_native__", "RawDiseaseLabel": label,
        "SourceDiseaseCode": source_code, "Cases": "" if value is None else str(value),
        "MissingValueStatus": "source_missing" if value is None else "",
        "Measure": measure, "Unit": "count", "Frequency": frequency or ("weekly" if period in {"weekly", "cumulative"} else period),
        "PeriodType": period, "ReportingBasis": basis,
        "DefinitionVersion": f"ng_ncdc_{family}_{classification}_{period}_{measure}_v1",
        "Geography": geography, "GeographyKey": geography_key,
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["url"],
        "SourceListingURL": report.get("listing_url", ""), "RawSHA256": source_hash,
        "SourceReportYear": str(report.get("report_year", report.get("year", year))),
        "SourceReportWeek": str(report.get("report_week", report.get("week", week))),
        "SourceReportDate": str(report.get("report_date", "")),
        "SourcePeriodYear": str(year), "SourcePeriodWeek": str(week),
        "SourcePeriodStart": period_start or (date(int(year), 1, 1).isoformat() if period.startswith("cumulative") else (when - timedelta(days=6)).isoformat()),
        "SourcePeriodEnd": when.isoformat(),
        "DatasetStatus": "provisional", "IsProvisional": "true",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
        "Dimensions": json.dumps({"source_disease_code": code, "case_classification": classification, "report_family": family, "count_period": period, "surveillance_scope": "sentinel" if code == "influenza_sentinel" else "reported_notifications"}, sort_keys=True),
    }


def _weekly_period(pages, report):
    text = "\n".join(pages[:2])
    # The PDF's issue/week has priority over its route. Old NCDC routes can
    # point at a preceding issue; the discrepancy is retained in the audit.
    m = re.search(r"Week\s+(\d{1,2})\s*:\s*.*?(20\d{2})", text, re.I | re.S)
    if m:
        return int(m[2]), int(m[1])
    m = re.search(r"WER.Nigeria,\s*(20\d{2})\s*\(Vol\.\s*\d+:\s*No\.\s*(\d+)", text, re.I)
    if m:
        return int(m[1]), int(m[2])
    # Legacy narrative issues have no cover week but contain the exact
    # source year and an explicit Issue No.; require both to agree.
    m = re.search(r"Issue:\s*Volume\s+\d+\s+No\.\s*(\d+).{0,80}?(20\d{2})", text, re.I | re.S)
    if m:
        return int(m[2]), int(m[1])
    raise NigeriaNCDCContractError("PDF has no explicit report year/week")


def parse_weekly_pages(pages, report, *, source_hash=""):
    year, week = _weekly_period(pages, report)
    date.fromisocalendar(year, week, 7)
    report = dict(report, report_year=year, report_week=week)
    rows, issues, parsed_categories = [], [], set()
    for page_number, page in enumerate(pages, 1):
        # Dedicated disease pages carry a heading followed by Week/Table.
        m = re.search(r"(?:^|\n)Report\s*\n(?P<label>[^\n]+)\n(?:Table\s+\d+:\s*)?Week\s+(?P<week>\d+)\b(?P<table>.*?)(?=Year\s+to\s+date|Table\s+\d+:[^\n]*Year\s+to\s+date|Key\s+[Pp]oint|Figure\s+\d|$)", page, re.I | re.S)
        if not m:
            continue
        label = m["label"].strip()
        if not label or label.casefold().startswith(("summary", "event", "table")):
            continue
        if int(m["week"]) != week:
            issues.append({"page": page_number, "category": label, "error": "Disease table week differs from report week"})
            continue
        table = m["table"]
        influenza = disease_identity(label) == "influenza_sentinel"
        if not re.search(r"Suspected\s+cases", table, re.I) or not re.search(r"Confirmed\s+cases", table, re.I):
            issues.append({"page": page_number, "category": label, "error": "Unverified weekly table columns"})
            continue
        width = 4 if influenza else 3
        token = r"(?:[\d,]+|[-—]|N/?A)"
        matches = re.findall(r"^\s*((?:" + token + r"\s+){" + str(width - 1) + "}" + token + r")(?:\s+State:.*)?\s*$", table, re.M)
        if len(matches) != 1:
            issues.append({"page": page_number, "category": label, "error": "No unique weekly count row"})
            continue
        values = [_count(v) for v in matches[0].split()]
        fields = [("suspected", "cases"), ("confirmed", "cases"), ("all_reported", "deaths")]
        if influenza:
            fields = [("suspected", "cases"), ("confirmed", "cases"), ("influenza_a_confirmed", "cases"), ("influenza_b_confirmed", "cases")]
        for value, (classification, measure) in zip(values, fields):
            rows.append(_row(label, classification, value, year=year, week=week, report=report, measure=measure, source_hash=source_hash))
        parsed_categories.add(disease_identity(label))
        # Current templates explicitly identify one year per row. Older
        # templates print interleaved year columns; retain those in audit
        # rather than assume their mislabelled 'week 1–52' is this week's YTD.
        cumulative = re.search(r"Year\s+to\s+date(?P<body>.*?)(?:Key\s+[Pp]oint|Figure\s+\d|$)", page, re.I | re.S)
        if cumulative:
            for match in re.finditer(r"^\s*(20\d{2})\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%\s*$", cumulative["body"], re.M):
                cyear = int(match[1])
                if not (year - 1 <= cyear <= year):
                    continue
                for value, (classification, measure) in zip(match.groups()[1:], fields[:3]):
                    rows.append(_row(label, classification, _count(value), year=cyear, week=week, report=report, period="cumulative", measure=measure, source_hash=source_hash))
    if not rows:
        rows.extend(_parse_legacy_summary(pages, report, year, week, source_hash))
    if not rows:
        raise NigeriaNCDCContractError("No independently verified weekly table or summary statements")
    if report.get("year") != year or report.get("week") != week:
        issues.append({"error": "Route/PDF reporting period differs", "route_year": report.get("year"), "route_week": report.get("week"), "pdf_year": year, "pdf_week": week})
    parsed = {json.loads(r["Dimensions"])["source_disease_code"] for r in rows}
    mentioned = {code for code, pattern in _DISEASES if re.search(pattern, "\n".join(pages), re.I)}
    return rows, {"year": year, "week": week, "parsed_categories": sorted(parsed), "mentioned_unparsed_categories": sorted(mentioned - parsed), "issues": issues, "coverage": "verified_text_tables_and_explicit_summary_statements; graphics and unverified layouts retained in raw archive"}


_NUMBER_WORDS = {word: str(i) for i, word in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])}


def _parse_legacy_summary(pages, report, year, week, source_hash):
    text = "\n".join(pages)
    m = re.search(r"SUMMARY\s+OF\s+REPORTS(.*?)(?:REPORT\s+ANALYSIS|In\s+the\s+reporting\s+week,\s+all\s+States)", text, re.S | re.I)
    if not m:
        return []
    summary = " ".join(m[1].split())
    for word, number in _NUMBER_WORDS.items():
        summary = re.sub(r"\b" + word + r"\b", number, summary, flags=re.I)
    rows = []
    for label_code, label_pattern in _DISEASES:
        pattern = r"There\s+(?:were|was)\s+(\d[\d,]*)\s+(?:new\s+)?(?:suspected\s+)?cases?\s+of\s+(" + label_pattern + r")\s+(?:\([^)]*\)\s*)?reported\b(.*?)(?=\bThere\s+(?:were|was)\s+\d[\d,]*\s+(?:new\s+)?(?:suspected\s+)?cases?\s+of\b|$)"
        match = re.search(pattern, summary, re.I)
        if not match:
            continue
        label, tail = match[2], match[3]
        classification = "suspected" if re.search(r"\bsuspected\s+cases?\s+of\b", match[0], re.I) else "reported"
        rows.append(_row(label, classification, _count(match[1]), year=year, week=week, report=report, source_hash=source_hash))
        # Only unambiguous summary statements create other measures. Do not
        # infer a death/confirmation zero merely because a sentence omits it.
        confirmed = re.search(r"(\d[\d,]*)\s+(?:was|were)\s+laboratory\s+confirmed", tail, re.I)
        zero_confirmed = re.search(r"(?:no|None\s+was)\s+laboratory\s+confirmed", tail, re.I)
        if confirmed or zero_confirmed:
            rows.append(_row(label, "confirmed", _count(confirmed[1]) if confirmed else 0, year=year, week=week, report=report, source_hash=source_hash))
        deaths = re.search(r"(\d[\d,]*)\s+deaths?\s+(?:was|were)\s+recorded", tail, re.I)
        zero_deaths = re.search(r"\bno\s+deaths?\b", tail, re.I)
        if deaths or zero_deaths:
            rows.append(_row(label, "all_reported", _count(deaths[1]) if deaths else 0, year=year, week=week, report=report, measure="deaths", source_hash=source_hash))
    return rows


def _month_number(label):
    return next((i for i in range(1, 13) if label.casefold() in {calendar.month_name[i].casefold(), calendar.month_abbr[i].casefold()}), None)


def _monthly_row(label, classification, value, *, year, month, report, source_hash, cumulative=False, measure="cases"):
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    period = "cumulative_monthly" if cumulative else "monthly"
    row = _row(label, classification, value, year=year, week=first.isocalendar().week, report=report, family="sitrep", period=period, measure=measure, source_hash=source_hash, period_end=first.isoformat(), frequency="monthly", period_start=date(year, 1, 1).isoformat() if cumulative else first.isoformat())
    row["SourcePeriodEnd"] = last.isoformat()
    return row


def _parse_monthly_classification_table(first, report, source_hash):
    label = "Measles" if str(report.get("category_id")) == "11" else "Dengue Fever"
    heading = re.search(re.escape(label) + r"\s+Situation\s+Report\s+([A-Za-z]+)\s+(20\d{2})", first, re.I)
    if not heading or not _month_number(heading[1]):
        raise NigeriaNCDCContractError("No explicit monthly disease report period")
    year, month = int(heading[2]), _month_number(heading[1])
    report = dict(report, report_year=year, report_week=date(year, month, 1).isocalendar().week)
    if not re.search(r"Suspected\s+cases\s+Confirmed\s+cases\s+Deaths\s+\(Confirmed\s+cases\)", first, re.I):
        raise NigeriaNCDCContractError("Monthly suspected/confirmed/death columns changed")
    table = re.search(r"Table\s+1:(.*?)(?:Highlights|$)", first, re.S | re.I)
    if not table:
        raise NigeriaNCDCContractError("Monthly classification summary table missing")
    fields = [("suspected", "cases"), ("confirmed", "cases"), ("confirmed", "deaths")]
    pattern = r"(?<!\w)" + re.escape(heading[1]) + r"\s+(20\d{2})\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%"
    match = re.search(pattern, table[1], re.I)
    if not match:
        raise NigeriaNCDCContractError("Monthly classification count row missing")
    rows = [_monthly_row(label, c, _count(v), year=year, month=month, report=report, source_hash=source_hash, measure=m) for v, (c, m) in zip(match.groups()[1:], fields)]
    # Restrict comparative YTD rows to an explicit single calendar year.
    # A Jan-2025 to Oct-2026 outbreak total is not a 2026 YTD count.
    pattern = r"Jan(?:uary)?\s*(?:to|-|–)\s*" + re.escape(heading[1]) + r"\s+(20\d{2})\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%"
    for match in re.finditer(pattern, table[1], re.I):
        rows.extend(_monthly_row(label, c, _count(v), year=int(match[1]), month=month, report=report, source_hash=source_hash, cumulative=True, measure=m) for v, (c, m) in zip(match.groups()[1:], fields))
    return rows, {"year": year, "month": month, "parsed_categories": [disease_identity(label)], "issues": [], "coverage": "verified national monthly classification table; state/age/sex and multi-year outbreak totals retained in raw archive"}


def _parse_weekly_classification_sitrep(first, report, source_hash):
    category = str(report["category_id"])
    label = "Diphtheria" if category == "18" else "Cerebrospinal Meningitis"
    period = re.search(r"(?:Epi(?:demiological)?\s*[ -]?\s*[Ww]eek)\s*:?\s*(\d+).*?(20\d{2})", first, re.I | re.S)
    if not period:
        raise NigeriaNCDCContractError("No explicit situation report year/week")
    week, year = int(period[1]), int(period[2])
    report = dict(report, report_year=year, report_week=week)
    flat = " ".join(first.split())
    if not re.search(r"Suspected cases Confirmed cases Deaths", flat, re.I):
        raise NigeriaNCDCContractError("Suspected/confirmed/death table columns changed")
    pattern = r"Current week\s*(?:20\d{2}\s*)?\(Week\s*\d+\)\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%?" if category == "18" else r"Epi Week\s+" + str(week) + r"\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%"
    current = re.search(pattern, flat, re.I)
    if not current:
        raise NigeriaNCDCContractError("Situation report current-week summary row missing")
    fields = [("suspected", "cases"), ("confirmed", "cases"), ("confirmed" if category == "18" else "suspected", "deaths")]
    rows = [_row(label, c, _count(v), year=year, week=week, report=report, family="sitrep", measure=m, source_hash=source_hash) for v, (c, m) in zip(current.groups(), fields)]
    if category == "18":
        for match in re.finditer(r"(20\d{2}) Cumulative \(Week\s*\d+\)\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%?", flat, re.I):
            rows.extend(_row(label, c, _count(v), year=int(match[1]), week=week, report=report, family="sitrep", period="cumulative", measure=m, source_hash=source_hash) for v, (c, m) in zip(match.groups()[1:], fields))
    else:
        # Meningitis seasons cross calendar years; preserve the explicit
        # epi-week 40 season origin rather than relabel it as January YTD.
        match = re.search(r"Cumulative \(Epi week (\d+)\s*[-–]\s*\d+ of (20\d{2})/(20\d{2})\)\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", flat, re.I)
        if match:
            start = date.fromisocalendar(int(match[2]), int(match[1]), 1)
            rows.extend(_row(label, c, _count(v), year=year, week=week, report=report, family="sitrep", period=f"cumulative_season_week{match[1]}", frequency="weekly", period_start=start.isoformat(), measure=m, source_hash=source_hash) for v, (c, m) in zip(match.groups()[3:], fields))
    return rows, {"year": year, "week": week, "parsed_categories": [disease_identity(label)], "issues": [], "coverage": "verified national current-week/explicit cumulative summary; serotypes, state and age graphics retained in raw archive"}


def _parse_mpox_highlights(first, report, source_hash):
    flat = " ".join(first.split())
    if re.search(r"Confirmed\s*\(Suspected\)\s*cases\s+Death\s*\(Confirmed\s*cases\)", flat, re.I):
        return _parse_mpox_paired_table(flat, report, source_hash)
    if re.search(r"Suspected\s+cases\s+Confirmed\s+cases\s+Deaths\s*\(Confirmed\s+cases\)", flat, re.I):
        return _parse_mpox_separate_table(flat, report, source_hash)
    if re.search(r"Total\s+confirmed\s+cases\s+in\s+Epi\s*Week\s*\d+,\s*20\d{2}", flat, re.I):
        return _parse_mpox_indicators(flat, report, source_hash)
    period = re.search(r"Cumulative Summary \(Weeks?\s*1\s*[-–]\s*(\d+),\s*(20\d{2})\)", flat, re.I)
    if not period:
        raise NigeriaNCDCContractError("No explicit mpox current-week/YTD summary period")
    week, year = int(period[1]), int(period[2])
    report = dict(report, report_year=year, report_week=week)
    current = re.search(r"Key Highlights for Epi Week\s*\d+\s*:(.*?)Cumulative Summary", flat, re.I)
    cumulative = re.search(r"Cumulative Summary.*?\):(.*?)(?:Mpox Trends|UPDATE ON MPOX|$)", flat, re.I)
    if not current or not cumulative:
        raise NigeriaNCDCContractError("Mpox highlight sections missing")
    rows = []
    for content, count_period in [(current[1], "weekly"), (cumulative[1], "cumulative")]:
        for classification in ["Suspected", "Confirmed"]:
            match = re.search(classification + r" Cases:\s*([\d,]+)", content, re.I)
            if not match:
                raise NigeriaNCDCContractError("Mpox case classification highlights missing")
            rows.append(_row("Mpox", classification.lower(), _count(match[1]), year=year, week=week, report=report, family="sitrep", period=count_period, source_hash=source_hash))
        match = re.search(r"Deaths:\s*([\d,]+)", content, re.I)
        if match or re.search(r"Zero deaths were reported for the week", content, re.I):
            rows.append(_row("Mpox", "all_reported", _count(match[1]) if match else 0, year=year, week=week, report=report, family="sitrep", period=count_period, measure="deaths", source_hash=source_hash))
    return rows, {"year": year, "week": week, "parsed_categories": ["mpox"], "issues": [], "coverage": "national suspected/confirmed/death highlights; co-infections and state breakdown retained in raw archive"}


def _parse_mpox_paired_table(flat, report, source_hash):
    current = re.search(r"(20\d{2})\s*Current\s*Week\s*(\d+)\s+([\d,]+)\s*\(([\d,]+)\)\s+([\d,]+)\s+[\d.]+", flat, re.I)
    if not current:
        raise NigeriaNCDCContractError("Mpox confirmed(suspected) current table row missing")
    year, week = int(current[1]), int(current[2])
    report = dict(report, report_year=year, report_week=week)
    fields = [("confirmed", "cases"), ("suspected", "cases"), ("confirmed", "deaths")]
    rows = [_row("Mpox", c, _count(v), year=year, week=week, report=report, family="sitrep", measure=m, source_hash=source_hash) for v, (c, m) in zip(current.groups()[2:], fields)]
    for match in re.finditer(r"(20\d{2})\s*Cumulative\s*Week\s*1\s*[-–]\s*(\d+)\s+([\d,]+)\s*\(([\d,]+)\)\s+([\d,]+)\s+[\d.]+", flat, re.I):
        cyear, cweek = int(match[1]), int(match[2])
        rows.extend(_row("Mpox", c, _count(v), year=cyear, week=cweek, report=report, family="sitrep", period="cumulative", measure=m, source_hash=source_hash) for v, (c, m) in zip(match.groups()[2:], fields))
    return rows, {"year": year, "week": week, "parsed_categories": ["mpox"], "issues": [], "coverage": "verified confirmed(suspected) summary table; comparative full-year endpoint preserved"}


def _parse_mpox_separate_table(flat, report, source_hash):
    current = re.search(r"(20\d{2})\s*Current\s*Week\s*(\d+)(?:\s*[-–]\s*(\d+))?\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+", flat, re.I)
    if not current:
        raise NigeriaNCDCContractError("Mpox separate-classification current row missing")
    year, begin = int(current[1]), int(current[2])
    week = int(current[3] or begin)
    report = dict(report, report_year=year, report_week=week)
    fields = [("suspected", "cases"), ("confirmed", "cases"), ("confirmed", "deaths")]
    rows = [_row("Mpox", c, _count(v), year=year, week=week, report=report, family="sitrep", period="weekly" if begin == week else f"weeks_{begin}_{week}", frequency="weekly" if begin == week else "multiweek", period_start=date.fromisocalendar(year, begin, 1).isoformat(), measure=m, source_hash=source_hash) for v, (c, m) in zip(current.groups()[3:], fields)]
    for match in re.finditer(r"(20\d{2})\s*Cumulative\s*Week\s*(?:1\s*[-–]\s*)?(\d+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+", flat, re.I):
        rows.extend(_row("Mpox", c, _count(v), year=int(match[1]), week=int(match[2]), report=report, family="sitrep", period="cumulative", measure=m, source_hash=source_hash) for v, (c, m) in zip(match.groups()[2:], fields))
    return rows, {"year": year, "week": week, "parsed_categories": ["mpox"], "issues": [], "coverage": "verified suspected/confirmed summary table; multiweek interval and comparative year endpoints preserved"}


def _parse_mpox_indicators(flat, report, source_hash):
    current = re.search(r"Total\s+confirmed\s+cases\s+in\s+Epi\s*Week\s*(\d+),\s*(20\d{2})\s+([\d,]+)", flat, re.I)
    if not current:
        raise NigeriaNCDCContractError("Mpox current-week indicator value missing")
    week, year = int(current[1]), int(current[2])
    report = dict(report, report_year=year, report_week=week)
    rows = [_row("Mpox", "confirmed", _count(current[3]), year=year, week=week, report=report, family="sitrep", source_hash=source_hash)]
    suspected = re.search(r"(" + _PROSE_NUMBER + r")\s+new\s+suspected\s+cases\s+(?:were\s+)?reported\s+in\s+Epi\s*week\s*" + str(week) + r",\s*" + str(year), flat, re.I)
    if suspected:
        rows.append(_row("Mpox", "suspected", _prose_count(suspected[1]), year=year, week=week, report=report, family="sitrep", source_hash=source_hash))
    pattern = r"Total\s+(suspected\s+cases|confirmed\s+cases|deaths)\s+from\s+January\s*1\s*(?:st)?\s+to\s+[A-Za-z]+\s+\d+\s*(?:st|nd|rd|th)?,\s*(20\d{2})\s*\(Epi\s*week\s*1\s*to\s*(\d+)\)\s*([\d,]+)"
    for match in re.finditer(pattern, flat, re.I):
        wording = match[1].casefold()
        rows.append(_row("Mpox", "suspected" if wording.startswith("suspected") else "confirmed" if wording.startswith("confirmed") else "all_reported", _count(match[4]), year=int(match[2]), week=int(match[3]), report=report, family="sitrep", period="cumulative", measure="deaths" if wording == "deaths" else "cases", source_hash=source_hash))
    return rows, {"year": year, "week": week, "parsed_categories": ["mpox"], "issues": [], "coverage": "explicit current-week and January-start national indicators; multi-year outbreak totals retained raw"}


def _parse_covid_tables(pages, report, source_hash):
    first = " ".join(pages[0].split())
    date_pattern = r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*([A-Za-z]+)\s*(20\d{2})"
    match = re.search(r"MIDNIGHT\s*" + date_pattern, first, re.I)
    if not match:
        match = re.search(date_pattern, first, re.I)
    if not match or not _month_number(match[2]):
        raise NigeriaNCDCContractError("No explicit COVID report date")
    when = date(int(match[3]), _month_number(match[2]), int(match[1]))
    year, week, _ = when.isocalendar()
    report = dict(report, report_year=year, report_week=week, report_date=when.isoformat())
    text = "\n".join(pages)
    table = re.search(r"Table\s*1:\s*States\s*with\s*reported\s*laboratory.confirmed\s*COVID.19\s*cases,\s*recoveries,\s*deaths(?P<body>.*?)(?=Table\s*2:|SELECTED\s*CHARTS|$)", text, re.I | re.S)
    if not table:
        raise NigeriaNCDCContractError("COVID national classification table not present")
    body = table["body"]
    if not all(word in body.upper() for word in ["CONFIRMED", "DEATHS"]):
        raise NigeriaNCDCContractError("COVID national table classification columns changed")
    totals = re.findall(r"^Total\s+((?:[\d,]+[*†]?\s+){6,8}[\d,]+[*†]?)\s*$", body, re.M | re.I)
    if len(totals) != 1:
        raise NigeriaNCDCContractError("No unique COVID national total row")
    values = [_count(token.rstrip("*†")) for token in totals[0].split()]
    if len(values) not in {7, 8, 9}:
        raise NigeriaNCDCContractError("COVID national total columns changed")
    fields = [(0, "confirmed", "cases"), (2, "confirmed", "recoveries"), (4, "confirmed", "deaths")]
    if len(values) in {8, 9}:
        header = re.split(r"\bAbia\b|\bLagos\b", body, maxsplit=1, flags=re.I)[0].upper()
        header = re.split(r"\bSTATES\b", header, maxsplit=1)[-1]
        # Older tables place two testing columns before active cases; newer
        # tables place the single active column before the testing pair.
        active = re.search(r"ACTIVE\s*CASES", header)
        testing = header.find("TESTING")
        position = 7 if len(values) == 9 and active and active.start() < testing else 6
        if "TESTING" in header:
            fields.append((position, "all_tested", "tests"))
        elif not re.search(r"SAMPLES\s*COLLECTED|SAMPLES\s*TESTED", header):
            raise NigeriaNCDCContractError("COVID additional table columns unverified")
    rows = []
    issues = []
    for position, classification, measure in fields:
        rows.append(_row("COVID-19", classification, values[position], year=year, week=week, report=report, family="sitrep", period="cumulative_daily", frequency="daily", period_start="2020-02-27", period_end=when.isoformat(), measure=measure, source_hash=source_hash))
    if re.search(r"New\s*in\s*the\s*last\s*24\s*hours", first, re.I):
        for position, classification, measure in fields[:3]:
            rows.append(_row("COVID-19", classification, values[position + 1], year=year, week=week, report=report, family="sitrep", period="daily", frequency="daily", period_start=when.isoformat(), period_end=when.isoformat(), measure=measure, source_hash=source_hash))
    # Biweekly tables publish the constituent daily counts explicitly. Import
    # these only when their vector reconciles to its published total; never
    # derive daily or weekly incidence by subtracting cumulative snapshots.
    interval = re.search(r"Epi Week\s+\d+\s*[-–]\s*\d+\s*:\s*(\d+)\s*(?:st|nd|rd|th)?\s*[-–]\s*(\d+)\s*(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})", first, re.I)
    if interval:
        start = date(int(interval[4]), _month_number(interval[3]), int(interval[1]))
        end = date(int(interval[4]), _month_number(interval[3]), int(interval[2]))
        length = (end - start).days + 1
        if 1 <= length <= 14 and end == when:
            daily_sections = [("DAILY CONFIRMED CASES", "DAILY RECOVERIES", "cases"), ("DAILY RECOVERIES", "DAILY FATALITIES", "recoveries"), ("DAILY FATALITIES", "DAILY EPICURVE", "deaths")]
            for begin, finish, measure in daily_sections:
                section = re.search(re.escape(begin) + r"(.*?)" + re.escape(finish), text, re.S | re.I)
                if not section:
                    continue
                national = re.findall(r"^Total\s+((?:[\d,]+\s+){" + str(length) + r"}[\d,]+)\s*$", section[1], re.M)
                if len(national) != 1:
                    continue
                vector = [_count(token) for token in national[0].split()]
                if vector[0] != sum(vector[1:]):
                    issues.append({"error": "COVID explicit daily vector does not reconcile to published total", "measure": measure})
                    continue
                for offset, value in enumerate(vector[1:]):
                    day = start + timedelta(days=offset)
                    rows.append(_row("COVID-19", "confirmed", value, year=day.isocalendar().year, week=day.isocalendar().week, report=report, family="sitrep", period="daily", frequency="daily", period_start=day.isoformat(), period_end=day.isoformat(), measure=measure, source_hash=source_hash))
    return rows, {"year": year, "week": week, "report_date": when.isoformat(), "parsed_categories": ["covid_19"], "issues": issues, "coverage": "verified national COVID table totals and explicitly published daily vectors; active-case estimates, contacts and state/age/sex tables retained in raw archive"}


def _parse_yellow_fever_monthly(pages, report, source_hash):
    flat = " ".join("\n".join(pages[:2]).split())
    heading = re.search(r"REPORTING MONTH:\s*([A-Za-z]+)\s+(20\d{2})", flat, re.I)
    if not heading or not _month_number(heading[1]):
        return _parse_yellow_fever_weekly(flat, report, source_hash)
    year, month = int(heading[2]), _month_number(heading[1])
    report = dict(report, report_year=year, report_week=date(year, month, 1).isocalendar().week)
    sections = re.split(r"CUMULATIVE FOR\s*1\s*ST\s+JANUARY", flat, maxsplit=1, flags=re.I)
    if len(sections) != 2:
        raise NigeriaNCDCContractError("Yellow fever monthly and YTD sections are not separable")
    rows = []
    for section, cumulative in zip(sections, [False, True]):
        suspected = re.search(r"A total of\s*([\d,]+)\s+suspected cases", section, re.I)
        if not suspected:
            raise NigeriaNCDCContractError("Yellow fever suspected-count statement missing")
        rows.append(_monthly_row("Yellow Fever", "suspected", _count(suspected[1]), year=year, month=month, report=report, source_hash=source_hash, cumulative=cumulative))
        zero_confirmed = re.search(r"No confirmed case was (?:recorded|reported)", section, re.I)
        confirmed = re.search(r"\(([\d,]+)\)\s+confirmed cases?\s+(?:were|was)\s+(?:recorded|reported)", section, re.I)
        if zero_confirmed or confirmed:
            rows.append(_monthly_row("Yellow Fever", "confirmed", _count(confirmed[1]) if confirmed else 0, year=year, month=month, report=report, source_hash=source_hash, cumulative=cumulative))
        if re.search(r"No death (?:was|has been) recorded", section, re.I):
            rows.append(_monthly_row("Yellow Fever", "suspected_and_confirmed", 0, year=year, month=month, report=report, source_hash=source_hash, cumulative=cumulative, measure="deaths"))
    return rows, {"year": year, "month": month, "parsed_categories": ["yellow_fever"], "issues": [], "coverage": "explicit national monthly/YTD suspected, confirmed and zero-death statements; presumptive positives, inconclusive tests and state/age/sex tables retained in raw archive"}


def _parse_yellow_fever_weekly(flat, report, source_hash):
    period = re.search(r"EPI.WEEK:\s*(\d+).*?DATE:.*?(20\d{2})", flat, re.I)
    section = re.search(r"In\s+this\s+reporting\s+week.*?:(.*?)(?:SUMMARY\s+OF\s+OUTBREAK|$)", flat, re.I)
    if not period or not section:
        raise NigeriaNCDCContractError("No explicit yellow fever reporting month or weekly summary")
    week, year = int(period[1]), int(period[2])
    report = dict(report, report_year=year, report_week=week)
    suspected = re.search(r"([\d,]+)\s+suspected\s+cases\s+were\s+(?:recorded|reported)", section[1], re.I)
    if not suspected:
        raise NigeriaNCDCContractError("Yellow fever weekly suspected statement missing")
    rows = [_row("Yellow Fever", "suspected", _count(suspected[1]), year=year, week=week, report=report, family="sitrep", source_hash=source_hash)]
    if re.search(r"No\s+new\s+confirmed\s+case\s+from\s+Institut\s+Pasteur\s*\(IP\)\s*Dakar", section[1], re.I):
        rows.append(_row("Yellow Fever", "ip_dakar_confirmed", 0, year=year, week=week, report=report, family="sitrep", source_hash=source_hash))
    return rows, {"year": year, "week": week, "parsed_categories": ["yellow_fever"], "issues": [], "coverage": "explicit weekly national suspected and IP Dakar confirmation statements; cross-year outbreak totals retained raw"}


def _parse_rubella_cards(pages, report, source_hash):
    flat = " ".join(pages[0].split())
    heading = re.search(r"Data as at\s+([A-Za-z]+)\s+\d+\s*(?:st|nd|rd|th)?\s+(20\d{2})", flat, re.I)
    if not heading or not _month_number(heading[1]) or not re.search(r"Total\s*\(New in this month\)", flat, re.I):
        raise NigeriaNCDCContractError("Rubella source does not establish total/current-month card semantics")
    year, month = int(heading[2]), _month_number(heading[1])
    report = dict(report, report_year=year, report_week=date(year, month, 1).isocalendar().week)
    rows = []
    for label, measure in [("LAB CONFIRMED CASES", "cases"), ("DEATHS AMONG LAB CONFIRMED CASES", "deaths")]:
        match = re.search(label + r"\s*([\d,]+)\s*\(([\d,]+)\)", flat, re.I)
        if not match:
            raise NigeriaNCDCContractError("Rubella lab-confirmed case/death cards missing")
        for value, cumulative in zip(match.groups(), [True, False]):
            rows.append(_monthly_row("Rubella", "lab_confirmed", _count(value), year=year, month=month, report=report, source_hash=source_hash, cumulative=cumulative, measure=measure))
    return rows, {"year": year, "month": month, "parsed_categories": ["rubella"], "issues": [], "coverage": "explicit national lab-confirmed case/death cards; tested-count discrepancies and congenital-rubella sentinel periods require separate contracts"}


def _parse_cholera_highlights(pages, report, source_hash):
    flat = " ".join("\n".join(pages[:2]).split())
    if re.search(r"Table\s*1:\s*Summary\s+of\s+current\s+week", flat, re.I):
        return _parse_cholera_summary_tables(flat, report, source_hash)
    period = re.search(r"Cholera Situation Report Epi Week:\s*(\d+),?\s*(20\d{2})", flat, re.I)
    if not period:
        raise NigeriaNCDCContractError("No explicit Cholera report epidemiological week")
    week, year = int(period[1]), int(period[2])
    report = dict(report, report_year=year, report_week=week)
    current = re.search(r"Week\s+" + str(week) + r"\s+Highlight\s*[•▪]?\s*([\d,]+)\s+new suspected cases were reported,\s*([\d,]+)\s+deaths?", flat, re.I)
    if not current:
        raise NigeriaNCDCContractError("No explicit Cholera current-week highlight counts")
    rows = [_row("Cholera", "suspected", _count(value), year=year, week=week, report=report, family="sitrep", measure=measure, source_hash=source_hash) for value, measure in zip(current.groups(), ["cases", "deaths"])]
    cumulative = re.search(r"Cumulative Epi.Summary for\s*" + str(year) + r"(.*?)(?:Figure\s*1|$)", flat, re.I)
    if cumulative:
        counts = re.search(r"a total of\s*([\d,]+)\s+suspected cases including\s*([\d,]+)\s+deaths", cumulative[1], re.I)
        if counts:
            rows.extend(_row("Cholera", "suspected", _count(value), year=year, week=week, report=report, family="sitrep", period="cumulative", measure=measure, source_hash=source_hash) for value, measure in zip(counts.groups(), ["cases", "deaths"]))
    return rows, {"year": year, "week": week, "parsed_categories": ["cholera"], "issues": [], "coverage": "explicit national weekly suspected/death highlights and YTD; epi-week-based nominal months, laboratory methods and state/age/sex tables retained in raw archive"}


def _parse_cholera_summary_tables(flat, report, source_hash):
    period = re.search(r"Summary\s+of\s+current\s+week\s*\(Epi\s*week\s*(\d+),\s*(20\d{2})\)", flat, re.I)
    if not period:
        raise NigeriaNCDCContractError("Cholera national table reporting week missing")
    week, year = int(period[1]), int(period[2])
    report = dict(report, report_year=year, report_week=week)
    rows = []
    for pattern, cumulative in [(r"Table\s*1:(.*?)(?=Table\s*2:)", False), (r"Table\s*3:(.*?)(?=Week\s*\d+\s*Highlights|$)", True)]:
        section = re.search(pattern, flat, re.I)
        if not section or not re.search(r"Suspected\s*Cases\s*Deaths\s*\(Suspected\s*cases\)", section[1], re.I):
            if not cumulative:
                raise NigeriaNCDCContractError("Cholera national classification columns changed")
            continue
        values = re.findall(r"(?<![\w,])([\d,]+)\s+([\d,]+)\s+[\d.]+%\s+\d+\s+\d+", section[1])
        if len(values) != 1:
            if not cumulative:
                raise NigeriaNCDCContractError("Cholera current national count row changed")
            continue
        if cumulative and not re.search(r"Cumulative\s+summary\s+from\s+Epi\s*week\s*1\s*[-–]\s*" + str(week) + r",\s*" + str(year), section[1], re.I):
            continue
        rows.extend(_row("Cholera", "suspected", _count(v), year=year, week=week, report=report, family="sitrep", period="cumulative" if cumulative else "weekly", measure=m, source_hash=source_hash) for v, m in zip(values[0], ["cases", "deaths"]))
    return rows, {"year": year, "week": week, "parsed_categories": ["cholera"], "issues": [], "coverage": "verified national current-week and January-start cumulative tables; nominal months retain raw epidemiological intervals"}


_PROSE_NUMBERS = {**{k: int(v) for k, v in _NUMBER_WORDS.items()}, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
_PROSE_NUMBER = r"(?:\d[\d,]*|(?:" + "|".join(_PROSE_NUMBERS) + r")(?:[-\s]+(?:" + "|".join(_PROSE_NUMBERS) + r")){0,5})(?:\s*\(\s*\d[\d,]*\s*\))?"


def _prose_count(token):
    explicit = re.search(r"\(([\d,]+)\)", token)
    if explicit:
        return _count(explicit[1])
    if re.fullmatch(r"[\d,]+", token):
        return _count(token)
    current, total = 0, 0
    for word in re.split(r"[-\s]+", token.casefold()):
        value = _PROSE_NUMBERS[word]
        if value == 100:
            current *= value
        elif value == 1000:
            total += current * value
            current = 0
        else:
            current += value
    return total + current


def _parse_lassa_narrative(first, report, source_hash):
    flat = " ".join(first.split())
    # Scope every weekly statement to its own bullet. Outbreak cumulative
    # totals, including outbreaks beginning in December, are not January YTD.
    match = re.search(r"In\s+(?:the\s+)?reporting\s+Week\s*(\d+)\s*\(([^)]*20\d{2})\)(.*?)(?=[•▪]|$)", flat, re.I)
    if not match:
        raise NigeriaNCDCContractError("No explicit Lassa year/week")
    week = int(match[1])
    year = int(re.search(r"20\d{2}", match[2])[0])
    report = dict(report, report_year=year, report_week=week)
    rows = []
    content = match[3]
    fields = [("suspected", "cases", r"(?:new\s+)?suspected(?:i+)?\s*cases"), ("confirmed", "cases", r"(?:new\s+)?(?:laboratory\s+)?confirmed(?:i+)?\s*cases"), ("all_reported", "deaths", r"(?:new\s+)?deaths")]
    for classification, measure, wording in fields:
        value = re.search(r"(?<![\w-])(" + _PROSE_NUMBER + r")\s*" + wording, content, re.I)
        if value:
            rows.append(_row("Lassa Fever", classification, _prose_count(value[1]), year=year, week=week, report=report, family="sitrep", measure=measure, source_hash=source_hash))
    # Explicit January-start national statements establish this year's YTD.
    cumulative = re.search(r"From\s*1\s*(?:st)?\s+(?:January\s+to\s*\d+\s*(?:st|nd|rd|th)?\s+[A-Za-z]+|to\s*\d+\s*(?:st|nd|rd|th)?\s+January)\s*" + str(year) + r",?\s*a\s+total\s+of\s+([\d,]+)\s*suspected(?:i+)?\s*cases(.*?)(?=[•▪]|$)", flat, re.I)
    if cumulative:
        rows.append(_row("Lassa Fever", "suspected", _count(cumulative[1]), year=year, week=week, report=report, family="sitrep", period="cumulative", source_hash=source_hash))
        confirmed = re.search(r"Of these,?\s*([\d,]+)\s*(?:were\s*)?confirmed\s*positive", cumulative[2], re.I)
        if confirmed:
            rows.append(_row("Lassa Fever", "confirmed", _count(confirmed[1]), year=year, week=week, report=report, family="sitrep", period="cumulative", source_hash=source_hash))
    deaths = re.search(r"Since\s+the\s+onset\s+of\s+the\s*" + str(year) + r"\s*outbreak,?\s*there\s+have\s+been\s*([\d,]+)\s*deaths\s+in\s+confirmed\s+cases", flat, re.I)
    if deaths:
        rows.append(_row("Lassa Fever", "confirmed", _count(deaths[1]), year=year, week=week, report=report, family="sitrep", period="cumulative", measure="deaths", source_hash=source_hash))
    if not rows:
        raise NigeriaNCDCContractError("Lassa legacy national summary counts missing")
    return rows, {"year": year, "week": week, "parsed_categories": ["lassa_fever"], "issues": [], "coverage": "explicit national reporting-week prose and explicit January-start statements; cross-year outbreak totals retained raw"}


def parse_sitrep_pages(pages, report, *, source_hash=""):
    """Dispatch independently verified table/text contracts by archive category.

    Unrecognized layouts stay explicit audit gaps with their complete originals.
    """
    category = str(report.get("category_id"))
    if category in {"11", "19"}:
        return _parse_monthly_classification_table(pages[0], report, source_hash)
    if category in {"6", "18"}:
        return _parse_weekly_classification_sitrep(pages[0], report, source_hash)
    if category == "8":
        return _parse_mpox_highlights(pages[0], report, source_hash)
    if category == "14":
        return _parse_covid_tables(pages, report, source_hash)
    if category == "10":
        return _parse_yellow_fever_monthly(pages, report, source_hash)
    if category == "13":
        return _parse_rubella_cards(pages, report, source_hash)
    if category == "7":
        return _parse_cholera_highlights(pages, report, source_hash)
    if category != "5":
        raise NigeriaNCDCContractError("Situation report numerical layout not yet verified; raw document retained")
    first = pages[0]
    if re.search(r"Lassa\s+fever", first, re.I) and re.search(r"In\s+(?:the\s+)?reporting\s+Week", first, re.I) and not re.search(r"Suspected\s*cases\s*(?:\(Negative\)\s*)?Confirmed\s*cases\s*(?:Probable\s*cases\s*)?Deaths", first, re.I):
        return _parse_lassa_narrative(first, report, source_hash)
    period = re.search(r"Lassa\s*Fever\s*Situation\s*Report\s*Epi\s*Week\s*:?\s*(\d{1,2})\s*(20\d{2})", first, re.I)
    if not period:
        period = re.search(r"Epi\s*Week\s*(\d{1,2}).*?(20\d{2})", first, re.I | re.S)
    if not period:
        raise NigeriaNCDCContractError("No explicit Lassa year/week")
    week, year = int(period[1]), int(period[2])
    if re.search(r"Suspected\s*cases\s*\(Negative\)\s*Confirmed\s*cases\s*Deaths", first, re.I):
        return _parse_lassa_negative_table(first, report, source_hash, year, week)
    if not re.search(r"Suspected\s*cases\s*Confirmed\s*cases\s*Probable\s*cases\s*Deaths\s*\(Confirmed\s*cases\)", first, re.I):
        raise NigeriaNCDCContractError("Lassa summary classification columns changed")
    table = re.search(r"Current\s*week\s*\(week\s*" + str(week) + r"\)\s*(.*)", first, re.S | re.I)
    if not table:
        raise NigeriaNCDCContractError("Lassa current-week/cumulative table missing")
    content = table[1]
    current = re.match(r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)(?=\s|$)", content.strip())
    if not current:
        raise NigeriaNCDCContractError("Lassa current-week counts missing")
    report = dict(report, report_year=year, report_week=week)
    fields = [("suspected", "cases"), ("confirmed", "cases"), ("probable", "cases"), ("confirmed", "deaths")]
    rows = [_row("Lassa Fever", c, _count(v), year=year, week=week, report=report, measure=m, family="sitrep", source_hash=source_hash) for v, (c, m) in zip(current.groups(), fields)]
    for match in re.finditer(r"(20\d{2})\s*Cumulative\s*\(week\s*(?:1\s*[-–]\s*)?(\d+)\)\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)(?=\s|$)", content, re.S | re.I):
        for value, (classification, measure) in zip(match.groups()[2:], fields):
            rows.append(_row("Lassa Fever", classification, _count(value), year=int(match[1]), week=int(match[2]), report=report, period="cumulative", measure=measure, family="sitrep", source_hash=source_hash))
    return rows, {"year": year, "week": week, "parsed_categories": ["lassa_fever"], "issues": [], "coverage": "verified_national_summary_table; state/age/sex graphics retained in raw archive"}


def _parse_lassa_negative_table(first, report, source_hash, year, week):
    report = dict(report, report_year=year, report_week=week)
    section = re.search(r"Table\s*1:(.*?)Table\s*2:", first, re.S | re.I)
    if not section:
        raise NigeriaNCDCContractError("Lassa suspected(negative) current table missing")
    matches = re.findall(r"(?:^|\n)\s*([\d,]+)\s*\([\d,]+\)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", section[1])
    if len(matches) != 1:
        raise NigeriaNCDCContractError("Lassa suspected(negative) current count row changed")
    fields = [("suspected", "cases"), ("confirmed", "cases"), ("confirmed", "deaths")]
    rows = [_row("Lassa Fever", c, _count(v), year=year, week=week, report=report, family="sitrep", measure=m, source_hash=source_hash) for v, (c, m) in zip(matches[0], fields)]
    # Repeated explicit interleaved year headers prove each numeric column.
    pattern = r"(20\d{2})\s+(20\d{2})\s+\1\s+\2\s+\1\s+\2\s+\1\s+\2\s+([\d,]+)\s*\([\d,]+\)\s+([\d,]+)\s*\([\d,]+\)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%\s+[\d.]+%"
    cumulative = re.search(pattern, first, re.S)
    if cumulative and re.search(r"week\s*1\s*[-–]\s*current\s*week", first, re.I):
        values = cumulative.groups()[2:]
        for offset, cyear in enumerate(cumulative.groups()[:2]):
            for position, (classification, measure) in enumerate(fields):
                rows.append(_row("Lassa Fever", classification, _count(values[position * 2 + offset]), year=int(cyear), week=week, report=report, family="sitrep", period="cumulative", measure=measure, source_hash=source_hash))
    return rows, {"year": year, "week": week, "parsed_categories": ["lassa_fever"], "issues": [], "coverage": "verified suspected(negative) national tables; negative tests excluded and comparative years verified"}


def parse_report(payload, report):
    pages = extract_pdf_pages(payload)
    if report["kind"] == "weekly" and re.search(r"Summary Table\s*\(IDSR Weekly Report", pages[0], re.I):
        return _parse_legacy_idsr_table(payload, pages, report)
    parser = parse_weekly_pages if report["kind"] == "weekly" else parse_sitrep_pages
    return parser(pages, report, source_hash=hashlib.sha256(payload).hexdigest())


def _parse_legacy_idsr_table(payload, pages, report):
    """Legacy merged-cell IDSR summary; verify every header and row width."""
    import pdfplumber
    year, week = _weekly_period(pages, report)
    with pdfplumber.open(io.BytesIO(payload)) as document:
        tables = document.pages[0].extract_tables()
    candidates = [row for table in tables for row in table if len(row) == 5 and row[0] and row[0].startswith("Disease\n") and row[1] and row[1].startswith("Variables\n")]
    if len(candidates) != 1:
        raise NigeriaNCDCContractError("Legacy IDSR merged summary table structure changed")
    cells = candidates[0]
    diseases = cells[0].splitlines()[1:]
    variables = cells[1].splitlines()[1:]
    previous = cells[2].splitlines()
    current = cells[3].splitlines()
    cumulative = cells[4].splitlines()
    if len(variables) != len(diseases) * 3:
        raise NigeriaNCDCContractError("Legacy IDSR disease/measure row alignment changed")
    prior_header = re.fullmatch(r"Week\s+(\d+)", previous[0])
    current_header = re.fullmatch(r"Week\s+(\d+)", current[0])
    current_years = re.fullmatch(r"(20\d{2})\s+(20\d{2})", current[1])
    cumulative_years = re.fullmatch(r"0?1\s*-\s*(\d+),\s*(20\d{2})\s+0?1\s*-\s*(\d+),\s*(20\d{2})", cumulative[1])
    if not all([prior_header, current_header, current_years, cumulative_years]):
        raise NigeriaNCDCContractError("Legacy IDSR epidemiological column headers changed")
    prior_week, prior_year = int(prior_header[1]), int(previous[1])
    if int(current_header[1]) != week or int(current_years[1]) != year or (int(cumulative_years[1]), int(cumulative_years[2]), int(cumulative_years[3]), int(cumulative_years[4])) != (week, year, week, int(current_years[2])):
        raise NigeriaNCDCContractError("Legacy IDSR summary/PDF periods conflict")
    if any(len(column[2:]) != len(variables) for column in [previous, current, cumulative]):
        raise NigeriaNCDCContractError("Legacy IDSR count columns are truncated")
    report = dict(report, report_year=year, report_week=week)
    rows = []
    digest = hashlib.sha256(payload).hexdigest()
    for offset, variable in enumerate(variables):
        label = diseases[offset // 3]
        if variable == "CFR":
            continue
        if variable not in {"Cases", "Deaths"} and not re.fullmatch(r"WPV Types?\s+[13](?:\s*&\s*3)?", variable):
            raise NigeriaNCDCContractError("Unreviewed legacy IDSR variable")
        measure = "deaths" if variable == "Deaths" else "cases"
        classification = "idsr_reported" if variable in {"Cases", "Deaths"} else _slug(variable)
        values = previous[2 + offset].split() + current[2 + offset].split() + cumulative[2 + offset].split()
        if len(values) != 5:
            raise NigeriaNCDCContractError("Legacy IDSR count row width changed")
        periods = [(prior_year, prior_week, "weekly"), (year, week, "weekly"), (int(current_years[2]), week, "weekly"), (year, week, "cumulative"), (int(current_years[2]), week, "cumulative")]
        for value, (observed_year, observed_week, count_period) in zip(values, periods):
            rows.append(_row(label, classification, _count(value), year=observed_year, week=observed_week, report=report, family="wer", period=count_period, measure=measure, source_hash=digest))
    issues = []
    if report.get("year") != year or report.get("week") != week:
        issues.append({"error": "Route/PDF reporting period differs", "route_year": report.get("year"), "route_week": report.get("week"), "pdf_year": year, "pdf_week": week})
    return rows, {"year": year, "week": week, "parsed_categories": sorted({disease_identity(label) for label in diseases}), "issues": issues, "coverage": "verified legacy national IDSR summary table; case classification remains source-reported; CFR, maps and demographic graphics retained in raw archive"}


class NigeriaNCDCCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=60, delay=0.15, **kwargs)

    def get(self, url, **kwargs):
        kwargs["headers"] = {"Connection": "close", **kwargs.get("headers", {})}
        for attempt in range(3):
            response = super().get(url, **kwargs)
            if response.content:
                return response
        raise NigeriaNCDCContractError("NCDC returned three empty HTTP 200 responses")

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use the durable staged NG connector for full-archive collection")

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=response.url, raw_data={"raw_bytes": response.content})]
