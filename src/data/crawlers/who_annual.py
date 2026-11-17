"""Country-reported WHO GHO annual counts with explicit country contracts.

These indicators are reported notifications, not modelled incidence. WHO is
recorded as distributor and each country's provenance remains separate.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from .base import BaseCrawler
from .my_who_annual import BASE_URL, INDICATORS, LANDING_URL, METADATA_URL, validate_indicators
from .my_who_flunet import count_value

DEFAULT_SCOPE = "who_gho_annual"
HISTORY_START_YEAR = 1900
COUNTRY_CONTRACTS = {
    "ET": {"iso3": "ETH", "name": "Ethiopia"},
    "KE": {"iso3": "KEN", "name": "Kenya"},
    "UG": {"iso3": "UGA", "name": "Uganda"},
    "GH": {"iso3": "GHA", "name": "Ghana"},
    "PH": {"iso3": "PHL", "name": "Philippines"},
    "VN": {"iso3": "VNM", "name": "Viet Nam"},
    "LA": {"iso3": "LAO", "name": "Lao PDR"},
    "MM": {"iso3": "MMR", "name": "Myanmar"},
    "BN": {"iso3": "BRN", "name": "Brunei Darussalam"},
    "TL": {"iso3": "TLS", "name": "Timor-Leste"},
    "TZ": {"iso3": "TZA", "name": "Tanzania"},
    "PY": {"iso3": "PRY", "name": "Paraguay"},
    "EC": {"iso3": "ECU", "name": "Ecuador"},
    "CR": {"iso3": "CRI", "name": "Costa Rica"},
    "PA": {"iso3": "PAN", "name": "Panama"},
}
for _code, _contract in COUNTRY_CONTRACTS.items():
    _contract.update(source_id=f"SRC_{_code}_WHO_GHO", source_name=f"{_contract['name']} WHO GHO Annual Reported Infections")


def country_contract(country_code):
    code = str(country_code).upper()
    if code not in COUNTRY_CONTRACTS:
        raise ValueError(f"Unreviewed WHO GHO country: {country_code!r}")
    return code, COUNTRY_CONTRACTS[code]


def data_url(code, country_code):
    _, contract = country_contract(country_code)
    if code not in INDICATORS:
        raise ValueError("Unreviewed WHO GHO indicator")
    return BASE_URL + code + "?" + urlencode({"$filter": f"SpatialDim eq '{contract['iso3']}'"})


def parse_records(records, *, country_code, metadata, as_of=None):
    code2, contract = country_contract(country_code)
    as_of = as_of or datetime.now(timezone.utc).date()
    validate_indicators(metadata)
    rows, seen = [], set()
    for code, raw, provenance in records:
        if code not in INDICATORS or raw.get("IndicatorCode") != code or raw.get("SpatialDim") != contract["iso3"] or raw.get("SpatialDimType") != "COUNTRY" or raw.get("TimeDimType") != "YEAR":
            raise ValueError("WHO GHO indicator/country/annual contract disagrees")
        if any(raw.get(k) is not None for k in ("Dim1", "Dim2", "Dim3", "Dim1Type", "Dim2Type", "Dim3Type")):
            raise ValueError("Unreviewed WHO GHO stratification; cannot treat as national total")
        year = raw.get("TimeDim")
        if type(year) is not int or not HISTORY_START_YEAR <= year < as_of.year:
            raise ValueError("WHO GHO year must be a reviewed closed calendar year")
        identity = (code, year)
        if identity in seen:
            raise ValueError("Duplicate WHO GHO indicator/year")
        seen.add(identity)
        if raw.get("Low") is not None or raw.get("High") is not None:
            raise ValueError("WHO GHO estimate/interval cannot become a reported count")
        value = count_value(raw.get("NumericValue"))
        status = "source_suppressed" if value is not None and not value.isdigit() else "reported"
        if value is None:
            source_text = str(raw.get("Value") or "").strip()
            if re.fullmatch(r"\d+|\d{1,3}(?:[ ,]\d{3})+", source_text):
                value = str(int(source_text.replace(" ", "").replace(",", "")))
            elif (source_text.startswith("<") and source_text[1:].strip().isdigit()) or source_text in {"*", "-", "suppressed"}:
                value, status = source_text, "source_suppressed"
            elif source_text.casefold() in {"", "none", "no data", "not available", "null", "na", "n/a"}:
                value, status = "", "source_missing"
            else:
                raise ValueError("WHO GHO nonnumeric value needs explicit missing/suppression review")
        label, measure = INDICATORS[code]
        rows.append({
            "Date": f"{year}-01-01", "Year": str(year), "Month": "1",
            "PeriodStart": f"{year}-01-01", "PeriodEnd": f"{year}-12-31",
            "DiseaseCode": "__source_native__", "SourceDiseaseCode": code,
            "RawDiseaseLabel": label, "Cases": value, "SourceReportedValue": raw.get("Value"),
            "MissingValueStatus": status, "SourceRecordId": raw["Id"],
            "Frequency": "annual", "PeriodType": "annual", "Measure": measure, "Unit": "count",
            "ReportingBasis": "who_country_reported_notifications", "DefinitionVersion": "who_gho_country_reported_annual_v1",
            "Geography": contract["name"], "GeographyKey": f"country:{code2}:national",
            "Source": contract["source_name"], "SourceURL": LANDING_URL,
            "SourcePublishedAt": raw.get("Date"), "SourceDataSourceDim": raw.get("DataSourceDim"),
            "SourceTimeDimensionBegin": raw.get("TimeDimensionBegin"), "SourceTimeDimensionEnd": raw.get("TimeDimensionEnd"),
            "SourceComments": raw.get("Comments"), "SourceCountryCode": contract["iso3"],
            "DatasetStatus": "closed_revisable", "IsProvisional": "false",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "PublicReleaseEnabled": "false", "AggregationPolicy": "non_additive",
            "Dimensions": json.dumps({"who_indicator": code}, sort_keys=True), **provenance,
        })
    if not rows:
        raise ValueError(f"No {code2} WHO reported annual records")
    return rows


class WHOAnnualCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, max_retries=2, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError("Use staged WHO artifact capture")

    def parse(self, response):
        raise NotImplementedError("GHO needs captured indicator and page provenance")
