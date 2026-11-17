"""ECDC respiratory laboratory surveillance, preserving every source stratum."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import re

from .base import BaseCrawler

DEFAULT_SCOPE = "ecdc_respiratory_weekly"
REPOSITORY = "EU-ECDC/Respiratory_viruses_weekly_data"
LANDING_URL = f"https://github.com/{REPOSITORY}"
TREE_URL = f"https://api.github.com/repos/{REPOSITORY}/git/trees/main?recursive=1"
DATASETS = {
    "data/nonSentinelTestsDetections.csv": "non-sentinel",
    "data/sentinelTestsDetectionsPositivity.csv": "primary care sentinel",
    "data/SARITestsDetectionsPositivity.csv": "SARI virological",
}
COUNTRY_NAMES = dict(zip(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IS IE IT LV LI LT LU MT NL NO PL PT RO SK SI ES SE".split(),
    ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"],
))
HEADERS = ["survtype", "countryname", "yearweek", "pathogen", "pathogentype", "pathogensubtype", "indicator", "age", "value"]


def parse_value(value, *, percentage=False):
    token = str(value).strip()
    if token in {"", "NA", "N/A", "NaN", "null"}:
        return "", "source_missing"
    if token in {"*", "suppressed"} or re.fullmatch(r"<\s*\d+", token):
        return token, "source_suppressed"
    try:
        numeric = float(token)
    except ValueError as exc:
        raise ValueError(f"Unreviewed ECDC numeric cell: {token!r}") from exc
    if not math.isfinite(numeric) or numeric < 0 or (percentage and numeric > 100) or (not percentage and not numeric.is_integer()):
        raise ValueError(f"Invalid ECDC {'percentage' if percentage else 'count'}: {token!r}")
    return (format(numeric, ".15g") if percentage else str(int(numeric))), "reported"


def parse_csv(payload, *, country_code, dataset, provenance=None, as_of=None):
    """Parse only explicit closed ISO week dates; counts and positivity stay apart."""
    code = country_code.upper()
    if code not in COUNTRY_NAMES or dataset not in DATASETS:
        raise ValueError("Unsupported ECDC country or laboratory dataset")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if reader.fieldnames != HEADERS:
        raise ValueError(f"ECDC laboratory CSV header changed: {reader.fieldnames!r}")
    today = as_of or datetime.now(timezone.utc).date()
    rows, seen = [], set()
    audit = {"dataset": dataset, "source_records": 0, "country_records": 0, "withheld_open_week": 0}
    for number, raw in enumerate(reader, 2):
        audit["source_records"] += 1
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError("ECDC CSV row width differs from header")
        if raw["countryname"] != COUNTRY_NAMES[code]:
            continue
        audit["country_records"] += 1
        if raw["survtype"] != DATASETS[dataset]:
            raise ValueError("ECDC surveillance type contradicts dataset contract")
        match = re.fullmatch(r"(\d{4})-W(\d{2})", raw["yearweek"])
        if not match:
            raise ValueError("ECDC source week lost ISO 8601 year-week notation")
        year, week = map(int, match.groups())
        start = date.fromisocalendar(year, week, 1)
        end = start + timedelta(days=6)
        if year < 2022 or start > today:
            raise ValueError("ECDC source contains unsupported or future week")
        if end >= today:
            audit["withheld_open_week"] += 1
            continue
        indicator = raw["indicator"]
        if indicator not in {"tests", "detections", "positivity"}:
            raise ValueError(f"Unreviewed ECDC laboratory indicator: {indicator!r}")
        if raw["pathogen"] not in {"Influenza", "RSV", "SARS-CoV-2"}:
            raise ValueError("Unreviewed ECDC respiratory pathogen")
        if not all(raw[k].strip() for k in HEADERS[:-1]):
            raise ValueError("ECDC source identity contains an empty stratum")
        dimensions = {k: raw[k] for k in ("survtype", "pathogen", "pathogentype", "pathogensubtype", "indicator", "age")}
        dimensions["source_dataset"] = dataset
        identity = (raw["yearweek"], json.dumps(dimensions, sort_keys=True))
        if identity in seen:
            raise ValueError("Duplicate ECDC country/week/laboratory stratum")
        seen.add(identity)
        value, status = parse_value(raw["value"], percentage=indicator == "positivity")
        source_code = "resp_" + hashlib.sha256(identity[1].encode()).hexdigest()[:28]
        label = " | ".join(raw[k] for k in ("pathogen", "pathogentype", "pathogensubtype", "indicator", "survtype", "age"))
        rows.append({
            "Date": start.isoformat(), "Year": str(start.year), "Month": str(start.month),
            "PeriodStart": start.isoformat(), "PeriodEnd": end.isoformat(), "SourceYear": str(year), "SourceWeek": str(week),
            "SourcePeriodLabel": raw["yearweek"], "Calendar": "ISO-8601", "TimeBasis": "source_iso_yearweek",
            "CountryCode": code, "DiseaseCode": "__source_native__", "SourceDiseaseCode": source_code,
            "RawDiseaseLabel": label, "Cases": value, "SourceReportedValue": raw["value"], "MissingValueStatus": status,
            "Measure": {"tests": "laboratory_tests", "detections": "laboratory_detections", "positivity": "test_positivity"}[indicator],
            "Unit": "percent" if indicator == "positivity" else "count", "Frequency": "weekly", "PeriodType": "weekly",
            "ReportingBasis": "ecdc_respiratory_laboratory_surveillance", "DefinitionVersion": "ecdc_respiratory_laboratory_v1",
            "Geography": COUNTRY_NAMES[code] + " reporting laboratory network", "GeographyKey": f"country:{code}:national",
            "PopulationScope": "reporting_laboratory_network_not_population_cases", "AggregationPolicy": "non_additive",
            "Source": f"{COUNTRY_NAMES[code]} ECDC Weekly Respiratory Laboratory Surveillance", "SourceURL": LANDING_URL,
            "SourceRow": number, "SourceDataset": dataset, "SourceSurveillanceType": raw["survtype"], "AgeGroup": raw["age"],
            "DatasetStatus": "reported_revisable", "IsProvisional": "true", "RevisionSemantics": "authoritative_revision",
            "AuthoritativeRevision": "true", "PublicReleaseEnabled": "true", "Dimensions": json.dumps(dimensions, sort_keys=True),
            **(provenance or {}),
        })
    audit.update(observations=len(rows), missing_rows=sum(r["MissingValueStatus"] == "source_missing" for r in rows))
    return rows, audit


class ECDCRespiratoryCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, max_retries=2, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged immutable ECDC tree/blob capture")

    def parse(self, response):
        raise NotImplementedError("Use parse_csv with source dataset and country")
