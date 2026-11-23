"""FHI Statistikk's public MSIS JSON-stat2 monthly case tables.

The November 2026 portal replaced Allvis' numeric diagnosis API. Discover the
published monthly *case* tables from its own menu, request explicit national
all-age/all-sex totals, and retain native diagnosis keys. Isolate counts and
preventive treatment are not disease cases. Missing/suppressed cells stay
unknown, and overlapping tables must agree before a row can be deduplicated.
"""
from __future__ import annotations

import hashlib
import itertools
import math
import re
from dataclasses import replace
from datetime import datetime, timezone

from .no import (
    DEFAULT_SOURCE_NAME,
    DEFAULT_SOURCE_SCOPE,
    NOContractError,
    NODiagnosis,
    NorwayMSISCrawler,
)

PORTAL_URL = "https://statistikk.fhi.no/msis"
BACKEND_URL = "https://statistikk-data.fhi.no"
CONTRACT = "fhi-msis-statistikk-jsonstat2-v1-observed-2026-11"
MONTH_DIMENSION = "proevedatomaaned"
TOTALS = {
    "bostedsfylkenrmappet": "0",
    "kjoenn": "Alle kjønn",
    "aldersgruppe_10": "Alle aldre",
    "MEASURE_TYPE": "antall_tilfeller",
}
DIMENSIONS = {"diagnose", MONTH_DIMENSION, *TOTALS}
EXCLUDED_DIAGNOSES = {"Tuberkulose - forebyggende behandling"}


def _categories(dimension):
    values = dimension.get("category", {}).get("index")
    if isinstance(values, dict):
        if sorted(values.values()) != list(range(len(values))):
            raise NOContractError("FHI JSON-stat category indexes are not contiguous")
        values = sorted(values, key=values.get)
    if (not isinstance(values, list) or not values
            or any(not isinstance(v, str) or not v for v in values)
            or len(set(values)) != len(values)):
        raise NOContractError("FHI JSON-stat has invalid or duplicate category keys")
    return values


def parse_case_cube(payload, requested):
    """Validate the exact cube grain and return explicit values plus omissions."""
    if not isinstance(payload, dict) or payload.get("class") != "dataset" or payload.get("version") != "2.0":
        raise NOContractError("FHI monthly response must be a JSON-stat2 dataset")
    ids = payload.get("id")
    if not isinstance(ids, list) or len(ids) != len(DIMENSIONS) or set(ids) != DIMENSIONS:
        raise NOContractError("FHI monthly cube dimensions changed")
    expected = {d["code"]: d["values"] for d in requested["dimensions"]}
    categories = [_categories(payload.get("dimension", {}).get(code, {})) for code in ids]
    if payload.get("size") != [len(values) for values in categories]:
        raise NOContractError("FHI monthly cube size does not match category indexes")
    for code, values in zip(ids, categories):
        if set(values) != set(expected[code]):
            raise NOContractError(f"FHI monthly cube returned unexpected {code} categories")
        if code in TOTALS and values != [TOTALS[code]]:
            raise NOContractError(f"FHI monthly cube is not the national total: {code}")
    size = 1
    for values in categories:
        size *= len(values)
    values = payload.get("value")
    if not isinstance(values, (list, dict)) or (isinstance(values, list) and len(values) != size):
        raise NOContractError("FHI monthly cube value array has an invalid size")
    if isinstance(values, dict) and any(not str(k).isdigit() or int(k) >= size for k in values):
        raise NOContractError("FHI monthly cube contains invalid value indexes")
    statuses = payload.get("status", "")
    if not isinstance(statuses, (str, list, dict)):
        raise NOContractError("FHI monthly cube has invalid cell statuses")
    if isinstance(statuses, list) and len(statuses) != size:
        raise NOContractError("FHI monthly cube status array has an invalid size")
    rows, missing = [], []
    for index, coordinates in enumerate(itertools.product(*categories)):
        cell = dict(zip(ids, coordinates))
        month = cell[MONTH_DIMENSION]
        if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
            raise NOContractError("FHI monthly cube has an invalid source month")
        value = values[index] if isinstance(values, list) else values.get(str(index))
        status = (statuses[index] if isinstance(statuses, list)
                  else statuses.get(str(index), "") if isinstance(statuses, dict) else statuses)
        if value is None or status:
            missing.append({"diagnosis": cell["diagnose"], "month": month,
                            "status": status or "missing"})
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise NOContractError("FHI monthly case count is not a nonnegative integer")
        rows.append((cell["diagnose"], month, int(value)))
    return rows, missing


