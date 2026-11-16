"""All categories in MOH's public annual notified STD dataset.

The upstream catalogue currently contains five diseases. This is an additive
national notifications feed, not a claim to cover all Malaysian notifiable
diseases. New categories are retained source-natively for registry review.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import date, datetime, timezone
from numbers import Real

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "moh_std_annual"
DEFAULT_SOURCE_NAME = "Malaysia MOH Annual STD Notifications"
ONTOLOGY_SOURCE_ID = "SRC_MY_MOH_STD"
LANDING_URL = "https://data.gov.my/data-catalogue/std_state"
DATA_URL = "https://storage.data.gov.my/healthcare/std_state.parquet"
HISTORY_START_YEAR = 2017
SOURCE_LABELS = {"aids": "AIDS", "chancroid": "Chancroid", "gonorrhea": "Gonorrhea", "hiv": "HIV", "syphillis": "Syphilis"}


class MYNotifiableContractError(ValueError):
    """Unexpected source geography, period or measure contract."""


def parse_moh_annual_records(records, *, raw_sha256="", retrieved_at=None):
    rows, seen = [], set()
    for raw in records:
        if not {"date", "state", "disease", "cases", "incidence"}.issubset(raw):
            raise MYNotifiableContractError("MOH annual disease dataset columns changed")
        if raw["state"] != "Malaysia":
            continue
        try:
            when = date.fromisoformat(str(raw["date"]))
        except ValueError as exc:
            raise MYNotifiableContractError("Invalid MOH annual date") from exc
        if (when.month, when.day) != (1, 1):
            raise MYNotifiableContractError("Annual source period must start on January 1")
        current_year = datetime.now(timezone.utc).year
        if when.year > current_year:
            raise MYNotifiableContractError("Future annual observations require source contract review")
        code = str(raw["disease"]).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", code):
            raise MYNotifiableContractError(f"Invalid source disease code: {code!r}")
        key = (when, code)
        if key in seen:
            raise MYNotifiableContractError(f"Duplicate national disease/year: {key}")
        seen.add(key)
        value = raw["cases"]
        missing = value is None or str(value).strip().casefold() in {"", "nan", "null", "<na>"}
        if not missing:
            # A nullable Parquet integer column may arrive as float64. Accept
            # exactly integral observations while rejecting truncation.
            integral_numeric = isinstance(value, Real) and math.isfinite(value) and value >= 0 and int(value) == value
            if isinstance(value, bool) or not (integral_numeric or str(value).isdigit()):
                raise MYNotifiableContractError(f"Invalid reported count: {value!r}")
        # Keep all new upstream disease categories. The Registry decides their
        # canonical mapping; labels and original misspellings remain auditable.
        label = SOURCE_LABELS.get(code, code)
        rows.append({
            "Date": when.isoformat(), "Year": str(when.year), "Month": "1",
            "DiseaseCode": "__source_native__", "RawDiseaseLabel": label, "SourceDiseaseCode": code,
            "Cases": "" if missing else str(int(value)), "MissingValueStatus": "source_missing" if missing else "",
            "SourceIncidencePer100000": "" if raw["incidence"] is None else str(raw["incidence"]),
            "PeriodType": "annual", "Frequency": "annual", "Measure": "cases", "ReportingBasis": "notification",
            "DefinitionVersion": "my_moh_notified_annual_report_date_v1",
            "Geography": "Malaysia", "GeographyKey": "country:MY:national",
            "DatasetStatus": "closed_revisable" if when.year < current_year else "provisional",
            "IsProvisional": "false" if when.year < current_year else "true",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": LANDING_URL, "DownloadURL": DATA_URL,
            "RetrievedAt": str(retrieved_at or datetime.now(timezone.utc).isoformat()), "RawSHA256": raw_sha256,
            "License": "CC-BY-4.0", "PublicReleaseEnabled": "false",
            "Dimensions": json.dumps({"source_disease_code": code, "source_disease_label": code, "date_basis": "report_date", "dataset": "std_state"}, sort_keys=True),
        })
    if not rows:
        raise MYNotifiableContractError("No explicit national annual notifications")
    return sorted(rows, key=lambda row: (row["Date"], row["SourceDiseaseCode"]))


def parse_moh_annual_payload(payload, *, kind="parquet", retrieved_at=None):
    if kind == "parquet":
        import pandas as pd
        records = pd.read_parquet(io.BytesIO(payload)).to_dict(orient="records")
    elif kind == "csv":
        records = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    else:
        raise MYNotifiableContractError(f"Unsupported annual artifact format: {kind}")
    return parse_moh_annual_records(records, raw_sha256=hashlib.sha256(payload).hexdigest(), retrieved_at=retrieved_at)


class MalaysiaMOHNotifiableCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=90, delay=0.2)

    def fetch_rows(self):
        return parse_moh_annual_payload(self.get(DATA_URL).content)

    async def crawl(self, **kwargs):
        import asyncio
        rows = await asyncio.to_thread(self.fetch_rows)
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": rows})]

    def parse(self, response):
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": parse_moh_annual_payload(response.content)})]
