"""Ireland HPSC national weekly notifiable-disease crawler.

The HPSC Notifiable Diseases Hub publishes an ArcGIS FeatureServer table with
one authoritative national ``total`` row per disease and ISO week.  This
adapter consumes that table directly; it deliberately does not reconstruct a
national total from age, sex, or regional rows.

The ArcGIS service is public but its schema is not a versioned API contract.
Every fetch therefore validates the layer metadata, fixed national predicates,
ISO-week fields, non-negative integer counts, and disease/week uniqueness.  A
schema change fails closed instead of silently producing plausible data.

HPSC's current Information Hub terms require prior permission for public
redistribution.  Rows remain available to the internal ingestion workflow, but
the country bootstrap keeps public release disabled until that permission is
recorded explicitly.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode

import requests

from src.core import get_logger

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Ireland HPSC Notifiable Diseases Hub"
DEFAULT_SOURCE_SCOPE = "hpsc_ndh"
DEFAULT_PORTAL_URL = "https://notifiabledisease.hpsc.ie/"
DEFAULT_SERVICE_URL = (
    "https://services3.arcgis.com/dQsP3byyKkTT53Ep/arcgis/rest/services/"
    "IDHUB_AllCasesTS_L/FeatureServer/0"
)
DEFAULT_QUERY_URL = f"{DEFAULT_SERVICE_URL}/query"
DEFAULT_HISTORY_START = (2021, 30)
DEFAULT_REFRESH_RECENT_WEEKS = 12
DEFAULT_QUERY_WEEK_BATCH = 12
OBSERVED_CONTRACT_VERSION = "hpsc-ndh-arcgis-v1-observed-2026-08"

NATIONAL_FILTERS = {
    "location": "Ireland",
    "Cat": "total",
    "Format": "Weekly Number of Cases",
    "SubCat": "Weekly Number of Cases",
    "Include": "Yes",
}
BASE_WHERE = " AND ".join(
    f"{field}='{value}'" for field, value in NATIONAL_FILTERS.items()
)

REQUIRED_FIELDS = {
    "Disease",
    "year",
    "week",
    "YearWeek",
    "location",
    "Cat",
    "Format",
    "SubCat",
    "Value",
    "Unique_ID",
    "Include",
    "ObjectId",
}

CSV_FIELDNAMES = [
    "Date",
    "RawDiseaseLabel",
    "DiseaseCode",
    "Year",
    "Week",
    "YearWeek",
    "Cases",
    "Deaths",
    "ValueStatus",
    "ReportingArea",
    "GeographyKey",
    "Category",
    "Format",
    "SubCategory",
    "Include",
    "DatasetStatus",
    "AuthoritativeRevision",
    "UpdateMode",
    "Source",
    "SourceScope",
    "SourceURL",
    "PortalURL",
    "RetrievedAt",
    "SourceUpdatedAt",
    "SourceContract",
    "ObjectId",
    "UniqueId",
    "RawArtifact",
    "RawSHA256",
    "PublicReleaseEnabled",
    "LicenseReviewStatus",
]


class IEContractError(ValueError):
    """Raised when the observed HPSC ArcGIS contract changes."""


@dataclass(frozen=True, order=True)
class IEWeek:
    """One ISO epidemiological week represented by its Monday."""

    year: int
    week: int
    monday: date

    @classmethod
    def from_parts(cls, year: object, week: object) -> "IEWeek":
        try:
            numeric_year = int(str(year).strip())
            numeric_week = int(str(week).strip())
            monday = date.fromisocalendar(numeric_year, numeric_week, 1)
        except (TypeError, ValueError) as exc:
            raise IEContractError(
                f"Invalid HPSC ISO week: year={year!r} week={week!r}"
            ) from exc
        return cls(numeric_year, numeric_week, monday)

    @classmethod
    def from_year_week(cls, value: object) -> "IEWeek":
        match = re.fullmatch(r"\s*(\d{4})\s+W(\d{1,2})\s*", str(value or ""))
        if match is None:
            raise IEContractError(f"Invalid HPSC YearWeek value: {value!r}")
        return cls.from_parts(match.group(1), match.group(2))

    @property
    def source_label(self) -> str:
        return f"{self.year:04d} W{self.week:02d}"


@dataclass(frozen=True)
class IERawProvenance:
    request_url: str
    retrieved_at: str
    response_sha256: str
    artifact_path: str = ""


@dataclass(frozen=True)
class IEServiceContract:
    max_record_count: int
    source_updated_at: Optional[str]
    payload_sha256: str


@dataclass(frozen=True)
class IEFetchSummary:
    row_count: int
    latest_date: Optional[date]
    weeks_fetched: int
    periods_fetched: Tuple[Tuple[int, int], ...]
    diseases_catalogued: int
    source_url: str
    source_updated_at: Optional[str]
    contract_version: str = OBSERVED_CONTRACT_VERSION


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").replace("\xa0", " ").split()).strip()


def stable_disease_code(label: object) -> str:
    """Derive a stable adapter-local code from the exact upstream label."""

    normalized = unicodedata.normalize("NFKD", _norm_text(label))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    code = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if not code:
        raise IEContractError(f"Cannot derive a disease code from label {label!r}")
    return code


def iter_iso_weeks(start: IEWeek, end: IEWeek) -> List[IEWeek]:
    """Return every ISO week in the inclusive source interval."""

    if start.monday > end.monday:
        raise ValueError("IE week range starts after it ends")
    weeks: List[IEWeek] = []
    current = start.monday
    while current <= end.monday:
        iso = current.isocalendar()
        weeks.append(IEWeek(iso.year, iso.week, current))
        current += timedelta(days=7)
    return weeks


def recent_source_weeks(
    latest: IEWeek, count: int = DEFAULT_REFRESH_RECENT_WEEKS
) -> List[IEWeek]:
    """Return the latest bounded revision window ending at the source maximum."""

    start_monday = latest.monday - timedelta(days=7 * (max(1, int(count)) - 1))
    iso = start_monday.isocalendar()
    return iter_iso_weeks(IEWeek(iso.year, iso.week, start_monday), latest)


def _parse_source_updated_at(raw_milliseconds: object) -> Optional[str]:
    if raw_milliseconds in (None, ""):
        return None
    try:
        milliseconds = int(raw_milliseconds)
    except (TypeError, ValueError) as exc:
        raise IEContractError("HPSC layer dataLastEditDate must be milliseconds") from exc
    if milliseconds <= 0:
        raise IEContractError("HPSC layer dataLastEditDate must be positive")
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def validate_service_metadata(payload: Any) -> IEServiceContract:
    """Validate the ArcGIS table and return paging/update metadata."""

    if not isinstance(payload, dict) or payload.get("error"):
        raise IEContractError("HPSC ArcGIS layer metadata must be a JSON object")
    if _norm_text(payload.get("type")) != "Table":
        raise IEContractError("HPSC ArcGIS source is no longer a table")
    if _norm_text(payload.get("name")) != "IDHUB_AllCasesTS_L":
        raise IEContractError(
            f"Unexpected HPSC ArcGIS table name: {payload.get('name')!r}"
        )

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise IEContractError("HPSC ArcGIS layer metadata has no fields array")
    field_names = {
        _norm_text(field.get("name"))
        for field in raw_fields
        if isinstance(field, dict)
    }
    missing = sorted(REQUIRED_FIELDS - field_names)
    if missing:
        raise IEContractError(
            "HPSC ArcGIS layer is missing required fields: " + ", ".join(missing)
        )

    try:
        max_record_count = int(payload.get("maxRecordCount") or 0)
    except (TypeError, ValueError) as exc:
        raise IEContractError("HPSC maxRecordCount is invalid") from exc
    if max_record_count <= 0:
        raise IEContractError("HPSC maxRecordCount must be positive")

    editing = payload.get("editingInfo")
    source_updated_at = _parse_source_updated_at(
        editing.get("dataLastEditDate") if isinstance(editing, dict) else None
    )
    payload_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return IEServiceContract(
        max_record_count=max_record_count,
        source_updated_at=source_updated_at,
        payload_sha256=payload_sha256,
    )


def validate_disease_catalog(payload: Any) -> Dict[str, str]:
    """Return stable-code to exact-label mapping from the distinct query."""

    if not isinstance(payload, dict) or payload.get("error"):
        raise IEContractError("HPSC disease catalogue response is invalid")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise IEContractError("HPSC disease catalogue is empty")

    catalog: Dict[str, str] = {}
    labels_seen: Set[str] = set()
    for index, feature in enumerate(features):
        attributes = feature.get("attributes") if isinstance(feature, dict) else None
        if not isinstance(attributes, dict):
            raise IEContractError(f"HPSC disease catalogue feature {index} is malformed")
        label = _norm_text(attributes.get("Disease"))
        if not label:
            raise IEContractError(f"HPSC disease catalogue feature {index} has no label")
        normalized_label = label.casefold()
        if normalized_label in labels_seen:
            raise IEContractError(f"Duplicate HPSC disease label: {label!r}")
        labels_seen.add(normalized_label)
        code = stable_disease_code(label)
        if code in catalog:
            raise IEContractError(
                f"HPSC disease labels collide on derived code {code!r}: "
                f"{catalog[code]!r} and {label!r}"
            )
        catalog[code] = label
    return catalog


def _parse_cases(value: object, *, path: str) -> Optional[int]:
    if value is None or _norm_text(value) == "":
        return None
    if isinstance(value, bool):
        raise IEContractError(f"HPSC {path} must be a numeric count")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise IEContractError(f"HPSC {path} must be a numeric count") from exc
    if not numeric.is_integer() or numeric < 0:
        raise IEContractError(f"HPSC {path} must be a non-negative integer")
    return int(numeric)


def validate_feature_page(
    payload: Any,
    *,
    catalog: Mapping[str, str],
    requested_weeks: Set[Tuple[int, int]],
    provenance: IERawProvenance,
    source_updated_at: Optional[str],
) -> List[Dict[str, str]]:
    """Validate and normalize one ArcGIS feature page."""

    if not isinstance(payload, dict) or payload.get("error"):
        raise IEContractError("HPSC weekly query returned an invalid response")
    features = payload.get("features")
    if not isinstance(features, list):
        raise IEContractError("HPSC weekly query has no features array")

    labels_by_folded = {label.casefold(): (code, label) for code, label in catalog.items()}
    rows: List[Dict[str, str]] = []
    seen: Set[Tuple[str, int, int]] = set()
    for index, feature in enumerate(features):
        attributes = feature.get("attributes") if isinstance(feature, dict) else None
        if not isinstance(attributes, dict):
            raise IEContractError(f"HPSC weekly feature {index} is malformed")

        for field, expected in NATIONAL_FILTERS.items():
            actual = _norm_text(attributes.get(field))
            if actual.casefold() != expected.casefold():
                raise IEContractError(
                    f"HPSC weekly feature {index} escaped the national filter: "
                    f"{field}={actual!r}"
                )

        source_week = IEWeek.from_parts(attributes.get("year"), attributes.get("week"))
        year_week = IEWeek.from_year_week(attributes.get("YearWeek"))
        if source_week != year_week:
            raise IEContractError(
                f"HPSC weekly feature {index} has inconsistent week fields"
            )
        if (source_week.year, source_week.week) not in requested_weeks:
            raise IEContractError(
                f"HPSC weekly feature {index} is outside the requested weeks"
            )

        label = _norm_text(attributes.get("Disease"))
        catalog_item = labels_by_folded.get(label.casefold())
        if catalog_item is None:
            raise IEContractError(
                f"HPSC weekly response contains unknown disease label: {label!r}"
            )
        code, canonical_label = catalog_item
        identity = (code, source_week.year, source_week.week)
        if identity in seen:
            raise IEContractError(
                f"Duplicate HPSC national disease/week row: {identity}"
            )
        seen.add(identity)

        cases = _parse_cases(attributes.get("Value"), path=f"features[{index}].Value")
        rows.append(
            {
                "Date": source_week.monday.isoformat(),
                "RawDiseaseLabel": canonical_label,
                "DiseaseCode": code,
                "Year": str(source_week.year),
                "Week": str(source_week.week),
                "YearWeek": source_week.source_label,
                "Cases": "" if cases is None else str(cases),
                "Deaths": "",
                "ValueStatus": (
                    "missing"
                    if cases is None
                    else ("zero" if cases == 0 else "reported")
                ),
                "ReportingArea": "Ireland national",
                "GeographyKey": "country:IE:national",
                "Category": NATIONAL_FILTERS["Cat"],
                "Format": NATIONAL_FILTERS["Format"],
                "SubCategory": NATIONAL_FILTERS["SubCat"],
                "Include": NATIONAL_FILTERS["Include"],
                "DatasetStatus": "provisional_revisable",
                "AuthoritativeRevision": "true",
                "UpdateMode": "authoritative_revision",
                "Source": DEFAULT_SOURCE_NAME,
                "SourceScope": DEFAULT_SOURCE_SCOPE,
                "SourceURL": provenance.request_url,
                "PortalURL": DEFAULT_PORTAL_URL,
                "RetrievedAt": provenance.retrieved_at,
                "SourceUpdatedAt": source_updated_at or "",
                "SourceContract": OBSERVED_CONTRACT_VERSION,
                "ObjectId": _norm_text(attributes.get("ObjectId")),
                "UniqueId": _norm_text(attributes.get("Unique_ID")),
                "RawArtifact": provenance.artifact_path,
                "RawSHA256": provenance.response_sha256,
                "PublicReleaseEnabled": "false",
                "LicenseReviewStatus": "written_permission_required",
            }
        )
    return rows


def validate_national_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    requested_weeks: Optional[Set[Tuple[int, int]]] = None,
) -> None:
    """Validate normalized rows before cache or database use."""

    if not rows:
        raise IEContractError("HPSC national weekly batch contains no rows")
    seen: Set[Tuple[str, int, int]] = set()
    present_weeks: Set[Tuple[int, int]] = set()
    for index, row in enumerate(rows):
        source_week = IEWeek.from_parts(row.get("Year"), row.get("Week"))
        if _norm_text(row.get("Date")) != source_week.monday.isoformat():
            raise IEContractError(f"IE row {index} Date is not its ISO-week Monday")
        code = _norm_text(row.get("DiseaseCode"))
        label = _norm_text(row.get("RawDiseaseLabel"))
        if not code or not label or code != stable_disease_code(label):
            raise IEContractError(f"IE row {index} has an invalid disease identity")
        cases = _norm_text(row.get("Cases"))
        if cases:
            _parse_cases(cases, path=f"rows[{index}].Cases")
        elif _norm_text(row.get("ValueStatus")) != "missing":
            raise IEContractError(f"IE row {index} has an unexplained missing value")
        identity = (code, source_week.year, source_week.week)
        if identity in seen:
            raise IEContractError(f"IE batch contains duplicate row: {identity}")
        seen.add(identity)
        present_weeks.add((source_week.year, source_week.week))

    if requested_weeks:
        missing = sorted(requested_weeks - present_weeks)
        if missing:
            preview = ", ".join(f"{year:04d} W{week:02d}" for year, week in missing[:8])
            raise IEContractError(
                "HPSC batch is missing requested national week(s): " + preview
            )


class IrelandHPSCWeeklyCrawler(BaseCrawler):
    """Fetch HPSC national weekly counts from the ArcGIS FeatureServer."""

    SOURCE_URL = DEFAULT_PORTAL_URL

    def __init__(
        self,
        *,
        service_url: str = DEFAULT_SERVICE_URL,
        portal_url: str = DEFAULT_PORTAL_URL,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        timeout: int = 45,
        max_retries: int = 3,
        delay: float = 0.05,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; IE-HPSC-NDH)",
            timeout=timeout,
            max_retries=max_retries,
            delay=delay,
        )
        self.service_url = service_url.rstrip("/")
        self.query_url = f"{self.service_url}/query"
        self.portal_url = portal_url
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/ie")
        self.session.headers.update(
            {"Accept": "application/json", "Referer": self.portal_url}
        )

    @staticmethod
    def _response_url(
        response: requests.Response,
        url: str,
        params: Sequence[Tuple[str, str]],
    ) -> str:
        response_url = _norm_text(getattr(response, "url", ""))
        if response_url:
            return response_url
        query = urlencode(params, doseq=True)
        return f"{url}?{query}" if query else url

    def _archive_response(
        self,
        *,
        endpoint_name: str,
        request_url: str,
        params: Sequence[Tuple[str, str]],
        response: requests.Response,
        payload: Any,
        retrieved_at: str,
        response_sha256: str,
    ) -> str:
        if not self.save_raw:
            return ""
        folder = self.raw_dir / endpoint_name
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_path = folder / f"response_{stamp}.json"
        envelope = {
            "contract_version": OBSERVED_CONTRACT_VERSION,
            "retrieved_at": retrieved_at,
            "request": {
                "method": "GET",
                "url": request_url,
                "params": [[key, value] for key, value in params],
            },
            "response": {
                "status_code": int(getattr(response, "status_code", 200)),
                "content_type": _norm_text(
                    getattr(response, "headers", {}).get("Content-Type")
                ),
                "sha256": response_sha256,
                "json": payload,
            },
        }
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=folder,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(envelope, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary_path, output_path)
            output_path.chmod(0o644)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return str(output_path)

    def _request_json(
        self,
        url: str,
        *,
        params: Optional[Sequence[Tuple[str, str]]] = None,
        endpoint_name: str,
    ) -> Tuple[Any, IERawProvenance]:
        request_params = list(params or [])
        response = self.get(url, params=request_params)
        content_type = _norm_text(response.headers.get("Content-Type")).casefold()
        if "json" not in content_type:
            raise IEContractError(
                f"HPSC {endpoint_name} returned non-JSON content: {content_type!r}"
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise IEContractError(f"HPSC {endpoint_name} returned invalid JSON") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise IEContractError(
                f"HPSC {endpoint_name} returned ArcGIS error: {payload['error']!r}"
            )
        raw_bytes = getattr(response, "content", b"")
        if not isinstance(raw_bytes, bytes) or not raw_bytes:
            raw_bytes = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        response_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat()
        request_url = self._response_url(response, url, request_params)
        artifact = self._archive_response(
            endpoint_name=endpoint_name,
            request_url=request_url,
            params=request_params,
            response=response,
            payload=payload,
            retrieved_at=retrieved_at,
            response_sha256=response_sha256,
        )
        return payload, IERawProvenance(
            request_url=request_url,
            retrieved_at=retrieved_at,
            response_sha256=response_sha256,
            artifact_path=artifact,
        )

    def fetch_service_contract(self) -> IEServiceContract:
        payload, _ = self._request_json(
            self.service_url,
            params=[("f", "json")],
            endpoint_name="metadata",
        )
        return validate_service_metadata(payload)

    def fetch_disease_catalog(self) -> Dict[str, str]:
        params = [
            ("where", BASE_WHERE),
            ("outFields", "Disease"),
            ("returnGeometry", "false"),
            ("returnDistinctValues", "true"),
            ("orderByFields", "Disease"),
            ("f", "json"),
        ]
        payload, _ = self._request_json(
            self.query_url,
            params=params,
            endpoint_name="disease_catalog",
        )
        return validate_disease_catalog(payload)

    def fetch_source_bounds(self) -> Tuple[IEWeek, IEWeek]:
        statistics = json.dumps(
            [
                {
                    "statisticType": "min",
                    "onStatisticField": "YearWeek",
                    "outStatisticFieldName": "earliest",
                },
                {
                    "statisticType": "max",
                    "onStatisticField": "YearWeek",
                    "outStatisticFieldName": "latest",
                },
            ],
            separators=(",", ":"),
        )
        params = [
            ("where", BASE_WHERE),
            ("outStatistics", statistics),
            ("returnGeometry", "false"),
            ("f", "json"),
        ]
        payload, _ = self._request_json(
            self.query_url,
            params=params,
            endpoint_name="source_bounds",
        )
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list) or len(features) != 1:
            raise IEContractError("HPSC source bounds response is malformed")
        attributes = features[0].get("attributes")
        if not isinstance(attributes, dict):
            raise IEContractError("HPSC source bounds attributes are malformed")
        earliest = IEWeek.from_year_week(attributes.get("earliest"))
        latest = IEWeek.from_year_week(attributes.get("latest"))
        if earliest.monday > latest.monday:
            raise IEContractError("HPSC source bounds are reversed")
        if (earliest.year, earliest.week) < DEFAULT_HISTORY_START:
            raise IEContractError(
                "HPSC source unexpectedly predates the observed 2021 W30 boundary"
            )
        return earliest, latest

    @staticmethod
    def _week_where(periods: Sequence[IEWeek]) -> str:
        by_year: Dict[int, List[int]] = {}
        for period in periods:
            by_year.setdefault(period.year, []).append(period.week)
        clauses = []
        for year, weeks in sorted(by_year.items()):
            values = ",".join(f"'{week}'" for week in sorted(set(weeks)))
            clauses.append(f"(year='{year}' AND week IN ({values}))")
        return f"{BASE_WHERE} AND ({' OR '.join(clauses)})"

    def fetch_week_rows(
        self,
        periods: Sequence[IEWeek],
        *,
        catalog: Mapping[str, str],
        contract: IEServiceContract,
        query_week_batch: int = DEFAULT_QUERY_WEEK_BATCH,
    ) -> List[Dict[str, str]]:
        if not periods:
            return []
        requested = {(period.year, period.week) for period in periods}
        page_size = min(1000, contract.max_record_count)
        rows: List[Dict[str, str]] = []
        batch_size = max(1, min(52, int(query_week_batch)))
        ordered_periods = sorted(set(periods))
        for batch_start in range(0, len(ordered_periods), batch_size):
            batch = ordered_periods[batch_start : batch_start + batch_size]
            batch_requested = {(period.year, period.week) for period in batch}
            offset = 0
            while True:
                params = [
                    ("where", self._week_where(batch)),
                    (
                        "outFields",
                        "Disease,year,week,YearWeek,location,Cat,Format,SubCat,"
                        "Value,Unique_ID,Include,ObjectId",
                    ),
                    ("returnGeometry", "false"),
                    ("orderByFields", "year,week,Disease,ObjectId"),
                    ("resultOffset", str(offset)),
                    ("resultRecordCount", str(page_size)),
                    ("f", "json"),
                ]
                payload, provenance = self._request_json(
                    self.query_url,
                    params=params,
                    endpoint_name="weekly",
                )
                page_rows = validate_feature_page(
                    payload,
                    catalog=catalog,
                    requested_weeks=batch_requested,
                    provenance=provenance,
                    source_updated_at=contract.source_updated_at,
                )
                rows.extend(page_rows)
                features = (
                    payload.get("features") if isinstance(payload, dict) else []
                )
                exceeded = (
                    bool(payload.get("exceededTransferLimit"))
                    if isinstance(payload, dict)
                    else False
                )
                if not exceeded and len(features) < page_size:
                    break
                if not features:
                    raise IEContractError("HPSC pagination did not advance")
                offset += len(features)

        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        validate_national_rows(rows, requested_weeks=requested)
        return rows

    @staticmethod
    def _read_existing_rows(output_csv: Path) -> List[Dict[str, str]]:
        if not output_csv.exists():
            return []
        rows: List[Dict[str, str]] = []
        with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw_row in csv.DictReader(handle):
                rows.append(
                    {name: _norm_text(raw_row.get(name)) for name in CSV_FIELDNAMES}
                )
        return rows

    @staticmethod
    def _write_rows(output_csv: Path, rows: Sequence[Mapping[str, object]]) -> None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=output_csv.parent,
            prefix=f".{output_csv.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {name: row.get(name, "") for name in CSV_FIELDNAMES}
                    )
            os.replace(temporary_path, output_csv)
            output_csv.chmod(0o644)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def crawl_weekly_national(
        self,
        output_csv: Path,
        *,
        weeks: Optional[Iterable[Tuple[int, int]]] = None,
        start_year: int = DEFAULT_HISTORY_START[0],
        refresh_recent_weeks: int = DEFAULT_REFRESH_RECENT_WEEKS,
    ) -> IEFetchSummary:
        """Fetch requested source weeks and merge them into the current CSV."""

        contract = self.fetch_service_contract()
        catalog = self.fetch_disease_catalog()
        source_earliest, source_latest = self.fetch_source_bounds()
        if weeks is None:
            target_periods = recent_source_weeks(source_latest, refresh_recent_weeks)
        else:
            requested: Dict[Tuple[int, int], IEWeek] = {}
            for raw_year, raw_week in weeks:
                period = IEWeek.from_parts(raw_year, raw_week)
                if source_earliest.monday <= period.monday <= source_latest.monday:
                    requested[(period.year, period.week)] = period
            target_periods = sorted(requested.values())

        target_periods = [
            period for period in target_periods if period.year >= int(start_year)
        ]
        if not target_periods:
            raise ValueError("IE HPSC request contains no source-available weeks")

        live_rows = self.fetch_week_rows(
            target_periods,
            catalog=catalog,
            contract=contract,
        )
        target_keys = {(period.year, period.week) for period in target_periods}
        existing_rows = self._read_existing_rows(Path(output_csv))
        preserved: List[Dict[str, str]] = []
        for row in existing_rows:
            try:
                key = (int(row.get("Year", "")), int(row.get("Week", "")))
            except ValueError:
                continue
            if key not in target_keys:
                preserved.append(row)
        combined = preserved + live_rows
        combined.sort(
            key=lambda row: (
                _norm_text(row.get("Date")),
                _norm_text(row.get("RawDiseaseLabel")),
            )
        )
        self._write_rows(Path(output_csv), combined)
        latest = max(
            (date.fromisoformat(row["Date"]) for row in live_rows), default=None
        )
        return IEFetchSummary(
            row_count=len(live_rows),
            latest_date=latest,
            weeks_fetched=len(target_periods),
            periods_fetched=tuple(
                (period.year, period.week) for period in target_periods
            ),
            diseases_catalogued=len(catalog),
            source_url=self.portal_url,
            source_updated_at=contract.source_updated_at,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.pop("output_csv", "data/current/ie/ireland_hpsc_weekly.csv")
        )
        self.crawl_weekly_national(output_csv, **kwargs)
        results: List[CrawlerResult] = []
        for row in self._read_existing_rows(output_csv):
            results.append(
                CrawlerResult(
                    title=f"{row['RawDiseaseLabel']} - {row['YearWeek']}",
                    url=row.get("SourceURL") or self.portal_url,
                    date=datetime.fromisoformat(row["Date"]),
                    metadata={
                        "country_code": "IE",
                        "source": DEFAULT_SOURCE_NAME,
                        "source_scope": DEFAULT_SOURCE_SCOPE,
                        "dataset_status": row.get("DatasetStatus"),
                    },
                    raw_data=dict(row),
                )
            )
        return results

    def parse(self, response: requests.Response) -> List[CrawlerResult]:
        content_type = _norm_text(response.headers.get("Content-Type")).casefold()
        if "json" not in content_type:
            raise IEContractError("HPSC parse response is not JSON")
        payload = response.json()
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            raise IEContractError("HPSC parse response has no features array")
        return [
            CrawlerResult(
                title=_norm_text(feature.get("attributes", {}).get("Disease"))
                or "HPSC weekly row",
                url=getattr(response, "url", self.query_url),
                raw_data=feature.get("attributes", {}),
            )
            for feature in features
            if isinstance(feature, dict)
        ]


__all__ = [
    "BASE_WHERE",
    "CSV_FIELDNAMES",
    "DEFAULT_HISTORY_START",
    "DEFAULT_PORTAL_URL",
    "DEFAULT_QUERY_WEEK_BATCH",
    "DEFAULT_QUERY_URL",
    "DEFAULT_REFRESH_RECENT_WEEKS",
    "DEFAULT_SERVICE_URL",
    "DEFAULT_SOURCE_NAME",
    "DEFAULT_SOURCE_SCOPE",
    "IEContractError",
    "IEFetchSummary",
    "IERawProvenance",
    "IEServiceContract",
    "IEWeek",
    "IrelandHPSCWeeklyCrawler",
    "NATIONAL_FILTERS",
    "OBSERVED_CONTRACT_VERSION",
    "iter_iso_weeks",
    "recent_source_weeks",
    "stable_disease_code",
    "validate_disease_catalog",
    "validate_feature_page",
    "validate_national_rows",
    "validate_service_metadata",
]
