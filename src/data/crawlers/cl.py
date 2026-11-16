"""Chile MINSAL's public, validated ENO notification archive."""
from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .base import BaseCrawler, CrawlerResult

DEFAULT_SCOPE = "cl_eno_historical"
DEFAULT_SOURCE_NAME = "Chile MINSAL ENO Historical Notifications"
ONTOLOGY_SOURCE_ID = "SRC_CL_ENO"
CATALOG_URL = "https://datos.gob.cl/api/3/action/package_show?id=notificaciones-eno"
LANDING_URL = "https://datos.gob.cl/dataset/notificaciones-eno"
HISTORY_START_YEAR = 2007
REQUIRED_COLUMNS = {"etapa_clinica", "anho_notificacion", "ENO", "region", "cie_10_diagnostico", "diagnostico"}
RESOURCE_NAMES = {
    "csv": "Bases ENO",
    "xlsx": "Diccionario de datos ENO",
    "pdf": "Metodologia validacion",
}


class ChileENOContractError(ValueError):
    pass


class ChileENOTransportCache:
    """Integrity-checked source bytes, with bounded conditional revalidation."""

    def __init__(self, directory=Path("data/cache/cl/eno_csv")):
        self.directory = Path(directory)

    def get(self, crawler, url: str, *, ttl_hours=24, force_refresh=False, revision_marker=None):
        self.directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        receipt_path = self.directory / f"{key}.json"
        lock_path = self.directory / f"{key}.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
                payload = None
                if previous is not None:
                    if previous.get("url") != url:
                        raise ChileENOContractError("CSV cache URL mismatch")
                    cached_path = self.directory / f"{previous['sha256']}.csv"
                    if not cached_path.exists() or not cached_path.resolve().is_relative_to(self.directory.resolve()):
                        raise ChileENOContractError("CSV cache object missing or outside cache")
                    payload = cached_path.read_bytes()
                    if len(payload) != previous["byte_size"] or hashlib.sha256(payload).hexdigest() != previous["sha256"]:
                        raise ChileENOContractError("CSV cache integrity mismatch")
                    last_checked = datetime.fromisoformat(previous["revalidated_at"])
                    if (not force_refresh and previous.get("revision_marker") == revision_marker
                            and datetime.now(timezone.utc) - last_checked < timedelta(hours=max(0, float(ttl_hours)))):
                        return payload, previous | {"cache_status": "hit_verified"}
                headers = {}
                if previous is not None:
                    if previous.get("etag"):
                        headers["If-None-Match"] = previous["etag"]
                    if previous.get("last_modified"):
                        headers["If-Modified-Since"] = previous["last_modified"]
                response = crawler.get(url, headers=headers)
                now = datetime.now(timezone.utc).isoformat()
                if response.status_code == 304:
                    if payload is None:
                        raise ChileENOContractError("Source returned 304 without cached original")
                    current = previous | {"revalidated_at": now, "revision_marker": revision_marker}
                    status = "revalidated_not_modified"
                else:
                    payload = response.content
                    if len(payload) < 100 or b";" not in payload[:4096]:
                        raise ChileENOContractError("Official CSV download is not an ENO table")
                    digest = hashlib.sha256(payload).hexdigest()
                    destination = self.directory / f"{digest}.csv"
                    if destination.exists():
                        if destination.read_bytes() != payload:
                            raise ChileENOContractError("Existing content-addressed CSV differs")
                    else:
                        self._atomic_write(destination, payload)
                    current = {"url": url, "sha256": digest, "byte_size": len(payload),
                               "retrieved_at": now, "revalidated_at": now,
                               "etag": response.headers.get("ETag"),
                               "last_modified": response.headers.get("Last-Modified"),
                               "revision_marker": revision_marker}
                    status = "refetched" if previous is not None else "downloaded"
                self._atomic_write(receipt_path, json.dumps(current, sort_keys=True).encode())
                return payload, current | {"cache_status": status}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _atomic_write(self, destination: Path, payload: bytes):
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        temporary.replace(destination)


def discover_resources(payload: bytes) -> tuple[dict, dict[str, dict]]:
    try:
        envelope = json.loads(payload)
        package = envelope["result"]
        if envelope["success"] is not True or package["name"] != "notificaciones-eno":
            raise KeyError("package identity")
    except (KeyError, TypeError, ValueError) as exc:
        raise ChileENOContractError("Official CKAN package contract changed") from exc
    if package.get("organization", {}).get("title") != "Ministerio de Salud":
        raise ChileENOContractError("CKAN package is not attributed to MINSAL")
    resources: dict[str, dict] = {}
    for item in package.get("resources", []):
        kind = str(item.get("format") or "").lower()
        if kind not in RESOURCE_NAMES or RESOURCE_NAMES[kind].casefold() not in str(item.get("name") or "").casefold():
            continue
        url = str(item.get("url") or "")
        if urlparse(url).scheme != "https" or urlparse(url).hostname != "datos.gob.cl":
            raise ChileENOContractError("CKAN resource left the official host")
        if kind in resources:
            raise ChileENOContractError(f"Duplicate official {kind} resource")
        resources[kind] = {"id": item.get("id"), "name": item["name"], "url": url,
                           "last_modified": item.get("last_modified"), "size": item.get("size")}
    if set(resources) != set(RESOURCE_NAMES):
        raise ChileENOContractError(f"Official ENO CSV, dictionary and methodology required: {sorted(resources)}")
    return package, resources


def _source_code(label: str, classification: str) -> str:
    folded = unicodedata.normalize("NFKD", label)
    slug = re.sub(r"[^a-z0-9]+", "_", "".join(c for c in folded if not unicodedata.combining(c)).lower()).strip("_")
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    return f"eno_{slug[:48]}_{digest}__{classification.lower()}"


