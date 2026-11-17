"""INS SIVIGILA municipality/week history from Colombia's official SODA release."""
from __future__ import annotations

import csv
from datetime import date, timedelta
import hashlib
import io
import json
import re
import time

import requests

DEFAULT_SCOPE = "co_ins_sivigila_historical"
DEFAULT_SOURCE_NAME = "Colombia INS SIVIGILA Historical Public Health Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_CO_INS_SIVIGILA"
HISTORY_START_YEAR = 2007
DATASET_ID = "4hyg-wa9d"
LANDING_URL = f"https://www.datos.gov.co/d/{DATASET_ID}"
METADATA_URL = f"https://www.datos.gov.co/api/views/{DATASET_ID}.json"
RECORD_URL = f"https://www.datos.gov.co/resource/{DATASET_ID}.csv"
COUNT_URL = f"https://www.datos.gov.co/resource/{DATASET_ID}.json"
FIELDS = ("cod_eve", "nombre_evento", "semana", "ano", "cod_dpto_o", "cod_mun_o",
          "departamento_ocurrencia", "municipio_ocurrencia", "conteo", "socrata_id")
PARSER_VERSION = "co-ins-sivigila-municipality-week-v1"


class ColombiaSIVIGILAContractError(ValueError):
    pass


def parse_metadata(payload: bytes) -> dict:
    try:
        document = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ColombiaSIVIGILAContractError("Invalid official SODA metadata") from exc
    fields = [column.get("fieldName") for column in document.get("columns", [])]
    if document.get("id") != DATASET_ID or not set(FIELDS[:-1]).issubset(fields):
        raise ColombiaSIVIGILAContractError("Wrong dataset or changed SIVIGILA columns")
    if document.get("attribution") != "Instituto Nacional de Salud - INS, Bogotá D.C.":
        raise ColombiaSIVIGILAContractError("Dataset publisher changed")
    return {"dataset_id": DATASET_ID, "rows_updated_at": document.get("rowsUpdatedAt"),
            "view_last_modified": document.get("viewLastModified"),
            "license_id": document.get("licenseId"), "source_fields": fields}


def count_query(year: int) -> dict[str, str]:
    return {"$select": "count(*) as n", "$where": f"ano = {year}"}


def page_query(year: int, *, offset: int, limit: int) -> dict[str, str | int]:
    return {"$select": "*, :id as socrata_id", "$where": f"ano = {year}",
            "$order": ":id", "$limit": limit, "$offset": offset}


def parse_count(payload: bytes) -> int:
    try:
        document = json.loads(payload)
        value = int(document[0]["n"])
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ColombiaSIVIGILAContractError("Invalid official year count") from exc
    if len(document) != 1 or value < 0:
        raise ColombiaSIVIGILAContractError("Invalid official year count")
    return value


def can_skip_import(live_version: dict, live_counts: dict, state: dict, acceptance: dict) -> bool:
    """Allow a no-op only after a complete, independently reconciled history."""
    counts = {str(year): int(total) for year, total in live_counts.items()}
    identity = state.get("identity") or {}
    accepted = acceptance.get("years") or {}
    return bool(
        counts
        and state.get("status") == "complete"
        and identity.get("dataset_version") == live_version
        and identity.get("year_counts") == counts
        and int(identity.get("start_year", -1)) == min(map(int, counts))
        and int(identity.get("end_year", -1)) == max(map(int, counts))
        and state.get("completed_rows") == state.get("expected_rows") == sum(counts.values())
        and acceptance.get("complete") is True
        and acceptance.get("dataset_version") == live_version
        and set(accepted) == set(counts)
        and all(accepted[year].get("complete") is True
                and accepted[year].get("official_rows") == counts[year]
                and accepted[year].get("database_rows") == counts[year]
                for year in counts)
    )


def _integer(value: str, field: str, *, minimum=0, maximum=None) -> int:
    if not re.fullmatch(r"\d+", str(value or "")):
        raise ColombiaSIVIGILAContractError(f"Invalid {field}: {value!r}")
    number = int(value)
    if number < minimum or (maximum is not None and number > maximum):
        raise ColombiaSIVIGILAContractError(f"Out of range {field}: {value!r}")
    return number


def _week_anchor(year: int, week: int) -> date:
    # The dataset names only an epidemiological week, without a dated calendar.
    # This private ordering anchor is explicitly not a claimed event date.
    return date(year, 1, 1) + timedelta(days=7 * (week - 1))


