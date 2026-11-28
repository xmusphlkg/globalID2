"""Public Health Institute of Malawi IDSR bulletin discovery and parsing."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, timedelta
from html import unescape
from urllib.parse import urljoin, urlparse

from lxml import html

from .base import BaseCrawler

DEFAULT_SCOPE = "mw_phim_idsr_weekly"
DEFAULT_SOURCE_NAME = "Malawi PHIM IDSR Weekly Bulletin"
ONTOLOGY_SOURCE_ID = "SRC_MW_PHIM_IDSR"
HISTORY_START_YEAR = 2025
SEARCH_URL = "https://phim.health.gov.mw/wp-json/wp/v2/search?search=IDSR&per_page=100&page=1"
MEDIA_URL = "https://phim.health.gov.mw/wp-json/wp/v2/media?search=IDSR&per_page=100&page=1"


class MalawiIDSRContractError(ValueError):
    """The source does not establish a trustworthy observation contract."""


class MalawiPHIMIDSRCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(delay=0, timeout=45, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged Malawi collector")

    def parse(self, content, **kwargs):
        raise NotImplementedError("Use parse_report_pdf on captured originals")


def discover_posts(payload):
    try:
        items = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise MalawiIDSRContractError("Invalid PHIM search JSON") from exc
    if not isinstance(items, list):
        raise MalawiIDSRContractError("PHIM search response is not a list")
    posts = []
    for item in items:
        title = unescape(item.get("title", ""))
        if not re.search(r"\bIDSR\b.*\bbulletin\b|\bbulletin\b.*\bIDSR\b", title, re.I):
            continue
        if not re.search(r"\bweek\s*\d{1,2}\b", title, re.I):
            continue
        url = item.get("url", "")
        if urlparse(url).hostname != "phim.health.gov.mw":
            raise MalawiIDSRContractError("Unexpected PHIM post host")
        posts.append({"post_id": int(item["id"]), "title": title, "post_url": url})
    return list({post["post_id"]: post for post in posts}.values())


def discover_pdf(payload, post):
    tree = html.fromstring(payload)
    candidates = []
    for anchor in tree.xpath("//a[@href]"):
        url = urljoin(post["post_url"], anchor.get("href", ""))
        path = urlparse(url).path
        if (urlparse(url).hostname == "phim.health.gov.mw"
                and path.lower().endswith(".pdf")
                and "/wp-content/uploads/" in path
                and re.search(r"idsr|epi.?bulletin", path, re.I)):
            candidates.append(url)
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        raise MalawiIDSRContractError(f"Expected one linked IDSR PDF, found {len(candidates)}")
    return {**post, "pdf_url": candidates[0]}


def discover_media(payload, *, start_year=HISTORY_START_YEAR, end_year=None):
    try:
        items = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise MalawiIDSRContractError("Invalid PHIM media JSON") from exc
    if not isinstance(items, list):
        raise MalawiIDSRContractError("PHIM media response is not a list")
    reports = []
    for item in items:
        url = item.get("source_url", "")
        path = urlparse(url).path
        title = unescape(item.get("title", {}).get("rendered", ""))
        uploaded = item.get("date", "")
        if (urlparse(url).hostname != "phim.health.gov.mw"
                or not path.lower().endswith(".pdf")
                or "/wp-content/uploads/" not in path
                or not re.search(r"idsr.*bulletin|bulletin.*idsr", path + " " + title, re.I)
                or re.search(r"lilongwe|district", path + " " + title, re.I)):
            continue
        if not re.match(r"20\d{2}-\d{2}-\d{2}", uploaded):
            raise MalawiIDSRContractError("Official media PDF has no publication date")
        year = int(uploaded[:4])
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        reports.append({"title": title, "pdf_url": url,
                        "post_url": f"https://phim.health.gov.mw/wp-json/wp/v2/media/{int(item['id'])}",
                        "media_id": int(item["id"]), "uploaded_at": uploaded,
                        "discovery": "official_media_api"})
    return list({report["pdf_url"]: report for report in reports}.values())


def extract_pdf_pages(payload):
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise MalawiIDSRContractError("Incomplete or invalid IDSR PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    if not pages or not pages[0].strip():
        raise MalawiIDSRContractError("IDSR PDF has no readable cover")
    return pages


def _slug(label):
    if re.fullmatch(r"diarrh(?:oea|ea|eal) with blood", label.casefold()):
        return "diarrhoea_with_blood"
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def _report_period(cover):
    match = re.search(r"Epidemiological\s+Week\s+(\d{1,2})\s*\([^)]*?\b(20\d{2})\s*\)", cover, re.I)
    if not match:
        match = re.search(r"Epidemiological\s+Week\s+(\d{1,2}).{0,90}?\b(20\d{2})\b", cover, re.I | re.S)
    if not match:
        raise MalawiIDSRContractError("IDSR cover has no explicit epidemiological year/week")
    week, year = int(match[1]), int(match[2])
    try:
        period_end = date.fromisocalendar(year, week, 7)
    except ValueError as exc:
        raise MalawiIDSRContractError("Invalid IDSR epidemiological week") from exc
    return year, week, period_end


def _table_rows(pages):
    for number, page in enumerate(pages, 1):
        marker = re.search(r"Table\s*1\s*[.:]\s*Reported alerts of diseases\s*(?:and|/)\s*conditions", page, re.I)
        if not marker:
            continue
        text = page[marker.end():]
        header = text.split("EPIDEMIC PRONE DISEASES", 1)[0]
        has_deaths = bool(re.search(r"Suspected\s+(?:cases\s+)?Deaths", header, re.I))
        if not has_deaths and not re.search(r"(?m)^Suspected cases\s*$", header, re.I):
            raise MalawiIDSRContractError("IDSR Table 1 lost its suspected-case header")
        rows = []
        unparsed = []
        section = ""
        lines = [line.strip() for line in text.splitlines()]
        for line in lines:
            section_heading = line in {"EPIDEMIC PRONE DISEASES", "ERADICATION/ELIMINATION", "IMPORTANCE"}
            if not has_deaths:
                section_heading |= line in {"DISEASES TARGETED FOR ERADICATION/ELIMINATION",
                                            "CONDITIONS OF PUBLIC HEALTH IMPORTANCE"}
            if section_heading:
                section = line.lower()
                continue
            if re.match(r"^\d+\.\s+Ongoing outbreaks", line, re.I):
                break
            if not line or line.isdigit() or line.startswith(("DISEASES TARGETED", "CONDITIONS OF PUBLIC")):
                continue
            pattern = (r"(?P<label>[A-Za-z][A-Za-z\- /()]+?)\s+(?P<cases>\d[\d,]*|[-—])\s+(?P<deaths>\d[\d,]*|[-—])"
                       if has_deaths else r"(?P<label>[A-Za-z][A-Za-z\- /()]+?)\s+(?P<cases>\d[\d,]*|[-—])")
            match = re.fullmatch(pattern, line) if section else None
            if not match:
                if re.fullmatch(r"[A-Za-z][A-Za-z\- /()]+\s+\d[\d,]*", line):
                    unparsed.append(line)
                continue
            label = " ".join(match["label"].split())
            if label.casefold() in {"suspected cases deaths"}:
                continue
            rows.append({"label": label, "cases": match["cases"],
                         "deaths": match["deaths"] if has_deaths else None, "section": section})
        if not rows:
            raise MalawiIDSRContractError("IDSR Table 1 has no verifiable disease rows")
        return number, rows, unparsed
    raise MalawiIDSRContractError("IDSR PDF has no supported national disease table")


def _split_table_cell(words, label, table_top):
    first = label.split()[0].casefold()
    starts = [word for word in words if word["text"].casefold() == first
              and word["top"] > table_top and 85 <= word["x0"] <= 180]
    if len(starts) != 1:
        return None
    top = starts[0]["top"]
    case_tokens = [word["text"] for word in words if 340 <= word["x0"] <= 430
                   and top - 2 <= word["top"] <= top + 15]
    death_tokens = [word["text"] for word in words if 470 <= word["x0"] <= 530
                    and top - 2 <= word["top"] <= top + 15]
    if len(case_tokens) != 1 or len(death_tokens) != 1:
        return None
    if not all(re.fullmatch(r"\d[\d,]*|[-—]", token) for token in case_tokens + death_tokens):
        return None
    return case_tokens[0], death_tokens[0]


def _recover_split_rows(payload, page_number, unparsed, existing):
    if not unparsed:
        return [], unparsed
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        page = pdf.pages[page_number - 1]
        headings = page.search(r"Table\s*1\s*[.:]\s*Reported alerts")
        if len(headings) != 1:
            return [], unparsed
        words, table_top = page.extract_words(), headings[0]["top"]
    known = {_slug(item["label"]) for item in existing}
    recovered, remaining = [], []
    for line in unparsed:
        match = re.fullmatch(r"(?P<label>[A-Za-z][A-Za-z\- /()]+?)\s+\d[\d,]*", line)
        if not match:
            remaining.append(line)
            continue
        label = match["label"].strip()
        if _slug(label) in known:
            remaining.append(line)
            continue
        cells = _split_table_cell(words, label, table_top)
        if cells is None:
            remaining.append(line)
            continue
        recovered.append({"label": label, "cases": cells[0], "deaths": cells[1],
                          "section": "epidemic prone diseases"})
    return recovered, remaining


def parse_report_pdf(payload, report):
    pages = extract_pdf_pages(payload)
    if not re.search(r"REPUBLIC OF MALAWI|Ministry of Health", pages[0], re.I):
        raise MalawiIDSRContractError("PDF cover does not establish national Malawi scope")
    year, week, period_end = _report_period(pages[0])
    page_number, table, unparsed = _table_rows(pages)
    recovered, unparsed = _recover_split_rows(payload, page_number, unparsed, table)
    table.extend(recovered)
    digest = hashlib.sha256(payload).hexdigest()
    rows = []
    for item in table:
        code = _slug(item["label"])
        measures = (("cases", item["cases"]),)
        if item["deaths"] is not None:
            measures += (("deaths", item["deaths"]),)
        for measure, token in measures:
            value = None if token in {"-", "—"} else int(token.replace(",", ""))
            # A maternal-death count is a death measure, never a patient-case
            # count. Keep source-native non-infectious categories for review.
            rows.append({
                "Date": period_end.isoformat(), "Year": str(period_end.year), "Month": str(period_end.month),
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": item["label"],
                "SourceDiseaseCode": f"idsr:{code}:suspected:weekly:{measure}",
                "Cases": "" if value is None else str(value),
                "MissingValueStatus": "source_missing" if value is None else "",
                "Measure": measure, "Unit": "count", "Frequency": "weekly", "PeriodType": "weekly",
                "ReportingBasis": "notification", "DefinitionVersion": "mw_phim_idsr_table1_suspected_v1",
                "Geography": "Malawi", "GeographyKey": "country:MW:national",
                "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["pdf_url"],
                "SourceListingURL": report["post_url"], "RawSHA256": digest,
                "SourceReportYear": str(year), "SourceReportWeek": str(week),
                "SourcePeriodYear": str(year), "SourcePeriodWeek": str(week),
                "SourcePeriodStart": (period_end - timedelta(days=6)).isoformat(),
                "SourcePeriodEnd": period_end.isoformat(), "SourcePage": str(page_number),
                "DatasetStatus": "provisional", "IsProvisional": "true",
                "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
                "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
                "Dimensions": json.dumps({"source_category": item["label"], "case_classification": "suspected", "table": "Table 1", "section": item["section"]}, sort_keys=True),
            })
    detail = {"year": year, "week": week, "table_page": page_number,
              "table_categories": len(table), "national_rows": len(rows),
              "geometry_recovered_categories": [item["label"] for item in recovered],
              "unparsed_table_lines": unparsed,
              "unparsed_sections": ["Annex district tables", "outbreak cumulative narratives", "figure-only values"]}
    return rows, detail