def parse_eno_csv(payload: bytes, *, source_url: str, catalog_modified: str | None = None,
                  retrieved_at: str | None = None, start_year: int = HISTORY_START_YEAR,
                  end_year: int | None = None) -> tuple[list[dict], dict]:
    if len(payload) < 100 or b";" not in payload[:4096]:
        raise ChileENOContractError("ENO CSV response is empty or not semicolon separated")
    digest = hashlib.sha256(payload).hexdigest()
    try:
        stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8-sig", newline="")
        reader = csv.DictReader(stream, delimiter=";", strict=True)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ChileENOContractError("Required official ENO columns changed")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ChileENOContractError("Duplicate ENO CSV columns")
        counts: Counter[tuple[int, str, str]] = Counter()
        years: Counter[int] = Counter()
        classifications: Counter[str] = Counter()
        labels: set[str] = set()
        total = 0
        for number, record in enumerate(reader, start=2):
            if None in record or any(value is None for value in record.values()):
                raise ChileENOContractError(f"Malformed ENO CSV row {number}")
            try:
                year = int(record["anho_notificacion"])
            except ValueError as exc:
                raise ChileENOContractError(f"Invalid notification year on row {number}") from exc
            if year < HISTORY_START_YEAR or year > datetime.now(timezone.utc).year:
                raise ChileENOContractError(f"Notification year outside source contract on row {number}")
            label = record["ENO"].strip()
            classification = record["etapa_clinica"].strip().upper()
            if not label or classification not in {"CONFIRMADA", "PROBABLE"}:
                raise ChileENOContractError(f"Unknown disease or case classification on row {number}")
            total += 1
            years[year] += 1
            labels.add(label)
            classifications[classification] += 1
            if year >= start_year and (end_year is None or year <= end_year):
                counts[(year, label, classification)] += 1
    except UnicodeError as exc:
        raise ChileENOContractError("ENO CSV encoding is not UTF-8") from exc
    except csv.Error as exc:
        raise ChileENOContractError("ENO CSV structure is invalid") from exc
    if not total or not counts:
        raise ChileENOContractError("No validated ENO notifications in requested source window")
    if max(years) < max(HISTORY_START_YEAR, start_year):
        raise ChileENOContractError("Requested years are absent from source CSV")
    rows: list[dict] = []
    for (year, label, classification), value in sorted(counts.items()):
        definition = "filemaker_2007_2018" if year <= 2018 else "epivigila_2019_onward"
        dimensions = {"source_eno": label, "source_case_classification": classification,
                      "source_notification_year": year, "source_system_period": definition,
                      "note": "Annual validated notifications; source may include infections acquired outside Chile"}
        rows.append({"Date": date(year, 12, 31).isoformat(), "Year": str(year), "Month": "12",
                     "PeriodStart": date(year, 1, 1).isoformat(), "PeriodEnd": date(year, 12, 31).isoformat(),
                     "DiseaseCode": "__source_native__", "SourceDiseaseCode": _source_code(label, classification),
                     "RawDiseaseLabel": label, "Cases": str(value), "MissingValueStatus": "",
                     "Measure": "confirmed_case_notifications" if classification == "CONFIRMADA" else "probable_case_notifications",
                     "Unit": "count", "Frequency": "annual", "PeriodType": "annual",
                     "ReportingBasis": "validated_notification_by_notification_year",
                     "DefinitionVersion": f"cl_eno_{definition}_{classification.lower()}_v1",
                     "Geography": "Chile", "GeographyKey": "country:CL:national",
                     "Source": DEFAULT_SOURCE_NAME, "SourceURL": source_url,
                     "SourceLandingPageURL": LANDING_URL, "RawSHA256": digest,
                     "RetrievedAt": retrieved_at or datetime.now(timezone.utc).isoformat(),
                     "DatasetStatus": "published_revisable", "IsProvisional": str(year >= 2019).lower(),
                     "RevisionSemantics": "full_csv_republished; deletions_not_authorized",
                     "AuthoritativeRevision": "false", "PublicReleaseEnabled": "false",
                     "License": "CC0-1.0 (catalog metadata)",
                     "Dimensions": json.dumps(dimensions, ensure_ascii=False, sort_keys=True)})
    audit = {"status": "captured_full_csv_aggregated_nationally", "source_case_rows": total,
             "observations": len(rows), "years_in_csv": dict(sorted(years.items())),
             "classifications": dict(classifications), "source_eno_categories": len(labels),
             "selected_years": sorted({year for year, _, _ in counts}),
             "catalog_year_claim": "2007-2022", "catalog_year_mismatch": max(years) > 2022,
             "catalog_category_claim_outdated": len(labels) > 33,
             "raw_sha256": digest, "raw_byte_size": len(payload),
             "public_release_enabled": False, "national_geography_basis": "notification filed with Chilean surveillance system",
             "absent_disease_years": "unknown, never synthesized as zero"}
    return rows, audit


class ChileENOCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=240, delay=0.1)

    async def crawl(self, **kwargs):
        import asyncio
        package = await asyncio.to_thread(self.get, CATALOG_URL)
        _, resources = discover_resources(package.content)
        response = await asyncio.to_thread(self.get, resources["csv"]["url"])
        rows, audit = parse_eno_csv(response.content, source_url=resources["csv"]["url"])
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": rows, "audit": audit})]

    def parse(self, response):
        rows, audit = parse_eno_csv(response.content, source_url=response.url)
        return [CrawlerResult(title=DEFAULT_SOURCE_NAME, url=LANDING_URL, raw_data={"rows": rows, "audit": audit})]
