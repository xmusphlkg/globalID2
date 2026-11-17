"""Source-native Czech and Estonian single-month notification tables."""
from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import io
import re
import unicodedata
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import pdfplumber
from .base import BaseCrawler

CONTRACTS = {
    "CZ": {"scope": "cz_szu_monthly", "source_id": "SRC_CZ_SZU_MONTHLY", "url": "https://szu.gov.cz/publikace-szu/data/infekce-v-cr/", "name": "Czechia SZU Monthly Infectious Disease Notifications", "start_year": 2017},
    "EE": {"scope": "ee_terviseamet_monthly", "source_id": "SRC_EE_TERVISEAMET_MONTHLY", "url": "https://www.terviseamet.ee/nakkushaigused/nakkushaiguste-statistika", "name": "Estonia Terviseamet Monthly Infectious Disease Notifications", "start_year": 2000},
}
MONTHS = {
    "CZ": ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen", "září", "říjen", "listopad", "prosinec"],
    "EE": ["jaanuar", "veebruar", "märts", "aprill", "mai", "juuni", "juuli", "august", "september", "oktoober", "november", "detsember"],
}


def compact(value):
    return " ".join(str(value or "").split())


def fold(value):
    return "".join(x for x in unicodedata.normalize("NFKD", compact(value).casefold()) if not unicodedata.combining(x))


def count_cell(value):
    token = compact(value)
    if token in {"", "-", "–", "—", "nd1", "nd2", "ND", ".."}:
        return "", "source_missing"
    if token in {"*", "<5"}:
        return token, "source_suppressed"
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:[ \xa0]\d{3})+)", token):
        raise ValueError(f"Ambiguous national monthly count: {token!r}")
    return str(int(token.replace(" ", "").replace("\xa0", ""))), "reported"


def _official_url(base, href, code):
    url = urljoin(base, href)
    p = urlsplit(url)
    hosts = {"szu.gov.cz"} if code == "CZ" else {"www.terviseamet.ee", "terviseamet.ee"}
    if p.scheme != "https" or p.hostname not in hosts or p.username or p.password:
        raise ValueError("Monthly source link left reviewed official host")
    return url


def discover_indexes(payload, *, country_code, url, start_year, end_year=None):
    """Discover real year-directory links; no invented next-year URLs."""
    if country_code == "EE":
        return []
    soup = BeautifulSoup(payload, "lxml"); found = {}
    for a in soup.select("a[href]"):
        match = re.fullmatch(r"(20\d{2})", compact(a.get_text()))
        if match and int(match[1]) >= start_year and (end_year is None or int(match[1]) <= end_year):
            u = _official_url(url, a["href"], country_code)
            if "/infekce-v-cr/" in u:
                found[u] = int(match[1])
    if not found:
        raise ValueError("CZ source did not expose year directories in requested range")
    return [{"url": u, "edition_year": y} for u, y in sorted(found.items())]


def discover_reports(payload, *, country_code, url, start_year, end_year=None, edition_year=None):
    soup = BeautifulSoup(payload, "lxml"); found, excluded = {}, []
    for a in soup.select("a[href]"):
        href, label = a["href"], compact(a.get_text(" ", strip=True))
        if not re.search(r"\.pdf(?:\?|$)", href, re.I):
            if country_code == "EE" and re.search(r"\.xlsx?(?:\?|$)", href, re.I) and "haigestumine" in fold(label + " " + href):
                excluded.append({"url": urljoin(url, href), "reason": "historical_xls_not_supported"})
            continue
        token = fold(label + " " + href)
        if country_code == "CZ":
            if "tabulka" not in token and "vyskyt vybranych hlasenych infekci" not in token:
                continue
            if (any(x in token for x in ["kraje", "kraju", "podle kraj"])
                    or re.search(r"(?:_|/)kraj(?:e|u)?(?:[_.\-/]|$)", token)
                    or re.search(r"leden[-_](?!20\d{2}(?:\.|_|\s|$))", token)):
                excluded.append({"url": urljoin(url, href), "reason": "regional_or_cumulative"}); continue
            year = edition_year
        else:
            if not any(x in token for x in ["haigestumine", "nh_haigestumine"]):
                continue
            # National compiled PDFs retain their constituent month headings;
            # narrative EstEpiReport and disease-specific cumulative reports do not.
            if "estepireport" in token:
                continue
            candidates = re.findall(r"\b(20\d{2})\b", label)
            if not candidates:
                candidates = re.findall(r"(20\d{2})", href.split("/")[-1])
            if not candidates:
                excluded.append({"url": urljoin(url, href), "reason": "unverified_edition_year"}); continue
            year = int(candidates[-1])
        if year is None or year < start_year or (end_year is not None and year > end_year):
            continue
        u = _official_url(url, href, country_code)
        found[u] = {"url": u, "edition_year": year, "label": label, "country_code": country_code}
    return [found[u] for u in sorted(found)], excluded


