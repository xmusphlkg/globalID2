"""DGHS daily dengue admissions/deaths: source table, never inferred incidence.

The dashboard URL supplied at onboarding returned 404. The public DGHS
press-release directory is the discovery authority for the linked PDFs.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
import pypdfium2 as pdfium

from src.data.crawlers.base import BaseCrawler
from src.services.situation_notices.documents import _PDFIUM_LOCK

DEFAULT_SCOPE = "bd_dghs_dengue_daily"
ONTOLOGY_SOURCE_ID = "SRC_BD_DGHS_DENGUE"
SOURCE_NAME = "Bangladesh DGHS Daily Dengue Hospital Surveillance"
DIRECTORY_URL = "https://dghs.gov.bd/pages/miscellaneous-infos"
DIRECTORY_FILTER = {"miscellaneous_info_type": "6a9cf0471fa8cd87d1f50227"}
LANDING_URL = DIRECTORY_URL + "?" + urlencode({"filters": json.dumps(DIRECTORY_FILTER, separators=(",", ":"))})
HISTORY_START_YEAR = 2026
DEFINITION_VERSION = "bd_dghs_dengue_division_hospital_daily_v1"
_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PDF_HOST = "objectstorage.ap-dcc-gazipur-1.oraclecloud15.com"
# Reviewed first-page order; aliases reflect the PDF's embedded Bengali font.
DIVISIONS = (
    ("Dhaka", "C", ("ঢাকা", "ঢোকো")),
    ("Mymensingh", "H", ("নরসিংহ", "ময়মনসিংহ", "ময়মনসিংহ", "ময়মনর্সিংহ")),
    ("Chattogram", "B", ("চট্টগ্রা", "চট্টগ্রোম")),
    ("Khulna", "D", ("খুলনা", "খযলনো")),
    ("Rajshahi", "E", ("িাজশাহী", "রাজশাহী", "োজশোহী")),
    ("Rangpur", "F", ("িিংপুি", "রংপুর", "রিংপুর", "িংপয")),
    ("Barishal", "A", ("র্রিশাল", "বরিশাল", "র্র্ শোল")),
    ("Sylhet", "G", ("রসমলট", "সিলেট", "র্সমলট")),
)


def directory_url(page=1, page_size=100):
    return DIRECTORY_URL + "?" + urlencode({"filters": json.dumps(DIRECTORY_FILTER, separators=(",", ":")), "page": int(page), "page_size": int(page_size)})


def validate_pdf_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != _PDF_HOST
            or "/o/office-dghs/" not in parsed.path or not parsed.path.lower().endswith(".pdf")):
        raise ValueError("DGHS PDF left its reviewed government publication storage")
    return url


def discover_reports(payload, *, base_url=LANDING_URL):
    soup = BeautifulSoup(payload, "lxml")
    table = soup.select_one("table#noticeTable")
    if table is None:
        raise ValueError("DGHS press-release table is absent")
    reports, seen = [], set()
    for tr in table.select("tbody tr"):
        title = tr.select_one('[data-column="title"]')
        if title is None or title.name != "td":
            continue
        label = " ".join(title.get_text(" ", strip=True).split())
        if "ডেঙ্গু" not in label or "প্রেস" not in label:
            raise ValueError("DGHS directory returned an unrelated document")
        # Some official titles omit the closing parenthesis; the explicit
        # day/month/year remains unambiguous and is cross-checked with PDF.
        match = re.search(r"\((\d{1,2})/(\d{1,2})/(20\d{2})(?:\)|$)", label.translate(_DIGITS))
        if not match:
            raise ValueError("DGHS title lacks an explicit daily report date")
        when = date(int(match[3]), int(match[2]), int(match[1]))
        published = tr.select_one('td[data-column="publish_date"]')
        if published is None:
            raise ValueError("DGHS publication date missing")
        try:
            published_date = datetime.strptime(published.get_text(strip=True).translate(_DIGITS), "%d-%m-%Y").date()
        except ValueError as exc:
            raise ValueError("DGHS publication date invalid") from exc
        if published_date < when:
            raise ValueError("DGHS publication precedes report date")
        links = tr.select('td[data-column="files"] a[href]')
        if len(links) != 1:
            raise ValueError("DGHS daily release requires exactly one PDF")
        url = validate_pdf_url(urljoin(base_url, links[0]["href"]))
        if url in seen:
            raise ValueError("DGHS directory duplicated a PDF")
        seen.add(url)
        reports.append({"title": label, "date": when.isoformat(), "publication_date": published_date.isoformat(), "url": url})
    if not reports:
        raise ValueError("DGHS dengue directory has no dated PDFs")
    pages = [int(a["data-page"]) for a in soup.select('.pagination a[data-page]')]
    return reports, max(pages, default=1)


def extract_summary_text(payload):
    if not payload.startswith(b"%PDF"):
        raise ValueError("DGHS publication response is not PDF")
    with _PDFIUM_LOCK, pdfium.PdfDocument(payload) as pdf:
        if not 1 <= len(pdf) <= 30:
            raise ValueError("Unexpected DGHS PDF page count")
        page = pdf[0]
        textpage = page.get_textpage()
        try:
            return textpage.get_text_range().replace("\r\n", "\n")
        finally:
            textpage.close()
            page.close()


def parse_summary_text(text, *, report_date, provenance=None, as_of=None):
    """Parse the eight divisions and source-stated national total on page one."""
    when = date.fromisoformat(report_date)
    if as_of and when > as_of:
        raise ValueError("DGHS report lies after retrieval date")
    plain = text.translate(_DIGITS)
    dates = re.findall(r"(?:তারিখ|তারিখ:)\s*:?[ ]*(\d{1,2}-\d{1,2}-\d{2,4})", plain)
    if len(dates) != 1:
        raise ValueError("DGHS first-page date missing or ambiguous")
    fmt = "%d-%m-%y" if len(dates[0].split("-")[-1]) == 2 else "%d-%m-%Y"
    if datetime.strptime(dates[0], fmt).date() != when:
        raise ValueError("DGHS PDF date differs from its directory title")
    if "controlroomdghs@yahoo.com" not in plain or not any(label in plain for label in ("24 ঘন্টাি", "24 ঘন্টো")) or f"01-01-{when.year}" not in plain:
        raise ValueError("DGHS hospital daily/cumulative summary contract changed")
    matched = list(re.finditer(r"(?m)^([1-8])\s+([^\d\n]+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", plain))
    if len(matched) != 8:
        raise ValueError("DGHS summary requires eight complete division rows")
    values = []
    for index, (m, (name, code, aliases)) in enumerate(zip(matched, DIVISIONS), 1):
        if int(m[1]) != index or not any(alias in m[2] for alias in aliases):
            raise ValueError("DGHS division identity/order changed")
        counts = tuple(int(m[i]) for i in range(3, 9))
        values.append((name, f"country:BD:subdivision:BD-{code}", m[2].strip(), counts))
    totals = re.findall(r"(?m)^(?:সর্মি াট:\s*)?(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", plain)
    if len(totals) != 1:
        raise ValueError("DGHS source-stated national summary missing or duplicated")
    total = tuple(map(int, totals[0]))
    if total != tuple(sum(row[3][i] for row in values) for i in range(6)):
        raise ValueError("DGHS national total differs from division sums")
    for _, _, _, count in values + [("Bangladesh", "country:BD:national", "National total", total)]:
        if count[2] != count[3] + count[4] + count[5]:
            raise ValueError("DGHS cumulative admission/discharge/death/inpatient balance differs")
    provenance = provenance or {}
    rows = []
    for name, geography, raw_label, counts in [("Bangladesh", "country:BD:national", "National total", total), *values]:
        for kind, count, measure, period, basis in (
            ("daily_admissions", counts[0], "hospital_admissions", "daily", "hospital_reported_last_24_hours"),
            ("daily_deaths", counts[1], "deaths", "daily", "hospital_reported_last_24_hours"),
            ("cumulative_admissions", counts[2], "cumulative_hospital_admissions", "cumulative", "hospital_reported_year_to_date"),
            ("cumulative_deaths", counts[3], "cumulative_deaths", "cumulative", "hospital_reported_year_to_date"),
        ):
            rows.append({
                "Date": when.isoformat(), "Year": str(when.year), "Month": str(when.month),
                "PeriodStart": when.replace(month=1, day=1).isoformat() if period == "cumulative" else when.isoformat(),
                "PeriodEnd": when.isoformat(), "Frequency": "daily", "PeriodType": period,
                "DateRole": "reporting_day_label", "TimeBasis": "source_stated_last_24_hours" if period == "daily" else "year_to_date",
                "DiseaseCode": "__source_native__", "SourceDiseaseCode": f"dengue__{kind}",
                "RawDiseaseLabel": f"Dengue {kind.replace('_', ' ')}", "DefinitionVersion": DEFINITION_VERSION,
                "Cases": str(count), "Measure": measure, "Unit": "count", "ReportingBasis": basis,
                "Geography": name, "GeographyKey": geography,
                "GeographyType": "national" if name == "Bangladesh" else "subdivision",
                "SourceGeographyLabel": raw_label, "Source": SOURCE_NAME, "SourceURL": LANDING_URL,
                "SourcePublishedAt": when.isoformat(), "SourcePage": "1",
                "MissingValueStatus": "reported", "DatasetStatus": "provisional_revisable", "IsProvisional": "true",
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "AggregationPolicy": "non_additive", "PublicReleaseEnabled": "false",
                "Dimensions": json.dumps({"disease": "dengue", "source_metric": kind, "population_scope": "reporting_hospitals", "source_table": "division_summary"}, sort_keys=True),
                **provenance,
            })
    return rows, {"date": when.isoformat(), "national_daily_admissions": total[0], "national_daily_deaths": total[1], "divisions": 8, "rows": len(rows), "incidence_inferred": False}


def parse_report(payload, *, report_date, provenance=None, as_of=None):
    provenance = {**(provenance or {}), "RawSHA256": hashlib.sha256(payload).hexdigest()}
    text = extract_summary_text(payload)
    if "Daily Dengue Press Release" in text:
        return parse_dashboard_text(text, report_date=report_date, provenance=provenance, as_of=as_of)
    return parse_summary_text(text, report_date=report_date, provenance=provenance, as_of=as_of)


def parse_dashboard_text(text, *, report_date, provenance=None, as_of=None):
    """Read explicit English dashboard count labels, never plot coordinates.

    The older publication's Bengali annex lacks the eight-division daily
    death summary. It cannot supply unreported zeros or cumulative deaths.
    """
    when = date.fromisoformat(report_date)
    if as_of and when > as_of:
        raise ValueError("DGHS report lies after retrieval date")
    date_match = re.match(r"(\d{2}-[A-Za-z]{3}-20\d{2})\s*\n", text)
    if not date_match or datetime.strptime(date_match[1], "%d-%b-%Y").date() != when:
        raise ValueError("DGHS dashboard date differs from directory title")
    if not all(label in text for label in ("Government of the People's Republic of Bangladesh", "Directorate General of Health Services (DGHS)", "Daily Dengue Press Release")):
        raise ValueError("DGHS dashboard source identity changed")
    count_pattern = r"(0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)"
    cases = re.findall(r"Dengue cases of last 24 hours\nNo\.\n(\d[\d,]*)\nAdmitted\n", text)
    deaths = re.findall(r"Dengue death of last 24 hours\nNo\.\n" + count_pattern + r"\nDeath\n", text)
    if len(cases) != 1 or len(deaths) != 1:
        raise ValueError("DGHS explicit national last-24-hour value missing/ambiguous")
    # Two archived PDFs contain duplicate comma glyphs in this text label.
    # An exact repair is accepted only when both the explicit age/sex grand
    # total and all ten regional counts independently confirm that integer.
    repaired_national_label = not bool(re.fullmatch(count_pattern, cases[0]))
    if repaired_national_label and not re.fullmatch(r"[1-9]\d{0,2},,\d{3}", cases[0]):
        raise ValueError("DGHS national admission label has unreviewed numeric format")
    observations = [
        ("Bangladesh", "country:BD:national", "national", "daily_admissions", int(cases[0].replace(",", ""))),
        ("Bangladesh", "country:BD:national", "national", "daily_deaths", int(deaths[0].replace(",", ""))),
    ]
    regional_status = "partial_category_labels_not_imported"
    marker = "Division & City corporation cases of last 24 hours\nNo.\n"
    if text.count(marker) != 1:
        raise ValueError("DGHS division/city corporation chart heading changed")
    block = text.split(marker, 1)[1]
    regional_values, separator, rest = block.partition("\nAdmitted\n")
    if not separator:
        raise ValueError("DGHS regional explicit labels missing")
    # PDFium may put horizontally adjacent data labels on the same line.
    value_lines = regional_values.split()
    if any(not re.fullmatch(count_pattern, value) for value in value_lines):
        raise ValueError("DGHS regional numeric labels changed")
    # Chart software suppresses some zero categories. With fewer than ten
    # explicit category/value pairs, retain national totals only.
    if len(value_lines) == 10:
        labels = re.split(r"\n0\n", rest, maxsplit=1)[0]
        labels = " ".join(labels.split())
        expected = "Barishal Division Chattogram Division Dhaka Division (Out of CC) DNCC DSCC Khulna Division Mymensingh Division Rajshahi Division Rangpur Division Sylhet Division"
        if labels != expected:
            raise ValueError("DGHS full regional category order differs from reviewed chart")
        counts = [int(v.replace(",", "")) for v in value_lines]
        if sum(counts) != observations[0][4]:
            raise ValueError("DGHS dashboard regional sum differs from national admissions")
        regions = [
            ("Barishal", "country:BD:subdivision:BD-A", "subdivision"),
            ("Chattogram", "country:BD:subdivision:BD-B", "subdivision"),
            ("Dhaka Division (Out of CC)", "country:BD:source_region:dhaka_out_of_city_corporations", "source_reporting_region"),
            ("Dhaka North City Corporation", "country:BD:source_region:dncc", "source_reporting_region"),
            ("Dhaka South City Corporation", "country:BD:source_region:dscc", "source_reporting_region"),
            ("Khulna", "country:BD:subdivision:BD-D", "subdivision"),
            ("Mymensingh", "country:BD:subdivision:BD-H", "subdivision"),
            ("Rajshahi", "country:BD:subdivision:BD-E", "subdivision"),
            ("Rangpur", "country:BD:subdivision:BD-F", "subdivision"),
            ("Sylhet", "country:BD:subdivision:BD-G", "subdivision"),
        ]
        observations.extend((*region, "daily_admissions", count) for region, count in zip(regions, counts))
        regional_status = "ten_explicit_categories_verified_against_national"
    elif len(value_lines) > 10:
        raise ValueError("DGHS regional category count exceeds reviewed chart")
    if repaired_national_label:
        age_totals = re.findall(r"Grand Total " + count_pattern + r" " + count_pattern + r" " + count_pattern + r"\nAge group distribution of affected cases", text)
        if len(value_lines) != 10 or len(age_totals) != 1:
            raise ValueError("DGHS duplicate comma label lacks independent count corroboration")
        male, female, total = [int(v.replace(",", "")) for v in age_totals[0]]
        if male + female != total or total != observations[0][4]:
            raise ValueError("DGHS repaired national count disagrees with age/sex grand total")
    rows = []
    for name, geography, geography_type, kind, count in observations:
        rows.append({
            "Date": when.isoformat(), "Year": str(when.year), "Month": str(when.month),
            "PeriodStart": when.isoformat(), "PeriodEnd": when.isoformat(), "PeriodType": "daily", "Frequency": "daily",
            "DateRole": "reporting_day_label", "TimeBasis": "source_stated_last_24_hours",
            "DiseaseCode": "__source_native__", "SourceDiseaseCode": f"dengue__{kind}",
            "RawDiseaseLabel": f"Dengue {kind.replace('_', ' ')}", "DefinitionVersion": "bd_dghs_dengue_dashboard_explicit_daily_v1",
            "Cases": str(count), "Measure": "hospital_admissions" if kind == "daily_admissions" else "deaths", "Unit": "count",
            "ReportingBasis": "hospital_reported_last_24_hours", "Geography": name, "GeographyKey": geography,
            "GeographyType": geography_type, "SourceGeographyLabel": name,
            "Source": SOURCE_NAME, "SourceURL": LANDING_URL, "SourcePublishedAt": when.isoformat(), "SourcePage": "1",
            "SourceNationalAdmissionLabel": cases[0],
            "ExtractionRepair": "duplicate_comma_confirmed_by_regional_and_age_sex_totals" if repaired_national_label else "none",
            "MissingValueStatus": "reported", "DatasetStatus": "provisional_revisable", "IsProvisional": "true",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true", "AggregationPolicy": "non_additive", "PublicReleaseEnabled": "false",
            "Dimensions": json.dumps({"disease": "dengue", "source_metric": kind, "population_scope": "reporting_hospitals", "source_table": "dashboard_explicit_count_labels"}, sort_keys=True),
            **(provenance or {}),
        })
    return rows, {"date": when.isoformat(), "format": "english_dashboard", "national_daily_admissions": observations[0][4], "national_daily_deaths": observations[1][4], "regional_status": regional_status, "rows": len(rows), "incidence_inferred": False, "national_label_repaired": repaired_national_label}


class BangladeshDGHSDengueCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=45, delay=0.15, max_retries=2)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged DGHS capture and offline organization")

    def parse(self, response):
        raise NotImplementedError("Use captured DGHS PDF with its directory date")