def parse_page(payload: bytes, *, year: int, offset: int, expected_rows: int,
               source_url: str, dataset_version: dict) -> tuple[list[dict], dict]:
    if not payload or len(payload) > 50_000_000:
        raise ColombiaSIVIGILAContractError("Empty or implausibly large SODA page")
    digest = hashlib.sha256(payload).hexdigest()
    text = io.StringIO(payload.decode("utf-8-sig"), newline="")
    reader = csv.DictReader(text)
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ColombiaSIVIGILAContractError("SIVIGILA CSV schema changed")
    rows = []
    identities = set()
    for source in reader:
        if None in source or any(value is None for value in source.values()):
            raise ColombiaSIVIGILAContractError("Malformed SODA CSV row")
        source_year = _integer(source["ano"], "ano", minimum=HISTORY_START_YEAR)
        week = _integer(source["semana"], "semana", minimum=1, maximum=53)
        event = _integer(source["cod_eve"], "cod_eve")
        department = _integer(source["cod_dpto_o"], "cod_dpto_o", maximum=99)
        municipality = _integer(source["cod_mun_o"], "cod_mun_o", maximum=99999)
        count = _integer(source["conteo"], "conteo")
        if source_year != year:
            raise ColombiaSIVIGILAContractError("SODA page contains another source year")
        if municipality and department and municipality // 1000 != department:
            raise ColombiaSIVIGILAContractError("Municipality and department source codes conflict")
        label = " ".join(source["nombre_evento"].split())
        department_name = " ".join(source["departamento_ocurrencia"].split())
        municipality_name = " ".join(source["municipio_ocurrencia"].split())
        if not label or not department_name or not municipality_name or not source["socrata_id"]:
            raise ColombiaSIVIGILAContractError("Missing source event or geographic identity")
        identity = (source_year, week, event, department, municipality)
        if identity in identities:
            raise ColombiaSIVIGILAContractError("Duplicate event/week/municipality within SODA page")
        identities.add(identity)
        geocode = f"{municipality:05d}" if municipality else "00000"
        exterior = department_name.casefold() == "exterior"
        unknown = municipality % 1000 == 0 or "desconocid" in municipality_name.casefold()
        location_type = ("source_external" if exterior else
                         "source_unknown" if unknown else "source_municipality")
        geography_key = (f"source:{ONTOLOGY_SOURCE_ID}:exterior:{geocode}" if exterior else
                         f"source:{ONTOLOGY_SOURCE_ID}:unknown-location:{department:02d}" if unknown else
                         f"country:CO:source-municipality:{geocode}")
        dimensions = {"source_epi_year": source_year, "source_epi_week": week,
                      "source_department_code": f"{department:02d}",
                      "source_municipality_code": geocode,
                      "date_role": "synthetic_week_ordinal_anchor_not_event_date",
                      "source_calendar": "week_boundaries_unverified",
                      "source_geo_status": location_type}
        anchor = _week_anchor(source_year, week)
        rows.append({"Date": anchor.isoformat(), "Year": str(source_year),
            "Month": str(anchor.month), "DiseaseCode": "__source_native__",
            "SourceDiseaseCode": f"sivigila_{event}", "RawDiseaseLabel": label,
            "Cases": str(count), "Measure": "source_reported_event_count", "Unit": "count",
            "Frequency": "weekly", "PeriodType": "source_epi_week",
            "ReportingBasis": "sivigila_municipality_event_summary",
            "DefinitionVersion": PARSER_VERSION, "Geography": municipality_name,
            "GeographyKey": geography_key, "Geocode": geocode,
            "ReportingArea": municipality_name, "ParentCountryCode": "CO",
            "LocationType": location_type,
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": source_url,
            "SourceLandingPageURL": LANDING_URL, "RawSHA256": digest,
            "SourceRecordID": source["socrata_id"],
            "SourceDatasetRowsUpdatedAt": str(dataset_version.get("rows_updated_at") or ""),
            "DatasetStatus": "published_revisable", "IsProvisional": "false",
            "AuthoritativeRevision": "false", "PublicReleaseEnabled": "false",
            "License": "CC BY-SA 4.0; attribution to INS",
            "Dimensions": json.dumps(dimensions, sort_keys=True)})
    if len(rows) != expected_rows:
        raise ColombiaSIVIGILAContractError(
            f"Truncated source page: expected {expected_rows}, got {len(rows)}")
    return rows, {"year": year, "offset": offset, "rows": len(rows),
                  "source_sha256": digest, "source_url": source_url,
                  "source_record_ids_unique": len({row["SourceRecordID"] for row in rows}) == len(rows),
                  "national_aggregate_derived": False}


class ColombiaINSSivigilaCrawler:
    def __init__(self, *, session=None, timeout=90):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "GlobalID/2.0 (official surveillance research)"})
        self.timeout = timeout

    def get(self, url, *, params=None):
        for attempt in range(5):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in {429, 500, 502, 503, 504} or attempt == 4:
                    raise
            except requests.RequestException:
                if attempt == 4:
                    raise
            time.sleep(min(2 ** attempt, 16))
        raise AssertionError("unreachable")
