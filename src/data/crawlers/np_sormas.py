"""Read-only, aggregate Nepal SORMAS public bulletin contract."""
from __future__ import annotations

import hashlib
import json
import re

from .base import BaseCrawler

DEFAULT_SCOPE = "np_sormas_weekly"
DEFAULT_SOURCE_NAME = "Nepal EDCD SORMAS Public Weekly Bulletin"
ONTOLOGY_SOURCE_ID = "SRC_NP_EDCD_SORMAS"
HISTORY_START_YEAR = 2026
PUBLIC_URL = "https://analysis.edcd.gov.np/"
API_URL = PUBLIC_URL + "sormas-public-rest/public/v1"
WEEKS_URL = API_URL + "/bulletin/weeks"


class NepalSORMASContractError(ValueError):
    pass


class NepalSORMASCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(delay=0, timeout=30, **kwargs)

    def crawl(self, **kwargs):
        raise NotImplementedError("Use the staged np_sormas connector")

    def parse(self, content, **kwargs):
        return parse_bulletin(content, **kwargs)


def _integer(value, label, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NepalSORMASContractError(f"Invalid {label}: {value!r}")
    return value


def discover_weeks(payload, *, start_year=HISTORY_START_YEAR, end_year=9999):
    values = json.loads(payload)
    if not isinstance(values, list):
        raise NepalSORMASContractError("Expected public bulletin week list")
    result = {}
    for item in values:
        year = _integer(item["year"], "year", minimum=2000)
        week = _integer(item["week"], "week", minimum=1)
        if week > 53:
            raise NepalSORMASContractError("Invalid bulletin week")
        key = (year, week)
        if key in result:
            raise NepalSORMASContractError("Duplicate public bulletin week")
        if int(start_year) <= year <= int(end_year):
            result[key] = {"year": year, "week": week, "locked_at": item.get("lockedAt"),
                           "url": f"{API_URL}/bulletin/{year}/{week}"}
    return [result[key] for key in sorted(result, reverse=True)]


def parse_bulletin(payload, report):
    document = json.loads(payload)
    year, week = report["year"], report["week"]
    if (document.get("year"), document.get("week")) != (year, week):
        raise NepalSORMASContractError("Response does not match requested source week")
    items, distribution = document.get("items"), document.get("distributionRows")
    provinces = document.get("provinceNames")
    if (not isinstance(items, list) or not items or not isinstance(distribution, list)
            or not isinstance(provinces, list) or len(set(provinces)) != len(provinces)):
        raise NepalSORMASContractError("Missing or duplicate public bulletin dimensions")
    by_label = {}
    for item in distribution:
        if item["label"] in by_label:
            raise NepalSORMASContractError("Duplicate distribution category")
        by_label[item["label"]] = item
    rows, seen = [], set()
    digest = hashlib.sha256(payload).hexdigest()
    for item in items:
        code, label = item["code"], item["label"]
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]+", code) or code in seen:
            raise NepalSORMASContractError("Invalid or duplicate source disease code")
        seen.add(code)
        if not isinstance(item.get("syndrome"), bool):
            raise NepalSORMASContractError("Missing syndrome classification")
        count = _integer(item["selectedWeekCount"], "selected week count")
        selected = [point for point in item.get("series", [])
                    if (point.get("year"), point.get("week")) == (year, week)]
        if len(selected) != 1 or selected[0].get("count") != count:
            raise NepalSORMASContractError("Weekly summary does not reconcile to trend point")
        category = "syndrome" if item["syndrome"] else "disease"
        cells = [("Nepal", "country:NP:national", count)]
        area = by_label.get(label)
        if area is not None:
            counts = area.get("provinceCounts")
            if not isinstance(counts, dict) or set(counts) - set(provinces):
                raise NepalSORMASContractError("Unexpected province dimensions")
            numeric = [_integer(v, "province count") for v in counts.values() if v is not None]
            if area.get("totalCount") != count:
                raise NepalSORMASContractError("Distribution total conflicts with selected week count")
            if len(numeric) == len(provinces) and sum(numeric) != count:
                raise NepalSORMASContractError("Province counts do not reconcile to reported total")
            for province in provinces:
                # Absent and null cells remain missing; never apply the UI's ?? 0 default.
                value = counts.get(province)
                slug = re.sub(r"[^a-z0-9]+", "_", province.casefold()).strip("_")
                cells.append((province, f"country:NP:source_province:{slug}", value))
        for geography, geography_key, value in cells:
            rows.append({
                "Date": f"{year}-01-01", "Year": str(year), "Month": "1",
                "SourceYear": str(year), "SourceWeek": str(week),
                "SourceReportYear": str(year), "SourceReportWeek": str(week),
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": label,
                "SourceDiseaseCode": f"sormas:{code}:{category}:weekly:cases",
                "Cases": "" if value is None else str(value),
                "MissingValueStatus": "source_missing" if value is None else "",
                "Measure": "cases", "Unit": "count", "Frequency": "weekly", "PeriodType": "source_week",
                "ReportingBasis": "source_unspecified",
                "DefinitionVersion": "np_sormas_public_selected_week_v1",
                "Geography": geography, "GeographyKey": geography_key,
                "CalendarStatus": "source_week_boundaries_unverified",
                "TimeBasis": "source_year_week_unverified_calendar", "DateRole": "source_year_storage_anchor_only",
                "Source": DEFAULT_SOURCE_NAME, "SourceURL": report["url"],
                "SourceListingURL": WEEKS_URL, "RawSHA256": digest,
                "PublicationDate": document.get("lockedAt", ""),
                "SourceTrendWindowStart": document.get("periodStart"),
                "SourceTrendWindowEnd": document.get("periodEnd"),
                "DatasetStatus": "provisional", "IsProvisional": "true",
                "AuthoritativeRevision": "true", "RevisionSemantics": "authoritative_revision",
                "PublicReleaseEnabled": "false", "License": "reuse_terms_unverified",
                "Dimensions": json.dumps({"source_year": year, "source_week": week,
                    "category_type": category, "case_classification": "source_unspecified",
                    "surveillance_scope": "public_sormas_reporting_network"}, sort_keys=True),
            })
    return rows, {"year": year, "week": week, "observations": len(rows), "categories": len(seen),
                  "calendar_status": "source_week_boundaries_unverified",
                  "storage_date_role": "source_year_anchor_only"}
