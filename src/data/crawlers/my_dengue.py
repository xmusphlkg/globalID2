"""Source-labelled daily national and state dengue counts from official iDengue.

The separately displayed cumulative case heading has an inconsistent start year.
Neither those totals nor cumulative deaths are converted to daily observations.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "my_idengue_daily"
ONTOLOGY_SOURCE_ID = "SRC_MY_IDENGUE_DAILY"
DEFAULT_SOURCE_NAME = "Malaysia iDengue Daily Dengue Surveillance"
LANDING_URL = "https://idengue.mysa.gov.my/"
DEFINITION_VERSION = "my_idengue_explicit_daily_count_v1"
SOURCE_REGION_LABELS = frozenset({
    "JOHOR", "KEDAH", "KELANTAN", "MELAKA", "NEGERI SEMBILAN", "PAHANG",
    "PULAU PINANG", "PERAK", "PERLIS", "SELANGOR", "TERENGGANU", "SABAH",
    "SARAWAK", "WILAYAH PERSEKUTUAN", "WILAYAH PERSEKUTUAN LABUAN",
})
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
_SOURCE_DATE = r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"


class IDengueContractError(ValueError):
    """The page no longer provides unambiguous daily counts."""


@dataclass(frozen=True)
class IDengueParseResult:
    rows: list[dict[str, str]]
    source_date: date
    source_audit: dict


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def _date(value: str) -> date:
    try:
        day, month, year = value.split()
        return date(int(year), _MONTHS[month.casefold()], int(day))
    except (ValueError, KeyError) as exc:
        raise IDengueContractError(f"Invalid iDengue source date: {value!r}") from exc


def _count(value: str) -> int:
    # Commas are allowed only as thousands separators, never suppression/dashes.
    if not re.fullmatch(r"(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)", value):
        raise IDengueContractError(f"Invalid explicit daily count: {value!r}")
    return int(value.replace(",", ""))


def parse_idengue_html(payload: bytes, *, retrieved_at=None) -> IDengueParseResult:
    # The official HTML has unclosed TH/TR tags and multiple nested HTML roots.
    # lxml repairs those source defects without treating nested totals as data.
    soup = BeautifulSoup(payload, "lxml")
    candidates = [table for table in soup.find_all("table")
                  if any(_text(th) == "NEGERI" for th in table.find_all("th"))]
    if len(candidates) != 1:
        raise IDengueContractError("Expected exactly one iDengue state daily table")
    table = candidates[0]
    headers = [_text(th) for th in table.find_all("th")]
    if len(headers) != 3 or headers[0] != "NEGERI":
        raise IDengueContractError("iDengue table columns changed")
    daily_match = re.fullmatch(r"KES HARIAN PADA " + _SOURCE_DATE, headers[1], re.IGNORECASE)
    if not daily_match:
        raise IDengueContractError("Explicit daily date heading missing or changed")
    when = _date(daily_match[1])
    retrieved = datetime.fromisoformat(str(retrieved_at)) if retrieved_at else datetime.now(timezone.utc)
    if retrieved.tzinfo is None:
        raise IDengueContractError("Retrieval timestamp must include its timezone")
    if when > retrieved.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date():
        raise IDengueContractError("Daily source date is in the future at capture time")
    cumulative_match = re.fullmatch(
        r"\*?JUMLAH KES TERKUMPUL DARI " + _SOURCE_DATE + r" HINGGA " + _SOURCE_DATE,
        headers[2], re.IGNORECASE,
    )
    if not cumulative_match or _date(cumulative_match[2]) != when:
        raise IDengueContractError("Daily and cumulative table dates disagree")
    source_values, seen = [], set()
    for tr in table.find_all("tr"):
        cells = [_text(td) for td in tr.find_all("td", recursive=False)]
        if not cells:
            continue
        if len(cells) != 3:
            raise IDengueContractError("Malformed state count row")
        label, daily, cumulative = cells
        if label in seen or label == "MALAYSIA" or not label:
            raise IDengueContractError("Duplicate/invalid source region")
        seen.add(label)
        source_values.append((label, _count(daily), cumulative))
    # Fail closed on a partial page or a changed federation reporting scheme.
    if seen != SOURCE_REGION_LABELS:
        raise IDengueContractError(f"Source region coverage changed: missing={sorted(SOURCE_REGION_LABELS-seen)}, new={sorted(seen-SOURCE_REGION_LABELS)}")
    national = []
    for other in soup.find_all("table"):
        if other is table:
            continue
        for tr in other.find_all("tr"):
            cells = [_text(td) for td in tr.find_all("td", recursive=False)]
            if cells and cells[0] == "MALAYSIA":
                if len(cells) != 3:
                    raise IDengueContractError("Malformed explicit Malaysia total")
                national.append((_count(cells[1]), cells[2]))
    if len(national) != 1:
        raise IDengueContractError("Expected exactly one explicit national daily value")
    daily_total, cumulative_total = national[0]
    regional_sum = sum(value for _, value, _ in source_values)
    if daily_total != regional_sum:
        raise IDengueContractError("Explicit national daily count disagrees with state daily sum")
    page_text = _text(soup)
    if "CPRC" not in page_text or "Kementerian Kesihatan Malaysia" not in page_text:
        raise IDengueContractError("Official KKM/CPRC source attribution missing")
    digest = hashlib.sha256(payload).hexdigest()
    rows = []
    for label, count, cumulative in [("MALAYSIA", daily_total, cumulative_total), *source_values]:
        is_national = label == "MALAYSIA"
        source_code = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        geography = "country:MY:national" if is_national else f"country:MY:source_region:{source_code}"
        rows.append({
            "Date": when.isoformat(), "Year": str(when.year), "Month": str(when.month),
            "PeriodStart": when.isoformat(), "PeriodEnd": when.isoformat(),
            "Frequency": "daily", "PeriodType": "daily", "Measure": "cases", "Unit": "count",
            "Cases": str(count), "DiseaseCode": "__source_native__", "RawDiseaseLabel": "Dengue",
            "SourceDiseaseCode": "dengue", "DefinitionVersion": DEFINITION_VERSION,
            "ReportingBasis": "published_daily_count_event_date_unspecified",
            "TimeBasis": "explicit_source_daily_label", "DateRole": "source_daily_observation_date",
            "Geography": "Malaysia" if is_national else label,
            "GeographyKey": geography, "SourceGeographyLabel": label,
            "GeographyType": "national" if is_national else "source_reporting_region",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": LANDING_URL,
            "RetrievedAt": retrieved.isoformat(), "RawSHA256": digest,
            "DailyHeading": headers[1], "CumulativeCaseHeading": headers[2],
            "ExcludedCumulativeCaseText": cumulative,
            "CumulativeCaseInterpretation": "not_imported_interval_requires_review",
            "IsProvisional": "true", "DatasetStatus": "provisional_revisable",
            "AuthoritativeRevision": "true", "RevisionSemantics": "authoritative_revision",
            "PublicReleaseEnabled": "false",
            "Dimensions": json.dumps({"date_basis": "source_daily_label", "source_disease_code": "dengue", "source_metric": "daily_cases", "source_geography_code": source_code}, sort_keys=True),
        })
    death_match = re.search(r"TARIKH:\s*" + _SOURCE_DATE + r" Sehingga " + _SOURCE_DATE + r" JUMLAH KES KEMATIAN:\s*([\d,]+) Orang", page_text, re.IGNORECASE)
    excluded_deaths = {"status": "not_present_or_unrecognized"}
    if death_match:
        excluded_deaths = {"status": "excluded_cumulative_not_daily", "start": _date(death_match[1]).isoformat(), "end": _date(death_match[2]).isoformat(), "value": _count(death_match[3])}
    return IDengueParseResult(rows, when, {
        "source_date": when.isoformat(), "national_daily_count": daily_total,
        "regional_daily_sum": regional_sum, "source_regions": len(seen),
        "explicit_zero_rows": sum(row["Cases"] == "0" for row in rows),
        "cumulative_case_heading": headers[2], "cumulative_cases_excluded": True,
        "cumulative_deaths": excluded_deaths, "historical_daily_backfill_verified": False,
        "raw_sha256": digest,
    })


class MalaysiaIDengueCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=60, delay=0.2)

    def fetch_rows(self):
        return parse_idengue_html(self.get(LANDING_URL).content).rows

    async def crawl(self, **kwargs):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL,
                              raw_data={"rows": await asyncio.to_thread(self.fetch_rows)})]

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL,
                              raw_data={"rows": parse_idengue_html(response.content).rows})]
