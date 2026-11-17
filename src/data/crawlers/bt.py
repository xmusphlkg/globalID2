"""Official Bhutan RCDC publications and conservative sentinel data extraction.

The publication cadence and observation cadence are deliberately independent:
only influenza fractions explicitly assigned to one week become observations.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import io
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from lxml import html

from .base import BaseCrawler

DEFAULT_SCOPE = "bt_rcdc_publications"
DEFAULT_SOURCE_NAME = "Bhutan RCDC Publications and Sentinel Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_BT_RCDC_PUBLICATIONS"
HISTORY_START_YEAR = 2015
LANDING_URL = "https://www.rcdc.gov.bt/web/"
PUBLICATIONS_URL = "https://www.rcdc.gov.bt/web/?page_id=3561"


class BhutanRCDCContractError(ValueError):
    """The captured source cannot establish the claimed observation."""


class BhutanRCDCCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=45, delay=0.1, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use staged RCDC collect/organize")

    def parse(self, response, **kwargs):
        raise NotImplementedError("Use parse_report_pdf on captured originals")


def _official(url):
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"rcdc.gov.bt", "www.rcdc.gov.bt"}


def _kind(title):
    if re.search(r"(?:surveillance report|fluview).*(?:week|20\d\d)|(?:week|20\d\d).*surveillance report", title, re.I):
        return "respiratory_surveillance"
    if re.search(r"monthly.*(?:epidemiology|epidemiological).*report", title, re.I):
        return "monthly_epidemiology"
    if re.search(r"quarterly.*bulletin|bulletin.*quarter", title, re.I):
        return "quarterly_bulletin"
    if re.search(r"annual scientific report", title, re.I):
        return "annual_scientific"
    if (not re.search(r"\b(?:training|workshop)\b", title, re.I)
            and re.search(r"outbreak|(?:suspected|confirmed).*(?:influenza|poisoning)|hand.?foot.?mouth", title, re.I)):
        return "event_report"
    return None


def discover_publications(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    tree = html.fromstring(payload)
    reports = {}
    for article in tree.xpath("//article[@data-date]"):
        links = article.xpath(".//*[contains(@class,'entry-title')]/a[@href]")
        if not links:
            continue
        title = " ".join(links[0].text_content().split())
        kind = _kind(title)
        if not kind:
            continue
        published = article.get("data-date", "")
        try:
            published_date = date.fromisoformat(published[:10])
        except ValueError as exc:
            raise BhutanRCDCContractError("RCDC publication missing source date") from exc
        if not int(start_year) <= published_date.year <= int(end_year or 9999):
            continue
        url = urljoin(LANDING_URL, links[0].get("href"))
        post_id = parse_qs(urlparse(url).query).get("p", [])
        if not _official(url) or len(post_id) != 1 or not post_id[0].isdigit():
            raise BhutanRCDCContractError("Unexpected RCDC publication URL")
        reports[url] = {"title": title, "publication_url": url, "published_at": published,
                        "post_id": int(post_id[0]), "publication_kind": kind}
    if not reports:
        raise BhutanRCDCContractError("No dated RCDC publications in requested window")
    return sorted(reports.values(), key=lambda r: (r["published_at"], r["post_id"]), reverse=True)


def discover_pdfs(payload, report):
    tree = html.fromstring(payload)
    # Restrict to the post content; related-post/sidebar attachments are not
    # source documents for the selected publication.
    containers = tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' elementor-widget-theme-post-content ')]")
    if not containers:
        containers = tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' entry-content ')]")
    result = []
    for container in containers:
        for anchor in container.xpath(".//a[@href]"):
            url = urljoin(report["publication_url"], anchor.get("href"))
            if urlparse(url).path.lower().endswith(".pdf"):
                if not _official(url) or "/wp-content/uploads/" not in urlparse(url).path:
                    raise BhutanRCDCContractError("RCDC attachment is outside official uploads")
                result.append(url)
    return list(dict.fromkeys(result))


def discover_events(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    """Retain the separate latest-event table without inventing time series.

    A row's count describes a reported event, not incidence for its date. The
    public table can contain residential locations, so only Dzongkhag is
    carried into structured audit metadata.
    """
    tree = html.fromstring(payload)
    tables = tree.xpath("//table[.//th[contains(normalize-space(.),'Event Reports(Latest)')]]")
    if not tables:
        return []
    if len(tables) != 1:
        raise BhutanRCDCContractError("Ambiguous RCDC latest-event tables")
    headers = [" ".join(th.text_content().split()) for th in tables[0].xpath("./thead/tr[last()]/th")]
    if headers != ["Dzongkhag", "Date", "Name", "Location", "Case", "Status"]:
        raise BhutanRCDCContractError("RCDC latest-event table columns changed")
    events = []
    for row in tables[0].xpath("./tbody/tr"):
        cells = [" ".join(td.text_content().split()) for td in row.xpath("./td")]
        if len(cells) != 6:
            raise BhutanRCDCContractError("RCDC event row is incomplete")
        district, reported, label, _location, cases, status = cells
        try:
            reported_date = date.fromisoformat(reported)
        except ValueError as exc:
            raise BhutanRCDCContractError("RCDC event has invalid reported date") from exc
        if not int(start_year) <= reported_date.year <= int(end_year or 9999):
            continue
        if not district or not label or not re.fullmatch(r"\d+", cases):
            raise BhutanRCDCContractError("RCDC event label/count is ambiguous")
        identity = json.dumps([district, reported, label], ensure_ascii=False)
        events.append({"event_key": hashlib.sha256(identity.encode()).hexdigest(),
                       "dzongkhag": district, "reported_date": reported, "raw_event_label": label,
                       "reported_event_cases": int(cases), "status": status,
                       "source_url": LANDING_URL,
                       "count_semantics": "event_snapshot_not_daily_or_weekly_incidence"})
    return events


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise BhutanRCDCContractError("Incomplete or invalid RCDC PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as document:
        return [page.extract_text() or "" for page in document.pages]


def _explicit_narrative_week_counts(cover, after_title):
    """Read one influenza sentence, isolating it from other pathogens/ILI.

    Older covers put the week on either side of a percentage and count.
    Counts must be parenthesized after an explicit positivity percentage;
    tested denominators are never derived from rounded percentages.
    """
    content = cover[after_title:]
    start = re.search(r"\b(?:Flu activity|Influenza activity|In week\s+\d{1,2},)", content, re.I)
    if not start:
        raise BhutanRCDCContractError("RCDC influenza summary absent")
    narrative = content[start.start():]
    narrative = re.split(r"\.(?=\s+(?:[A-Z]|[•●]))", narrative, maxsplit=1)[0]
    narrative = re.split(r"\b(?:COVID-19|SARS-CoV-2|RSV|A total of|Influenza-like Illness|Severe Acute)", narrative, maxsplit=1, flags=re.I)[0]
    if not re.search(r"\b(?:flu|influenza)\b", narrative, re.I):
        raise BhutanRCDCContractError("RCDC weekly count lacks influenza attribution")
    value = r"(?P<percent>\d+(?:\.\d+)?)\s*%\s*\(\s*(?P<count>\d[\d,]*)\s*(?:cases?)?\s*\)"
    values = list(re.finditer(value, narrative, re.I))
    assigned, claimed = {}, set()

    def assign(week, match):
        if week in assigned or match.start() in claimed:
            raise BhutanRCDCContractError("Conflicting weekly influenza narrative values")
        assigned[week] = (match["percent"], match["count"], None, str(week))
        claimed.add(match.start())

    # The plural form identifies “weeks 33 and 34”; singular “week 27 and
    # 9.0%” must never be mistaken for a week pair.
    paired = re.search(r"\bweeks\s+(\d{1,2})\s+and\s+(\d{1,2})\b", narrative, re.I)
    if paired and len(values) == 2:
        # “positivity rates ... X% (n) and Y% (n), respectively” is an
        # explicit ordered assignment to the printed pair.
        assign(int(paired[1]), values[0])
        assign(int(paired[2]), values[1])

    # Assign trailing week labels first; a prefix matcher must not steal a
    # subsequent week's explicitly labelled observation.
    for match in values:
        suffix = re.match(r"\s*(?:(?:positivity|positive)(?:\s+rate)?\s*)?in\s+week\s+(\d{1,2})\b", narrative[match.end():], re.I)
        if suffix:
            assign(int(suffix[1]), match)
    week_tokens = list(re.finditer(r"\bweek\s+(\d{1,2})\b", narrative, re.I))
    for index, token in enumerate(week_tokens):
        week = int(token[1])
        if week in assigned:
            continue
        limit = week_tokens[index + 1].start() if index + 1 < len(week_tokens) else len(narrative)
        candidates = [m for m in values if token.end() <= m.start() < limit and m.start() not in claimed]
        if len(candidates) == 1:
            assign(week, candidates[0])
        elif len(candidates) > 1:
            raise BhutanRCDCContractError("Multiple influenza counts lack explicit weekly assignment")
        else:
            clause = narrative[token.end():limit]
            if re.search(r"\b(?:there were\s+)?no influenza (?:cases(?:\s+detected)?|positives)\b", clause, re.I):
                # Explicit zero detections do not prove a tested denominator
                # or a positivity percentage, so both stay unknown.
                assigned[week] = (None, "0", None, str(week))
    if len(claimed) != len(values):
        raise BhutanRCDCContractError("Influenza narrative has unassigned count/percentage")
    return list(assigned.values())


def parse_respiratory_text(pages, report, *, raw_sha256):
    if not pages:
        raise BhutanRCDCContractError("Empty RCDC document")
    cover = " ".join(pages[0].split())
    if not re.search(r"Biweekly (?:National Respiratory Virus Sentinel|Respiratory Integrated Flu) Surveillance Report", cover, re.I):
        raise BhutanRCDCContractError("Unsupported RCDC respiratory cover/layout")
    period = re.search(r"(?:Epidemiological Week|for week)\s+(\d{1,2})\s*&\s*(\d{1,2})\s*\(([^)]+)\)(?:\s*,?\s*(20\d{2}))?", cover, re.I)
    if not period:
        raise BhutanRCDCContractError("Missing explicit RCDC week pair and date range")
    first, last = int(period[1]), int(period[2])
    # Month elision (October 5-18) and an explicit first year are both used
    # in the official covers. Never infer a date from the upload filename.
    source_dates = period[3].strip()
    if period[4]:
        source_dates += ", " + period[4]
    dates = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})(?:,?\s+(20\d{2}))?\s*[-–—]\s*(?:([A-Za-z]+)\s+)?(\d{1,2}),?\s+(20\d{2})", source_dates)
    if not dates or last != first + 1:
        raise BhutanRCDCContractError("Unsupported or ambiguous RCDC biweekly dates")
    from datetime import datetime
    year = int(dates[6])
    def month_number(label):
        if label.casefold() == "sept":
            label = "Sep"
        for pattern in ("%B", "%b"):
            try:
                return datetime.strptime(label, pattern).month
            except ValueError:
                pass
        raise ValueError("unknown source month")
    try:
        start = date(int(dates[3] or year), month_number(dates[1]), int(dates[2]))
        end = date(year, month_number(dates[4] or dates[1]), int(dates[5]))
        if start > end and not dates[3]:
            start = start.replace(year=year - 1)
        if end - start != timedelta(days=13):
            raise ValueError("week dates conflict")
    except ValueError as exc:
        raise BhutanRCDCContractError("RCDC date range does not establish supported weekly intervals") from exc
    summary = re.search(r"Influenza Activity:\s*(.*?)\s*Other Respiratory Pathogens:", cover, re.I)
    fractions = re.findall(r"(\d+(?:\.\d+)?)%\s*\((\d[\d,]*)\s*/\s*(\d[\d,]*)\)\s*in\s*week\s*(\d{1,2})", summary[1], re.I) if summary else []
    if not summary:
        fractions = _explicit_narrative_week_counts(cover, period.end())
    if len(fractions) != 2 or {int(v[3]) for v in fractions} != {first, last}:
        raise BhutanRCDCContractError("Summary does not provide both explicit weekly influenza fractions")
    fractions.sort(key=lambda value: int(value[3]))
    rows = []
    for percent, positive, tested, week in fractions:
        value, denominator, week = int(positive.replace(",", "")), int(tested.replace(",", "")) if tested is not None else None, int(week)
        if (percent is not None and not 0 <= float(percent) <= 100) or (denominator is not None and (denominator <= 0 or value > denominator or abs(100 * value / denominator - float(percent)) > 0.15)):
            raise BhutanRCDCContractError("Influenza numerator/denominator/percentage conflict")
        # RCDC has used both Monday–Sunday and Sunday–Saturday source weeks.
        # The printed interval is authoritative; ISO conversion would shift
        # older Sunday-start reports by one day.
        offset = (week - first) * 7
        period_start = start + timedelta(days=offset)
        period_end = period_start + timedelta(days=6)
        if period_end > end or (week == last and period_end != end):
            raise BhutanRCDCContractError("RCDC explicit week interval is inconsistent")
        rows.append({
            "Date": period_end.isoformat(), "Year": str(period_end.year), "Month": str(period_end.month),
            "DiseaseCode": "__source_native__", "RawDiseaseLabel": "Influenza",
            "SourceDiseaseCode": "influenza:sentinel:weekly:laboratory_detections",
            "Cases": str(value), "Measure": "laboratory_detections", "Unit": "count",
            "Frequency": "weekly", "PeriodType": "weekly",
            "ReportingBasis": "sentinel_laboratory_surveillance",
            "DefinitionVersion": "bt_rcdc_explicit_weekly_influenza_summary_v1",
            "Geography": "Bhutan sentinel surveillance network", "GeographyKey": "country:BT:national",
            "PopulationScope": "reporting_sentinel_network_not_population_cases",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"],
            "SourceListingURL": report["publication_url"], "SourcePublishedAt": report["published_at"],
            "RawSHA256": raw_sha256, "SourcePage": "1", "SourceReportYear": str(year),
            "SourcePeriodYear": str(year), "SourcePeriodWeek": str(week),
            "SourcePeriodStart": period_start.isoformat(),
            "SourcePeriodEnd": period_end.isoformat(),
            "SourceSamplesTested": str(denominator) if denominator is not None else "", "SourcePositivityPercent": percent if percent is not None else "",
            "DatasetStatus": "provisional", "IsProvisional": "true", "AggregationPolicy": "non_additive",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
            "Dimensions": json.dumps({"surveillance_population": "sentinel_network", "pathogen": "influenza"}, sort_keys=True),
        })
    return rows, {"year": year, "weeks": [first, last], "observations": len(rows),
                  "publication_frequency": "biweekly", "observation_frequency": "weekly",
                  "excluded": ["biweekly RSV and SARS-CoV-2 totals", "image-only tables/curves", "unreported weeks"]}


def parse_report_pdf(payload, report):
    return parse_respiratory_text(extract_pdf_pages(payload), report,
                                  raw_sha256=hashlib.sha256(payload).hexdigest())