def monthly_row(*, code, year, month, local_code, label, value, status, page, url, digest, retrieved_at):
    start = date(year, month, 1); end = date(year, month, monthrange(year, month)[1]); contract = CONTRACTS[code]
    category = "monthly_" + hashlib.sha256((local_code + "|" + label).encode()).hexdigest()[:24]
    return {"Date": start.isoformat(), "Year": str(year), "Month": str(month), "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(),
        "CountryCode": code, "SourceYear": str(year), "SourceMonth": str(month), "Calendar": "Gregorian", "TimeBasis": "source_single_calendar_month",
        "DiseaseCode": "__source_native__", "SourceDiseaseCode": category, "SourceICDCode": local_code, "RawDiseaseLabel": label,
        "Cases": value, "MissingValueStatus": status, "Measure": "case_notifications", "Unit": "count", "Frequency": "monthly", "PeriodType": "monthly",
        "ReportingBasis": "national_notified_cases", "DefinitionVersion": code.lower() + "_monthly_source_native_v1",
        "GeographyKey": f"country:{code}:national", "Geography": "Czechia" if code == "CZ" else "Estonia", "Dimensions": "{}",
        "Source": contract["name"], "SourceURL": url, "DownloadURL": url, "RawSHA256": digest, "SourcePage": page,
        "RetrievedAt": retrieved_at, "DatasetStatus": "reported_revisable", "IsProvisional": "true", "AggregationPolicy": "non_additive",
        "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true", "PublicReleaseEnabled": "true"}


def parse_pdf(payload, *, country_code, source_url, edition_year, retrieved_at=None, as_of=None):
    if country_code not in CONTRACTS or not payload.startswith(b"%PDF"):
        raise ValueError("Unsupported national monthly PDF")
    code = country_code; digest = hashlib.sha256(payload).hexdigest(); today = as_of or datetime.now(timezone.utc).date()
    months = {fold(m): i + 1 for i, m in enumerate(MONTHS[code])}
    rows, seen, errors = [], set(), []
    current_month = None; year = None
    with pdfplumber.open(io.BytesIO(payload)) as document:
        for page_number, page in enumerate(document.pages, 1):
            text = compact(page.extract_text())
            if code == "CZ":
                heading = re.search(r"(?:České republice|Czech Republic),\s*([^,]+?)\s+(20\d{2})(?:\s|$)", text)
            else:
                heading = re.search(r"\b(" + "|".join(MONTHS[code]) + r")\s+(20\d{2})\b", text, re.I)
            if heading:
                token = fold(heading[1])
                if token not in months:
                    raise ValueError("Cumulative or unrecognized monthly report heading")
                current_month, year = months[token], int(heading[2])
            if current_month is None or year is None:
                raise ValueError("National PDF lacks explicit single-month/year heading")
            if year != int(edition_year):
                raise ValueError("Monthly PDF header contradicts discovery edition year")
            if date(year, current_month, monthrange(year, current_month)[1]) >= today:
                raise ValueError("National monthly report is not a closed month")
            tables = page.extract_tables()
            if not tables:
                errors.append({"page": page_number, "reason": "no_supported_grid_table"}); continue
            for table in tables:
                header_at = None
                for i, cells in enumerate(table):
                    if code == "CZ" and len(cells) >= 3 and compact(cells[0]) == "Kód" and compact(cells[1]) == "Diagnóza":
                        header_at = i; break
                    if code == "EE" and len(cells) >= 6 and compact(cells[0]) == "Haiguse nimetus" and "RHK" in compact(cells[1]):
                        header_at = i; break
                if header_at is None:
                    errors.append({"page": page_number, "reason": "unrecognized_table_headers"}); continue
                header = table[header_at]
                if code == "CZ":
                    years = [compact(c) for c in header[2:]]
                    if str(year) not in years or len(set(years)) != len(years) or not all(re.fullmatch(r"20\d{2}", x) for x in years):
                        raise ValueError("CZ comparison-year columns changed")
                    column = 2 + years.index(str(year))
                else:
                    if Counter(re.sub(r"\s", "", header[5]).casefold()) != Counter("kuukokku"):
                        raise ValueError("EE single-month total header changed")
                    if table[header_at + 1][:6] != ["A", "B", "C", "1", "2", "3"]:
                        raise ValueError("EE column legend changed; monthly column identity unverified")
                    column = 5
                for cells in table[header_at + (2 if code == "EE" else 1):]:
                    if len(cells) != len(header):
                        errors.append({"page": page_number, "reason": "row_width_changed", "cells": cells}); continue
                    label, local_code = (compact(cells[1]), compact(cells[0])) if code == "CZ" else (compact(cells[0]), re.sub(r"\s", "", cells[1] or ""))
                    if not label or not local_code or (code == "EE" and not compact(cells[2]).isdigit()):
                        continue
                    # Source aggregate and subset labels remain distinct even
                    # when they reuse an ICD code.
                    try:
                        value, status = count_cell(cells[column])
                    except ValueError as exc:
                        errors.append({"page": page_number, "label": label, "reason": str(exc)}); continue
                    key = (year, current_month, local_code, label)
                    if key in seen:
                        raise ValueError("Duplicate national monthly category/month")
                    seen.add(key)
                    rows.append(monthly_row(code=code, year=year, month=current_month, local_code=local_code, label=label, value=value, status=status, page=page_number, url=source_url, digest=digest, retrieved_at=retrieved_at))
    if not rows:
        raise ValueError("No supported national single-month notification cells")
    return rows, {"url": source_url, "observations": len(rows), "failed_count": len(errors), "failures": errors, "coverage_complete": not errors}


class EUMonthlyCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, max_retries=2, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged discovery/capture and offline parse_pdf")

    def parse(self, response):
        raise NotImplementedError("Use country/edition-specific PDF parsing")
