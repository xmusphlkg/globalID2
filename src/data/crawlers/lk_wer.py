"""Bounded discovery of Sri Lanka's public WER originals.

Numerical extraction intentionally requires a verified report layout. The live
official site returned 403/509 during onboarding; no case table is guessed.
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime
import re
from urllib.parse import urldefrag, urljoin, urlparse

from lxml import html

from .base import BaseCrawler

DEFAULT_SCOPE = "lk_epid_wer_weekly"
DEFAULT_SOURCE_NAME = "Sri Lanka Epidemiology Unit Weekly Epidemiological Report"
ONTOLOGY_SOURCE_ID = "SRC_LK_EPID_WER"
INDEX_URL = "https://www.epid.gov.lk/epid/public/weekly-epidemiological-report"
HISTORY_START_YEAR = 2025
OFFICIAL_HOSTS = {"epid.gov.lk", "www.epid.gov.lk"}
WAYBACK_HOSTS = {"web.archive.org"}


class SriLankaWERContractError(ValueError):
    """An original does not establish a validated numerical contract."""


class SriLankaWERCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(delay=0.5, timeout=35, max_retries=1, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged Sri Lanka WER collector")

    def parse(self, content, **kwargs):
        raise NotImplementedError("Use captured originals for offline organization")


def official_url(url):
    parsed = urlparse(url)
    return (parsed.scheme in {"http", "https"} and parsed.hostname in OFFICIAL_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.port in {None, 80, 443})


def wayback_url(url):
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname in WAYBACK_HOSTS
            and parsed.username is None and parsed.password is None)


def validate_index(payload):
    text = payload.decode("utf-8", errors="replace")
    if re.search(r"Bandwidth Limit Exceeded|<title>\s*(?:403|500|509)\b|<h1>\s*Forbidden", text, re.I):
        raise SriLankaWERContractError("Official WER source unavailable (403/509 error document)")
    try:
        tree = html.fromstring(payload)
    except (ValueError, html.etree.ParserError) as exc:
        raise SriLankaWERContractError("Invalid WER archive HTML") from exc
    if not re.search(r"weekly\s+epidemiological\s+report|\bWER\b", " ".join(tree.itertext()), re.I):
        raise SriLankaWERContractError("Official response is not a WER archive")
    return tree


def discover_reports(payload, *, source_url=INDEX_URL, start_year=HISTORY_START_YEAR, end_year=None):
    """Follow linked official English PDFs only; never construct PDF filenames."""
    if not official_url(source_url):
        raise SriLankaWERContractError("Unexpected WER archive host")
    tree = validate_index(payload)
    reports = {}
    for anchor in tree.xpath("//a[@href]"):
        url = urldefrag(urljoin(source_url, anchor.get("href")))[0]
        if not official_url(url) or not urlparse(url).path.lower().endswith(".pdf"):
            continue
        label = " ".join(anchor.itertext()).strip()
        context = " ".join(anchor.getparent().itertext()) if anchor.getparent() is not None else label
        if len(context) > 1000:
            context = label
        identity = f"{url} {label} {context}"
        if re.search(r"sinhala|tamil|(?:^|[/_-])(?:sin|tam)(?:[/_.-]|$)", identity, re.I):
            continue
        if not re.search(r"\bWER\b|weekly[\s_-]+epidemiological|vol[_\s-]*\d+[_\s-]+no", identity, re.I):
            continue
        years = set(map(int, re.findall(r"(?<!\d)(20\d{2})(?!\d)", identity)))
        # Conflicting or absent years stay unclassified rather than assigned
        # the archive retrieval year.
        if len(years) != 1:
            continue
        year = years.pop()
        if year < int(start_year) or (end_year is not None and year > int(end_year)):
            continue
        reports[url] = {"pdf_url": url, "listing_url": source_url, "title": label,
                        "listing_year": year}
    return sorted(reports.values(), key=lambda item: item["pdf_url"])


def discover_archive_links(payload, *, source_url=INDEX_URL):
    tree = validate_index(payload)
    links = set()
    for anchor in tree.xpath("//a[@href]"):
        url = urldefrag(urljoin(source_url, anchor.get("href")))[0]
        if (official_url(url) and "weekly-epidemiological-report" in urlparse(url).path
                and not urlparse(url).path.lower().endswith(".pdf") and url != source_url):
            links.add(url)
    return sorted(links)


WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_REPLAY = "https://web.archive.org/web/{timestamp}id_/{original}"


def discover_wayback_reports(payload, *, start_year=2007, end_year=2024,
                             original_prefix="www.epid.gov.lk/web/images/pdf/wer/"):
    """Return CDX records for archived official English WER PDFs.

    CDX is a public archival index; each row retains the original official URL,
    capture timestamp and archival digest. We never construct filenames or
    treat a replay response as current official publication.
    """
    try:
        records = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise SriLankaWERContractError("Invalid Wayback CDX response") from exc
    if not isinstance(records, list) or not records or records[0] != ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]:
        raise SriLankaWERContractError("Unexpected Wayback CDX schema")
    result = []
    for values in records[1:]:
        if len(values) != 7:
            raise SriLankaWERContractError("Malformed Wayback CDX row")
        _, timestamp, original, mimetype, status, digest, length = values
        path = urlparse(original).path
        years = set(map(int, re.findall(r"(?<!\d)(20\d{2})(?!\d)", path)))
        if (urlparse(original).hostname not in OFFICIAL_HOSTS
                or not path.startswith("/web/images/pdf/wer/")
                or not re.search(r"(?:_|-)english(?:_\d+)?\.pdf$", path, re.I)
                or mimetype != "application/pdf" or status != "200"
                or len(years) != 1 or not (int(start_year) <= next(iter(years)) <= int(end_year))):
            continue
        year = next(iter(years))
        result.append({"pdf_url": WAYBACK_REPLAY.format(timestamp=timestamp, original=original),
                       "original_url": original, "archive_timestamp": timestamp,
                       "archive_digest": digest, "archive_length": int(length), "listing_year": year,
                       "listing_url": WAYBACK_CDX_URL})
    return sorted({(item["original_url"], item["archive_timestamp"]): item for item in result}.values(),
                  key=lambda item: (item["listing_year"], item["original_url"]))


def inspect_report_pdf(payload, *, source_url):
    """Inspect captured originals without converting any numbers to case facts."""
    if not official_url(source_url):
        raise SriLankaWERContractError("Unexpected WER PDF host")
    if not payload.startswith(b"%PDF") or b"%%EOF" not in payload[-65536:]:
        raise SriLankaWERContractError("Invalid or incomplete WER PDF")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
    cover = texts[0] if texts else ""
    if not re.search(r"WER Sri Lanka|Weekly\s+Report|Vol\.\s*\d+\s+No\.\s*\d+", cover, re.I):
        raise SriLankaWERContractError("PDF cover does not establish WER identity")
    full = "\n".join(texts)
    table5 = bool(re.search(r"Table\s+5:\s+Selected\s+notifiable\s+diseases", full, re.I))
    return {"pages": len(texts), "numerical_contract": "legacy_table5_v1" if table5 else "unverified",
            "status": "validated_legacy_table5" if table5 else "original_captured_only", "source_url": source_url,
            "table5": table5,
            "note": "Table 5 A/B columns are weekly and cumulative WRCD notifications; no confirmed-case measure is inferred"}


_DISTRICTS = ["Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya", "Galle", "Hambantota", "Matara", "Jaffna", "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu", "Batticaloa", "Ampara", "Trincomalee", "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa", "Badulla", "Monaragala", "Ratnapura", "Kegalle", "Kalmunai", "SRI LANKA"]
_TABLE5_DISEASES = ["dengue_fever_dhf", "dysentery", "encephalitis", "enteric_fever", "food_poisoning", "leptospirosis", "typhus_fever", "viral_hepatitis"]


def _period_from_text(text):
    match = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})\s*\(\s*(\d{1,2})(?:st|nd|rd|th)?\s+Week", text, re.I)
    if not match:
        raise SriLankaWERContractError("WER Table 5 has no source-defined week range")
    try:
        month_start = match[2] or match[4]
        month_format = "%d %b %Y" if len(month_start) <= 3 else "%d %B %Y"
        period_start = datetime.strptime(f"{match[1]} {month_start} {match[5]}", month_format).date()
        period_end = datetime.strptime(f"{match[3]} {match[4]} {match[5]}", "%d %b %Y" if len(match[4]) <= 3 else "%d %B %Y").date()
    except ValueError as exc:
        raise SriLankaWERContractError("WER week range has invalid calendar date") from exc
    if (period_end - period_start).days not in {6, 7}:
        raise SriLankaWERContractError("WER report period is not a weekly range")
    return period_start, period_end, int(match[6])


def parse_report_pdf(payload, report):
    inspection = inspect_report_pdf(payload, source_url=report.get("original_url", report["pdf_url"]))
    if not inspection["table5"]:
        raise SriLankaWERContractError("WER numerical layout is unverified; Table 5 is absent")
    import pdfplumber
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        text = "\n".join(page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages)
    start, end, week = _period_from_text(text[text.find("Table 5:"):])
    section = text[text.find("Table 5:"):]
    section = section[:section.find("Source: Weekly") if "Source: Weekly" in section else len(section)]
    rows = []
    for district in _DISTRICTS:
        match = re.search(r"^" + re.escape(district) + r"\s+((?:\d{1,3}|-){17})\s*$", section, re.M)
        if not match:
            # Token spacing is expected in source PDFs; permit ordinary spaces.
            match = re.search(r"^" + re.escape(district) + r"\s+((?:(?:\d{1,3}|-)\s+){16}(?:\d{1,3}|-))\s*$", section, re.M)
        if not match:
            continue
        tokens = re.findall(r"\d{1,3}|-", match[1])
        if len(tokens) != 17:
            raise SriLankaWERContractError(f"WER Table 5 row has {len(tokens)} columns: {district}")
        geography = "country:LK:national" if district == "SRI LANKA" else "country:LK:district:" + re.sub(r"[^a-z0-9]+", "_", district.casefold()).strip("_")
        geography_name = "Sri Lanka" if district == "SRI LANKA" else district
        for idx, disease in enumerate(_TABLE5_DISEASES):
            for measure, token in (("cases", tokens[idx * 2]), ("cumulative_cases", tokens[idx * 2 + 1])):
                value = None if token == "-" else int(token)
                rows.append({"Date": end.isoformat(), "Year": str(end.year), "Month": str(end.month),
                    "DiseaseCode": "__source_native__", "RawDiseaseLabel": disease.replace("_", " ").title(),
                    "SourceDiseaseCode": f"wer:{disease}:notification:{measure}",
                    "Cases": "" if value is None else str(value), "MissingValueStatus": "source_missing" if value is None else "",
                    "Measure": measure, "Unit": "count", "Frequency": "weekly", "PeriodType": "weekly" if measure == "cases" else "cumulative",
                    "ReportingBasis": "notification", "DefinitionVersion": "lk_wer_table5_wrcd_v1",
                    "Geography": geography_name, "GeographyKey": geography, "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": report["pdf_url"], "OriginalSourceURL": report.get("original_url", report["pdf_url"]),
                    "SourcePeriodStart": start.isoformat(), "SourcePeriodEnd": end.isoformat(),
                    "SourceReportWeek": str(week), "SourcePage": "4", "DatasetStatus": "historical",
                    "IsProvisional": "false", "RevisionSemantics": "archived_snapshot", "AuthoritativeRevision": "false",
                    "PublicReleaseEnabled": "false", "License": "archive_replay_terms_unverified",
                    "Dimensions": json.dumps({"table": "Table 5", "source_column": "A" if measure == "cases" else "B",
                        "definition": "WRCD notifications; A=current week, B=year cumulative", "district": district}, sort_keys=True)})
    if not rows:
        raise SriLankaWERContractError("WER Table 5 contains no recognized district rows")
    return rows, {"period_start": start.isoformat(), "period_end": end.isoformat(), "week": week,
                  "table": "Table 5", "districts": sorted({r["Geography"] for r in rows}), "rows": len(rows),
                  "numerical_contract": "legacy_table5_v1", "notification_only": True}
