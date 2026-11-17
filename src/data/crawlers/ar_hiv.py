"""Argentina Ministry of Health's direct national annual HIV notifications.

The reviewed 2014–2023 release supplies its own national, both-sexes totals.
Province and sex strata are retained in the archived original files, never
summed. CSV and XLSX must agree on every row before any total is admitted.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import requests

from src.core.ar_hiv_source import (
    CATALOGUE_ID, CATALOGUE_URL, CSV_RESOURCE_ID, CSV_URL, DATASET_ID,
    DATASET_URL, DEFINITION_VERSION, FREQUENCY, METRIC_TYPE,
    NATIONAL_DIMENSIONS, REPORTING_BASIS, SOURCE_DISEASE_CODE,
    SOURCE_DISEASE_LABEL, SOURCE_ID, SOURCE_NAME, SOURCE_SCOPE,
    XLSX_RESOURCE_ID, XLSX_URL,
)
from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = SOURCE_SCOPE
DEFAULT_SOURCE_NAME = SOURCE_NAME
ONTOLOGY_SOURCE_ID = SOURCE_ID
HISTORY_START_YEAR = 2014
REVIEWED_YEARS = frozenset(range(2014, 2024))
SOURCE_COLUMNS = ("id_sexo", "sexo", "id_jurisdiccion", "jurisdiccion", "anio", "casos_vih")
INTERMEDIATE_PATH = Path(__file__).with_name("certificates") / "sectigo-public-server-authentication-ca-dv-r36.pem"
INTERMEDIATE_SHA256 = "b40358316fd9e1caaaab92c382927d2c13ef51b481eec9371a82e16c86f2a687"
INTERMEDIATE_URL = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"


class ArgentinaHIVContractError(ValueError):
    """The reviewed resource, schema, national definition or evidence changed."""


def discover_resources(payload: bytes) -> tuple[dict, dict]:
    document = json.loads(payload)
    package = document.get("result")
    if document.get("success") is not True or not isinstance(package, dict):
        raise ArgentinaHIVContractError("Expected official CKAN package_show response")
    if package.get("id") != CATALOGUE_ID or package.get("original_identifier") != DATASET_ID:
        raise ArgentinaHIVContractError("Official HIV dataset identity changed")
    resources = {}
    for kind, identity, expected in (("csv", CSV_RESOURCE_ID, CSV_URL), ("xlsx", XLSX_RESOURCE_ID, XLSX_URL)):
        matches = [r for r in package.get("resources", []) if r.get("original_identifier") == identity]
        if len(matches) != 1:
            raise ArgentinaHIVContractError(f"Exactly one reviewed {kind} resource required; new resources need a separate contract")
        item = matches[0]
        url = str(item.get("url", ""))
        # The official harvested catalogue still advertises HTTP. Upgrade only
        # this known official endpoint; do not accept a substituted host/path.
        if url.startswith("http://datos.salud.gob.ar/"):
            url = "https://" + url.removeprefix("http://")
        if url != expected or str(item.get("format", "")).lower() != kind:
            raise ArgentinaHIVContractError(f"Reviewed {kind} download URL/format changed")
        resources[kind] = {
            "id": identity, "catalogue_resource_id": item.get("id"), "url": url,
            "last_modified": item.get("last_modified"),
            "declared_license": item.get("distribution_license"),
        }
    return package, resources


def _string_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ArgentinaHIVContractError("Boolean source cells are not counts or identifiers")
    if isinstance(value, float):
        if not value.is_integer():
            raise ArgentinaHIVContractError("Non-integral source value")
        return str(int(value))
    return str(value).strip()


def _csv_records(payload: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(payload.decode("cp850")), delimiter=";")
    if tuple(reader.fieldnames or ()) != SOURCE_COLUMNS:
        raise ArgentinaHIVContractError("Reviewed HIV CSV columns changed")
    rows = []
    for record in reader:
        if None in record or any(record.get(k) is None for k in SOURCE_COLUMNS):
            raise ArgentinaHIVContractError("Malformed HIV CSV row")
        rows.append({k: _string_cell(record[k]) for k in SOURCE_COLUMNS})
    return rows


def _xlsx_records(payload: bytes) -> list[dict]:
    import openpyxl
    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) != 1:
            raise ArgentinaHIVContractError("Expected one reviewed HIV worksheet")
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        if tuple(next(rows, ())) != SOURCE_COLUMNS:
            raise ArgentinaHIVContractError("Reviewed HIV XLSX columns changed")
        records = []
        for values in rows:
            if len(values) != len(SOURCE_COLUMNS):
                raise ArgentinaHIVContractError("Malformed HIV XLSX row")
            if not any(v is not None for v in values):
                continue
            records.append(dict(zip(SOURCE_COLUMNS, map(_string_cell, values))))
        return records
    finally:
        workbook.close()


def parse_hiv_payload(csv_payload: bytes, xlsx_payload: bytes, *, retrieved_at=None,
                      catalog_sha256="") -> tuple[list[dict], dict]:
    csv_rows, xlsx_rows = _csv_records(csv_payload), _xlsx_records(xlsx_payload)
    if csv_rows != xlsx_rows:
        raise ArgentinaHIVContractError("CSV and XLSX do not agree on every source row")
    if not csv_rows:
        raise ArgentinaHIVContractError("Empty HIV release")
    selected, identities, selected_years = [], set(), set()
    csv_sha = hashlib.sha256(csv_payload).hexdigest()
    xlsx_sha = hashlib.sha256(xlsx_payload).hexdigest()
    for number, raw in enumerate(csv_rows, 2):
        identity = tuple(raw[k] for k in SOURCE_COLUMNS[:-1])
        if identity in identities:
            raise ArgentinaHIVContractError("Duplicate source sex/jurisdiction/year identity")
        identities.add(identity)
        if not re.fullmatch(r"\d{4}", raw["anio"]) or int(raw["anio"]) not in REVIEWED_YEARS:
            raise ArgentinaHIVContractError("Source year is outside the reviewed 2014–2023 release")
        national = raw["id_jurisdiccion"] == "ARG" or raw["jurisdiccion"] == "200"
        if national and (raw["id_jurisdiccion"], raw["jurisdiccion"]) != ("ARG", "200"):
            raise ArgentinaHIVContractError("National jurisdiction label/code conflict")
        if (raw["id_sexo"] == "3") != (raw["sexo"] == "Ambos sexos"):
            raise ArgentinaHIVContractError("Both-sexes label/code conflict")
        value = raw["casos_vih"]
        if value and not re.fullmatch(r"\d+", value):
            raise ArgentinaHIVContractError("Reported HIV count must be a nonnegative integer or blank")
        if not national or raw["id_sexo"] != "3":
            continue
        year = int(raw["anio"])
        if year in selected_years:
            raise ArgentinaHIVContractError("Duplicate national annual HIV total")
        selected_years.add(year)
        selected.append({
            "Date": f"{year}-01-01", "Year": str(year), "Month": "1",
            "DiseaseCode": "__source_native__", "SourceDiseaseCode": SOURCE_DISEASE_CODE,
            "RawDiseaseLabel": SOURCE_DISEASE_LABEL, "Cases": value,
            "MissingValueStatus": "" if value else "source_missing",
            "Measure": METRIC_TYPE, "ReportingBasis": REPORTING_BASIS,
            "Frequency": FREQUENCY, "PeriodType": "annual",
            "PeriodStart": f"{year}-01-01", "PeriodEnd": f"{year}-12-31",
            "DefinitionVersion": DEFINITION_VERSION,
            "Geography": "Argentina", "GeographyKey": "country:AR:national",
            "Source": SOURCE_NAME, "SourceURL": DATASET_URL, "DownloadURL": CSV_URL,
            "SourceResourceID": CSV_RESOURCE_ID, "SourceRowNumber": str(number),
            "SourceRawRecord": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "RawSHA256": csv_sha, "CrosscheckXLSXSHA256": xlsx_sha,
            "CatalogSHA256": catalog_sha256,
            "RetrievedAt": str(retrieved_at or datetime.now(timezone.utc).isoformat()),
            "DatasetStatus": "closed_revisable", "IsProvisional": "false",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "License": "CC-BY-4.0", "PublicReleaseEnabled": "false",
            "Dimensions": json.dumps(dict(NATIONAL_DIMENSIONS), sort_keys=True),
        })
    if selected_years != REVIEWED_YEARS:
        raise ArgentinaHIVContractError("Exactly one explicit national both-sexes total per reviewed year required")
    audit = {
        "source_rows": len(csv_rows), "selected_national_rows": len(selected),
        "csv_xlsx_all_rows_equal": True, "csv_sha256": csv_sha, "xlsx_sha256": xlsx_sha,
        "catalog_sha256": catalog_sha256, "first_year": min(selected_years),
        "last_year": max(selected_years), "selection": dict(NATIONAL_DIMENSIONS),
        "aggregation": "none_source_reported_national_total", "source_encoding": "cp850",
        "coverage": "fixed reviewed 2014–2023 release; does not establish current surveillance coverage",
    }
    return sorted(selected, key=lambda row: row["Date"]), audit


@contextmanager
def verified_health_ca_bundle():
    """Complete the server's chain without disabling root/hostname validation.

    The added certificate is the signed intermediate omitted by the host,
    not a new trust root. The request continues to require an existing CA root.
    See docs/data-sources/AR_ALTERNATIVE_SOURCES_20261116.md for verification.
    """
    intermediate = INTERMEDIATE_PATH.read_bytes()
    if hashlib.sha256(intermediate).hexdigest() != INTERMEDIATE_SHA256:
        raise ArgentinaHIVContractError("Bundled intermediate certificate changed")
    with tempfile.TemporaryDirectory(prefix="globalid-ar-hiv-ca-") as directory:
        path = Path(directory) / "verified-chain.pem"
        path.write_bytes(Path(requests.certs.where()).read_bytes() + b"\n" + intermediate)
        yield path


class ArgentinaHIVAnnualCrawler(BaseCrawler):
    def __init__(self, **kwargs):
        super().__init__(timeout=90, delay=0.2)

    def get_health_resource(self, url: str):
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "datos.salud.gob.ar":
            raise ArgentinaHIVContractError("Verified health transport is restricted to the official HTTPS host")
        with verified_health_ca_bundle() as bundle:
            response = self.get(url, verify=str(bundle), allow_redirects=False)
        if response.status_code != 200 or response.url != url:
            raise ArgentinaHIVContractError("Unexpected health-resource status or redirect")
        return response

    def fetch_rows(self):
        catalogue = self.get(CATALOGUE_URL)
        _, resources = discover_resources(catalogue.content)
        csv_response = self.get_health_resource(resources["csv"]["url"])
        xlsx_response = self.get_health_resource(resources["xlsx"]["url"])
        rows, self.last_audit = parse_hiv_payload(csv_response.content, xlsx_response.content,
                                                catalog_sha256=hashlib.sha256(catalogue.content).hexdigest())
        return rows

    async def crawl(self, **kwargs):
        import asyncio
        rows = await asyncio.to_thread(self.fetch_rows)
        return [CrawlerResult(title=SOURCE_NAME, url=DATASET_URL, raw_data={"rows": rows})]

    def parse(self, response):
        raise ArgentinaHIVContractError("HIV CSV parsing requires the independently captured XLSX crosscheck")
