"""Derive weekly notification-record counts from archived INS individual data.

These are reproducible aggregates of a closed-year release, not an INS-published
national incidence table. Collective forms require their own measure contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import openpyxl

DEFAULT_SCOPE = "co_ins_sivigila_microdatos"
DEFAULT_SOURCE_NAME = "Colombia INS SIVIGILA Annual Microdata"
ONTOLOGY_SOURCE_ID = "SRC_CO_INS_SIVIGILA_MICRODATOS"
LANDING_URL = "https://portalsivigila.ins.gov.co/Paginas/Buscador.aspx"
PARSER_VERSION = "co-ins-individual-microdata-week-v2"
COLLECTIVE_EVENTS = {"349", "995", "998"}
REQUIRED_FIELDS = {"CONSECUTIVE", "COD_EVE", "SEMANA", "ANO", "TIP_CAS",
                   "AJUSTE", "ESTADO_FINAL_DE_CASO", "COD_PAIS_O"}


class MicrodataContractError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def header_name(value) -> str:
    return unicodedata.normalize("NFKD", str(value or "").strip()).encode(
        "ascii", "ignore").decode().upper()


def source_literal(value):
    # INS exports text codes as Excel constant-string formulas (e.g. ="07").
    # Decode only this exact literal syntax; never evaluate spreadsheet formulas.
    if isinstance(value, str):
        match = re.fullmatch(r'="([^"\r\n]*)"', value.strip())
        if match:
            return match[1]
    return value


def integer(value) -> int:
    value = source_literal(value)
    if value is None or isinstance(value, bool):
        raise MicrodataContractError("Missing or invalid integer")
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number != number.to_integral_value():
            raise InvalidOperation
        return int(number)
    except (InvalidOperation, ValueError):
        raise MicrodataContractError("Invalid integer") from None


def source_value(value) -> str:
    value = source_literal(value)
    if value is None or str(value).strip() == "":
        return "__missing__"
    try:
        return str(integer(value))
    except MicrodataContractError:
        return str(value).strip()


def archive_records(root: Path, *, start_year: int, end_year: int,
                    events: set[str] | None = None) -> list[dict]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("status") != "complete" or manifest.get("errors"):
        raise MicrodataContractError("A complete microdata archive manifest is required")
    snapshot = (root / manifest["catalogue_snapshot"]).resolve()
    if not snapshot.is_relative_to(root.resolve()):
        raise MicrodataContractError("Catalogue snapshot is outside the archive")
    evidence = json.loads((snapshot / "manifest.json").read_bytes())
    catalogue = {}
    catalogue_count = 0
    for artifact in evidence["artifacts"]:
        path = (root / artifact["path"]).resolve()
        if (not path.is_relative_to(root.resolve()) or path.stat().st_size != artifact["byte_size"]
                or sha256_file(path) != artifact["sha256"]):
            raise MicrodataContractError("Official microdata catalogue failed integrity verification")
        if path.suffix == ".json":
            for item in json.loads(path.read_bytes())["d"]["results"]:
                catalogue_count += 1
                identity = (integer(item["A_x00f1_o"]), str(integer(item["CodigoEvento"])))
                if not start_year <= identity[0] <= end_year or (events is not None and identity[1] not in events):
                    continue
                if identity in catalogue:
                    raise MicrodataContractError("Duplicate event/year in official catalogue")
                catalogue[identity] = item
    if catalogue_count != evidence["file_count"]:
        raise MicrodataContractError("Official microdata catalogue is incomplete")
    expected = {identity for identity in catalogue if start_year <= identity[0] <= end_year
                and (events is None or identity[1] in events)}
    records = []
    identities = set()
    for record in manifest["files"]:
        if not start_year <= record["year"] <= end_year:
            continue
        if events is not None and record["event_code"] not in events:
            continue
        identity = (record["year"], record["event_code"])
        if identity in identities:
            raise MicrodataContractError("Duplicate event/year workbook in archive")
        identities.add(identity)
        if identity not in expected:
            # The collector retains withdrawn originals for provenance. They
            # must not silently reappear in the latest derived observations.
            continue
        item = catalogue[identity]
        if (record["source_url"] != urljoin("https://portalsivigila.ins.gov.co", item["FileRef"])
                or record["source_modified"] != item["Modified"]
                or record["byte_size"] != int(item["File"]["Length"])):
            raise MicrodataContractError("Workbook metadata differs from current captured catalogue")
        path = (root / record["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise MicrodataContractError("Workbook is missing or outside the archive")
        # Older releases have other filenames; still require the official library.
        url = urlsplit(record["source_url"])
        if (url.scheme != "https" or url.netloc != "portalsivigila.ins.gov.co"
                or not url.path.startswith("/Microdatos/") or url.query or url.fragment
                or "/../" in url.path or not url.path.lower().endswith(".xlsx")):
            raise MicrodataContractError("Workbook URL is not an official microdata original")
        records.append(record)
    if expected - {(r["year"], r["event_code"]) for r in records}:
        raise MicrodataContractError("Missing workbook from the captured official catalogue")
    if not records:
        raise MicrodataContractError("No published microdata workbooks in requested years/events")
    if events is not None and events - {r["event_code"] for r in records}:
        raise MicrodataContractError("Requested event is absent from the archive")
    return sorted(records, key=lambda r: (r["year"], r["event_code"]))


def aggregate_workbook(path: Path, record: dict) -> dict:
    """Read every individual record; withhold the entire workbook on row errors."""
    if path.stat().st_size != record["byte_size"] or sha256_file(path) != record["sha256"]:
        raise MicrodataContractError("Microdata original failed size/SHA-256 verification")
    book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        profiles = []
        candidates = []
        for sheet in book:
            header = next(sheet.iter_rows(max_row=1, values_only=True), ())
            names = [header_name(value) for value in header]
            profiles.append({"sheet": sheet.title, "headers": list(header),
                             "declared_data_rows": max(0, (sheet.max_row or 1) - 1),
                             "schema_sha256": hashlib.sha256(json.dumps(names).encode()).hexdigest()})
            if REQUIRED_FIELDS <= set(names):
                if any(names.count(field) != 1 for field in REQUIRED_FIELDS):
                    raise MicrodataContractError("Duplicated required microdata field")
                candidates.append((sheet, names))
        audit = {"source": DEFAULT_SCOPE, "parser_version": PARSER_VERSION,
                 "year": record["year"], "event_code": record["event_code"],
                 "event_name": record["event_name"], "sha256": record["sha256"],
                 "source_url": record["source_url"], "source_modified": record["source_modified"],
                 "sheets": profiles, "public_release_enabled": False,
                 "official_national_weekly_table": False, "onset_incidence_derived": False}
        if record["event_code"] in COLLECTIVE_EVENTS:
            return {"rows": [], "audit": audit | {"status": "quarantined_collective_form",
                    "reason": "One row is not one case; event-specific aggregate measure review required"}}
        if len(candidates) != 1:
            return {"rows": [], "audit": audit | {"status": "quarantined_schema",
                    "reason": "Exactly one reviewed individual-record sheet required"}}
        sheet, names = candidates[0]
        indices = {field: names.index(field) for field in REQUIRED_FIELDS}
        counts = Counter()
        seen = set()
        errors = Counter()
        scanned = blank = 0
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if all(value is None for value in values):
                blank += 1
                continue
            scanned += 1
            item = {field: values[index] if index < len(values) else None
                    for field, index in indices.items()}
            try:
                year, week = integer(item["ANO"]), integer(item["SEMANA"])
                event = str(integer(item["COD_EVE"]))
                if year != record["year"] or event != record["event_code"]:
                    raise MicrodataContractError("event_or_year_mismatch")
                if not 1 <= week <= 53:
                    raise MicrodataContractError("invalid_source_week")
                identity = source_value(item["CONSECUTIVE"])
                if identity == "__missing__" or identity.startswith("="):
                    raise MicrodataContractError("missing_record_identity")
                if identity in seen:
                    raise MicrodataContractError("duplicate_record_identity")
                seen.add(identity)
                strata = tuple(source_value(item[field]) for field in
                               ("TIP_CAS", "ESTADO_FINAL_DE_CASO", "AJUSTE", "COD_PAIS_O"))
                if any(value.startswith("=") for value in strata):
                    raise MicrodataContractError("formula_in_required_field")
                counts[(week, *strata)] += 1
            except MicrodataContractError as exc:
                errors[str(exc)] += 1
        audit.update(scanned_records=scanned, blank_rows=blank,
                     invalid_records=sum(errors.values()), row_errors=dict(errors),
                     unique_record_ids=len(seen))
        if errors:
            return {"rows": [], "audit": audit | {"status": "quarantined_rows"}}
        rows = []
        for (week, case_type, final_status, adjustment, occurrence_country), count in sorted(counts.items()):
            dims = {"source_epi_year": record["year"], "source_epi_week": week,
                    "source_case_type": case_type, "source_final_case_status": final_status,
                    "source_adjustment": adjustment, "source_occurrence_country": occurrence_country,
                    "source_calendar": "week_boundaries_unverified",
                    "date_role": "synthetic_week_ordinal_anchor_not_event_date",
                    "geographic_basis": "national_reporting_system_including_foreign_and_unknown_occurrence"}
            anchor = date(record["year"], 1, 1) + timedelta(days=7 * (week - 1))
            dimensions = json.dumps(dims, sort_keys=True, ensure_ascii=False)
            rows.append({
                "Date": anchor.isoformat(), "Year": str(record["year"]), "Month": str(anchor.month),
                "SourceDiseaseCode": f"microdatos_{record['event_code']}",
                "DiseaseCode": "__source_native__", "RawDiseaseLabel": record["event_name"],
                "Cases": str(count), "Measure": "derived_notification_record_count", "Unit": "count",
                "Frequency": "weekly", "PeriodType": "source_epi_week",
                "ReportingBasis": "sivigila_closed_year_individual_records",
                "DefinitionVersion": PARSER_VERSION, "ParserVersion": PARSER_VERSION,
                "Geography": "Colombia reporting system", "GeographyKey": "country:CO:national",
                "Geocode": "CO", "LocationType": "national", "ParentCountryCode": "CO",
                "ReportingArea": "Colombia reporting system", "Source": DEFAULT_SOURCE_NAME,
                "SourceURL": record["source_url"], "SourceLandingPageURL": LANDING_URL,
                "RawSHA256": record["sha256"], "SourceModifiedAt": record["source_modified"],
                "SourceRecordID": f"{record['year']}:{record['event_code']}:"
                                  + hashlib.sha256(dimensions.encode()).hexdigest()[:24],
                "Dimensions": dimensions, "ExtractionMethod": "individual_record_weekly_aggregation",
                "DatasetStatus": "closed_year_revisable", "IsProvisional": "false",
                "AuthoritativeRevision": "true", "PublicReleaseEnabled": "false",
                "License": "source_terms_review_pending",
            })
        total = sum(int(row["Cases"]) for row in rows)
        if total != scanned:
            raise MicrodataContractError("Weekly aggregate does not reconcile to workbook records")
        audit.update(status="aggregated_source_records", aggregated_records=total,
                     weekly_strata=len(rows), observed_weeks=sorted({key[0] for key in counts}),
                     national_aggregate_derived=True, zero_filled=False,
                     case_status_filtered=False, cross_event_person_deduplication=False)
        return {"rows": rows, "audit": audit}
    finally:
        book.close()