class NorwayStatisticsCrawler(NorwayMSISCrawler):
    """Current portal transport; the legacy crawler remains for archived bytes."""

    SOURCE_URL = PORTAL_URL
    contract_version = CONTRACT

    def __init__(self, *, backend_url=BACKEND_URL, **kwargs):
        kwargs.setdefault("portal_url", PORTAL_URL)
        super().__init__(**kwargs)
        self.backend_url = backend_url.rstrip("/")
        self._tables = None
        self.source_audit = {"status": "validated", "contract": CONTRACT,
                             "tables": [], "missing_cells": 0, "missing_examples": [],
                             "duplicate_rows": 0, "excluded_diagnoses": sorted(EXCLUDED_DIAGNOSES),
                             "unavailable_table_months": [], "archived_table_months_excluded": [],
                             "coverage_note": "Published monthly case tables only; isolate counts and preventive treatment are excluded. Native diagnosis keys are retained for registry review."}

    def _metadata(self, suffix):
        return self._request_json(f"{self.backend_url}/api/internal/msis/Menu/{suffix}",
                                  endpoint_name="statistikk_metadata")[0]

    def _load_tables(self):
        if self._tables is not None:
            return self._tables
        default = self._metadata("default")
        identifier = default.get("identifier") if isinstance(default, dict) else None
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]+", identifier):
            raise NOContractError("FHI MSIS menu lacks its public default identifier")
        menu = self._metadata(f"menu-structure/{identifier}")
        if not isinstance(menu, dict) or not isinstance(menu.get("menu"), list):
            raise NOContractError("FHI MSIS public menu contract changed")
        links = []

        def walk(nodes, path=()):
            for node in nodes:
                if not isinstance(node, dict):
                    raise NOContractError("FHI MSIS menu node is not an object")
                name = str(node.get("name") or "")
                children = node.get("children")
                if children:
                    walk(children, (*path, name))
                elif "tilfeller per måned" in name.casefold():
                    link = node.get("routerLink") or ""
                    if not re.fullmatch(r"/msis/[\w-]+", link):
                        raise NOContractError("FHI MSIS monthly menu link changed")
                    links.append((link.rsplit("/", 1)[-1], " / ".join(path), name))

        walk(menu["menu"])
        if not links or len(links) > 32:
            raise NOContractError("FHI MSIS monthly case table discovery is empty or unexpectedly large")
        tables = []
        seen = set()
        for ident, group, title in links:
            metadata = self._metadata(f"menu-item-data/{ident}")
            table_id = metadata.get("tableId")
            if type(table_id) is not int or table_id <= 0:
                raise NOContractError("FHI MSIS monthly table ID is invalid")
            if table_id in seen:
                continue
            seen.add(table_id)
            raw_dimensions = metadata.get("dimensions", {}).get("dimensions", [])
            dimensions = {d["code"]: d.get("categories") for d in raw_dimensions}
            if set(dimensions) != DIMENSIONS or len(raw_dimensions) != len(DIMENSIONS):
                raise NOContractError(f"FHI MSIS table {table_id} has an unsupported grain")
            categories = {}
            for code, entries in dimensions.items():
                if not isinstance(entries, list) or not entries:
                    raise NOContractError(f"FHI MSIS table {table_id} has no {code} categories")
                categories[code] = {e["value"]: e["label"] for e in entries}
                if len(categories[code]) != len(entries):
                    raise NOContractError("FHI MSIS metadata contains duplicate category keys")
                if code in TOTALS and categories[code].get(TOTALS[code]) is None:
                    raise NOContractError(f"FHI MSIS table {table_id} lacks {code} total")
            if any(not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value)
                   for value in categories[MONTH_DIMENSION]):
                raise NOContractError("FHI MSIS table metadata has invalid months")
            tables.append({"id": table_id, "group": group, "title": title, "categories": categories})
            self.source_audit["tables"].append({"id": table_id, "group": group,
                "earliest_month": min(categories[MONTH_DIMENSION]),
                "latest_month": max(categories[MONTH_DIMENSION])})
        self._tables = tables
        return tables

    def fetch_diagnoses(self):
        diagnoses = {}
        for table in self._load_tables():
            for code, label in table["categories"]["diagnose"].items():
                if code in EXCLUDED_DIAGNOSES:
                    continue
                existing = diagnoses.get(code)
                if existing is not None and existing.name != label:
                    raise NOContractError("FHI monthly tables disagree about a diagnosis label")
                diagnoses.setdefault(code, NODiagnosis(code, label, table["id"], table["group"]))
        self.source_audit["source_native_diagnoses"] = sorted(diagnoses)
        return list(diagnoses.values())

    def fetch_source_year_range(self):
        months = [m for t in self._load_tables() for m in t["categories"][MONTH_DIMENSION]]
        return int(min(months)[:4]), int(max(months)[:4])

    def _requests(self, year, target_months, diagnoses):
        selected = {d.code for d in diagnoses}
        wanted = {f"{y:04d}-{m:02d}" for y, m in target_months if y == year}
        for table in self._load_tables():
            months = sorted(wanted & table["categories"][MONTH_DIMENSION].keys())
            names = sorted(selected & table["categories"]["diagnose"].keys())
            if not names:
                continue
            unavailable = wanted - table["categories"][MONTH_DIMENSION].keys()
            archive = re.search(r"\((\d{4})-(\d{4})\)", table["title"])
            outside_archive = ({m for m in unavailable
                                if not int(archive[1]) <= int(m[:4]) <= int(archive[2])}
                               if archive else set())
            for missing, field in ((outside_archive, "archived_table_months_excluded"),
                                   (unavailable - outside_archive, "unavailable_table_months")):
                if missing:
                    self.source_audit[field].append({"table_id": table["id"], "months": sorted(missing),
                                                     "diagnoses": names})
                    if field == "unavailable_table_months":
                        self.source_audit["status"] = "partial"
            if not months:
                continue
            choices = {"diagnose": names, MONTH_DIMENSION: months,
                       **{code: [value] for code, value in TOTALS.items()}}
            body = {"response": {"format": "json-stat2"}, "dimensions": [
                {"code": code, "filter": "item", "values": choices[code]}
                for code in ["diagnose", MONTH_DIMENSION, *TOTALS]]}
            yield table, body

    def collect_monthly_responses(self, months):
        """Decode metadata only; case cubes are interpreted during organize."""
        diagnoses = self.fetch_diagnoses()
        for year in sorted({y for y, _ in months}):
            for table, body in self._requests(year, months, diagnoses):
                self.post(f"{self.backend_url}/api/open/v1/msis/Table/{table['id']}/data", json=body)

    def fetch_year_rows(self, year, *, diagnoses, target_months, as_of, include_current_month):
        eligible = {key for key in target_months if key < (as_of.year, as_of.month)
                    or (include_current_month and key == (as_of.year, as_of.month))}
        self.source_audit["requested_months"] = sorted(f"{y:04d}-{m:02d}" for y, m in eligible)
        by_code = {d.code: d for d in diagnoses}
        rows = {}
        for table, body in self._requests(year, eligible, diagnoses):
            url = f"{self.backend_url}/api/open/v1/msis/Table/{table['id']}/data"
            response = self.post(url, json=body)
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise NOContractError(f"FHI MSIS table {table['id']} returned non-JSON content")
            payload = response.json()
            values, missing = parse_case_cube(payload, body)
            self.source_audit["missing_cells"] += len(missing)
            self.source_audit["missing_examples"] = (self.source_audit["missing_examples"] + missing)[:50]
            if missing:
                self.source_audit["status"] = "partial"
            digest = hashlib.sha256(response.content).hexdigest()
            retrieved = datetime.now(timezone.utc).isoformat()
            raw_artifact = self._archive_response(
                endpoint_name=f"table_{table['id']}_{year}", year=year,
                request_url=url, params=[], request_body=body, response=response,
                payload=payload, retrieved_at=retrieved, response_sha256=digest,
            )
            for code, month, cases in values:
                key = (code, month)
                if key in rows:
                    if rows[key]["Cases"] != str(cases):
                        raise NOContractError(f"FHI overlapping monthly tables disagree for {code} in {month}")
                    self.source_audit["duplicate_rows"] += 1
                    continue
                rows[key] = {"Date": month + "-01", "RawDiseaseLabel": by_code[code].name,
                    "DiseaseCode": code, "DiseaseGroup": table["group"], "Year": month[:4],
                    "Month": str(int(month[5:])), "Cases": str(cases), "Deaths": "",
                    "ReportingArea": "Norway national",
                    "DataStatus": "provisional" if month == as_of.strftime("%Y-%m") else "closed",
                    "AuthoritativeRevision": "true",
                    "UpdateMode": "dynamic_provisional" if month == as_of.strftime("%Y-%m") else "authoritative_revision",
                    "Source": DEFAULT_SOURCE_NAME, "SourceScope": DEFAULT_SOURCE_SCOPE,
                    "SourceURL": url, "RetrievedAt": retrieved, "SourceContract": CONTRACT,
                    "RawArtifact": raw_artifact, "RawSHA256": digest}
        return sorted(rows.values(), key=lambda row: (row["Date"], row["RawDiseaseLabel"]))

    def crawl_monthly_national(self, *args, **kwargs):
        summary = super().crawl_monthly_national(*args, **kwargs)
        self.source_audit["rows"] = summary.row_count
        return replace(summary, contract_version=CONTRACT)
