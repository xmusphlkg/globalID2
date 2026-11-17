"""INS SIVIGILA 2023 onward, published as a separate official SODA dataset."""
from __future__ import annotations

import json

from src.data.crawlers.co import (
    FIELDS,
    ColombiaINSSivigilaCrawler,
    ColombiaSIVIGILAContractError,
)
from src.data.crawlers.co import (
    parse_page as parse_historical_page,
)

DEFAULT_SCOPE = "co_ins_sivigila_current"
DEFAULT_SOURCE_NAME = "Colombia INS SIVIGILA Current Public Health Surveillance"
ONTOLOGY_SOURCE_ID = "SRC_CO_INS_SIVIGILA_CURRENT"
HISTORY_START_YEAR = 2023
DATASET_ID = "wns8-p38i"
LANDING_URL = f"https://www.datos.gov.co/d/{DATASET_ID}"
METADATA_URL = f"https://www.datos.gov.co/api/views/{DATASET_ID}.json"
RECORD_URL = f"https://www.datos.gov.co/resource/{DATASET_ID}.csv"
COUNT_URL = f"https://www.datos.gov.co/resource/{DATASET_ID}.json"
PARSER_VERSION = "co-ins-sivigila-current-municipality-week-v1"
PUBLISHER = "Instituto Nacional de Salud - INS, Bogotá D.C."


def parse_metadata(payload: bytes) -> dict:
    try:
        document = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ColombiaSIVIGILAContractError("Invalid official current SODA metadata") from exc
    fields = [column.get("fieldName") for column in document.get("columns", [])]
    if document.get("id") != DATASET_ID or not set(FIELDS[:-1]).issubset(fields):
        raise ColombiaSIVIGILAContractError("Wrong current dataset or changed SIVIGILA columns")
    if document.get("attribution") != PUBLISHER:
        raise ColombiaSIVIGILAContractError("Current dataset publisher changed")
    if document.get("licenseId") != "CC_40_BY":
        raise ColombiaSIVIGILAContractError("Current dataset license changed")
    return {
        "dataset_id": DATASET_ID,
        "rows_updated_at": document.get("rowsUpdatedAt"),
        "view_last_modified": document.get("viewLastModified"),
        "license_id": document.get("licenseId"),
        "source_fields": fields,
    }


def parse_page(payload: bytes, *, year: int, offset: int, expected_rows: int,
               source_url: str, dataset_version: dict) -> tuple[list[dict], dict]:
    if year < HISTORY_START_YEAR or dataset_version.get("dataset_id") != DATASET_ID:
        raise ColombiaSIVIGILAContractError("Current SIVIGILA page must be from 2023 onward")
    rows, detail = parse_historical_page(
        payload, year=year, offset=offset, expected_rows=expected_rows,
        source_url=source_url, dataset_version=dataset_version,
    )
    for row in rows:
        row["Source"] = DEFAULT_SOURCE_NAME
        row["SourceLandingPageURL"] = LANDING_URL
        row["DefinitionVersion"] = PARSER_VERSION
        row["License"] = "CC BY 4.0; attribution to INS"
        row["GeographyKey"] = row["GeographyKey"].replace(
            "source:SRC_CO_INS_SIVIGILA:",
            f"source:{ONTOLOGY_SOURCE_ID}:",
            1,
        )
    detail["source_dataset_id"] = DATASET_ID
    return rows, detail


class ColombiaINSSivigilaCurrentCrawler(ColombiaINSSivigilaCrawler):
    pass
