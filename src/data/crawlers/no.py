"""Norway FHI MSIS national monthly infectious-disease crawler.

The public Allvis/MSIS application is backed by an unversioned JSON API.  This
module deliberately validates the observed response contract before producing
rows so a front-end/API change cannot silently become plausible disease data.

The monthly endpoint aggregates years when a range is requested.  We therefore
request exactly one year at a time and retain the year locally.  For an active
year the endpoint returns zero-filled future months; those placeholders are
always removed.  The current month is also excluded by default because it is
still open, but can be emitted explicitly with ``DataStatus=provisional``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from src.core import get_logger

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Norway FHI MSIS Statistics Bank"
DEFAULT_SOURCE_SCOPE = "fhi_msis"
DEFAULT_PORTAL_URL = "https://allvis.fhi.no/msis/sykdomshendelser"
DEFAULT_API_BASE_URL = "https://allvis.fhi.no/api/msis"
DEFAULT_HISTORY_START_YEAR = 1977
DEFAULT_REFRESH_RECENT_MONTHS = 3
OBSERVED_CONTRACT_VERSION = "fhi-msis-allvis-v1-observed-2026-08"
NORWAY_TIMEZONE = ZoneInfo("Europe/Oslo")

MONTH_NAMES = {
    "januar": 1,
    "februar": 2,
    "mars": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}

CSV_FIELDNAMES = [
    "Date",
    "RawDiseaseLabel",
    "DiseaseCode",
    "DiseaseGroup",
    "Year",
    "Month",
    "Cases",
    "Deaths",
    "ReportingArea",
    "DataStatus",
    "AuthoritativeRevision",
    "UpdateMode",
    "Source",
    "SourceScope",
    "SourceURL",
    "RetrievedAt",
    "SourceContract",
    "RawArtifact",
    "RawSHA256",
]


class NOContractError(ValueError):
    """Raised when the unversioned FHI endpoint drifts from its contract."""


@dataclass(frozen=True)
class NODiagnosis:
    code: str
    name: str
    group_number: int
    group_name: str


@dataclass(frozen=True)
class NORawProvenance:
    request_url: str
    retrieved_at: str
    response_sha256: str
    artifact_path: str = ""


@dataclass(frozen=True)
class NOFetchSummary:
    row_count: int
    latest_date: Optional[date]
    years_fetched: int
    diagnoses_requested: int
    source_url: str
    contract_version: str = OBSERVED_CONTRACT_VERSION


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _as_non_bool_int(value: object, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NOContractError(f"FHI MSIS contract violation at {path}: expected integer")
    return value


def _month_key(day: date) -> Tuple[int, int]:
    return day.year, day.month


def previous_closed_month(as_of: date) -> Tuple[int, int]:
    """Return the year/month immediately before ``as_of``'s open month."""

    if as_of.month == 1:
        return as_of.year - 1, 12
    return as_of.year, as_of.month - 1


def norway_today() -> date:
    """Return the source-local calendar date used for open-month filtering."""

    return datetime.now(NORWAY_TIMEZONE).date()


def effective_target_months(
    *,
    as_of: date,
    include_current_month: bool,
    months: Optional[Iterable[Tuple[int, int]]] = None,
    start_year: int = DEFAULT_HISTORY_START_YEAR,
    end_year: Optional[int] = None,
) -> List[Tuple[int, int]]:
    """Resolve requested months while rejecting open/future placeholders."""

    current = (as_of.year, as_of.month)
    upper = current if include_current_month else previous_closed_month(as_of)

    if months is not None:
        normalized: Set[Tuple[int, int]] = set()
        for raw_year, raw_month in months:
            year = int(raw_year)
            month = int(raw_month)
            if not 1 <= month <= 12:
                raise ValueError(f"Invalid month requested for NO MSIS: {year}-{month}")
            key = (year, month)
            if key <= upper:
                normalized.add(key)
        return sorted(normalized)

    final_year = min(int(end_year or upper[0]), upper[0])
    first_year = int(start_year)
    if first_year > final_year:
        return []

    output: List[Tuple[int, int]] = []
    for year in range(first_year, final_year + 1):
        last_month = 12 if year < upper[0] else upper[1]
        for month in range(1, last_month + 1):
            output.append((year, month))
    return output


def validate_diagnosis_catalog(payload: Any) -> List[NODiagnosis]:
    """Validate and flatten ``kodeverk/diagnoser``."""

    if not isinstance(payload, list) or not payload:
        raise NOContractError("FHI MSIS diagnosis catalog must be a non-empty JSON array")

    diagnoses: List[NODiagnosis] = []
    seen_codes: Set[str] = set()
    seen_names: Set[str] = set()
    for group_index, group in enumerate(payload):
        path = f"diagnoser[{group_index}]"
        if not isinstance(group, dict):
            raise NOContractError(f"FHI MSIS contract violation at {path}: expected object")

        group_number = _as_non_bool_int(group.get("nr"), path=f"{path}.nr")
        group_name = _norm_text(group.get("beskrivelse"))
        if not group_name:
            raise NOContractError(f"FHI MSIS contract violation at {path}.beskrivelse")
        # These fields are part of the currently observed group contract.  They
        # are range labels rather than validity dates, so only their types are
        # asserted here.
        _as_non_bool_int(group.get("id"), path=f"{path}.id")
        for key in ("fra", "til"):
            if not isinstance(group.get(key), str) or not _norm_text(group.get(key)):
                raise NOContractError(f"FHI MSIS contract violation at {path}.{key}")

        raw_diagnoses = group.get("diagnoseListe")
        if not isinstance(raw_diagnoses, list):
            raise NOContractError(
                f"FHI MSIS contract violation at {path}.diagnoseListe: expected array"
            )
        for diagnosis_index, item in enumerate(raw_diagnoses):
            item_path = f"{path}.diagnoseListe[{diagnosis_index}]"
            if not isinstance(item, dict):
                raise NOContractError(
                    f"FHI MSIS contract violation at {item_path}: expected object"
                )
            _as_non_bool_int(item.get("id"), path=f"{item_path}.id")
            code = _norm_text(item.get("verdi"))
            name = _norm_text(item.get("beskrivelse"))
            if not code or not name:
                raise NOContractError(
                    f"FHI MSIS contract violation at {item_path}: empty code/name"
                )
            if code in seen_codes:
                raise NOContractError(f"FHI MSIS duplicate diagnosis code: {code}")
            if name.casefold() in seen_names:
                raise NOContractError(f"FHI MSIS duplicate diagnosis label: {name}")
            seen_codes.add(code)
            seen_names.add(name.casefold())
            diagnoses.append(
                NODiagnosis(
                    code=code,
                    name=name,
                    group_number=group_number,
                    group_name=group_name,
                )
            )

    if not diagnoses:
        raise NOContractError("FHI MSIS diagnosis catalog contains no diagnoses")
    return diagnoses


def validate_monthly_payload(
    payload: Any,
    *,
    selected_diagnoses: Sequence[NODiagnosis],
) -> List[Dict[str, Any]]:
    """Validate one single-year ``etterDiagnoseFordeltPaaMaaned`` response.

    FHI omits a diagnosis entirely when there are no rows for that diagnosis in
    the selected year.  A diagnosis that is present has exactly one row for
    each Norwegian month name, including legitimate closed-month zeroes and
    current-year future placeholders.
    """

    if not isinstance(payload, list):
        raise NOContractError("FHI MSIS monthly response must be a JSON array")

    selected_by_name = {item.name: item for item in selected_diagnoses}
    months_by_label: Dict[str, Set[int]] = {}
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(payload):
        path = f"monthly[{index}]"
        if not isinstance(item, dict):
            raise NOContractError(f"FHI MSIS contract violation at {path}: expected object")
        label = _norm_text(item.get("tekst"))
        month_name = _norm_text(item.get("fordeltPaa"))
        cases = _as_non_bool_int(item.get("antall"), path=f"{path}.antall")
        if not label or label not in selected_by_name:
            raise NOContractError(
                f"FHI MSIS monthly response contains unknown diagnosis label: {label!r}"
            )
        month = MONTH_NAMES.get(month_name.casefold())
        if month is None:
            raise NOContractError(
                f"FHI MSIS monthly response contains unknown month name: {month_name!r}"
            )
        if cases < 0:
            raise NOContractError(
                f"FHI MSIS contract violation at {path}.antall: negative count"
            )
        seen = months_by_label.setdefault(label, set())
        if month in seen:
            raise NOContractError(
                f"FHI MSIS duplicate month for diagnosis {label!r}: {month}"
            )
        seen.add(month)
        normalized.append({"label": label, "month": month, "cases": cases})

    expected_months = set(range(1, 13))
    for label, seen in months_by_label.items():
        if seen != expected_months:
            missing = sorted(expected_months - seen)
            raise NOContractError(
                f"FHI MSIS diagnosis {label!r} did not return all 12 months; "
                f"missing={missing}"
            )
    return normalized


def validate_no_national_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    target_months: Optional[Set[Tuple[int, int]]] = None,
    as_of: Optional[date] = None,
    include_current_month: bool = False,
) -> None:
    """Validate normalized national monthly rows before cache/database use."""

    if not rows:
        raise ValueError("NO FHI MSIS batch contains no normalized monthly rows")

    today = as_of or norway_today()
    current = (today.year, today.month)
    seen: Set[Tuple[str, str]] = set()
    present_months: Set[Tuple[int, int]] = set()
    for index, row in enumerate(rows):
        try:
            report_date = date.fromisoformat(_norm_text(row.get("Date")))
        except ValueError as exc:
            raise ValueError(f"NO row {index} has invalid Date") from exc
        if report_date.day != 1:
            raise ValueError(f"NO row {index} is not at monthly grain")
        key_month = _month_key(report_date)
        if key_month > current:
            raise ValueError(f"NO row {index} contains a future-month placeholder")
        if key_month == current and not include_current_month:
            raise ValueError(f"NO row {index} contains the open current month")

        label = _norm_text(row.get("RawDiseaseLabel") or row.get("Disease"))
        code = _norm_text(row.get("DiseaseCode"))
        raw_cases = _norm_text(row.get("Cases")).replace(",", "")
        if not label or not code:
            raise ValueError(f"NO row {index} has an empty diagnosis label/code")
        try:
            cases = int(raw_cases)
        except ValueError as exc:
            raise ValueError(f"NO row {index} has invalid Cases") from exc
        if cases < 0:
            raise ValueError(f"NO row {index} has negative Cases")

        record_key = (report_date.isoformat(), code)
        if record_key in seen:
            raise ValueError(f"NO batch contains duplicate national row: {record_key}")
        seen.add(record_key)
        present_months.add(key_month)

        status = _norm_text(row.get("DataStatus"))
        expected_status = "provisional" if key_month == current else "closed"
        if status and status != expected_status:
            raise ValueError(
                f"NO row {index} has DataStatus={status!r}; expected {expected_status!r}"
            )

    if target_months:
        eligible_targets = {
            month
            for month in target_months
            if month < current or (include_current_month and month == current)
        }
        missing = sorted(eligible_targets - present_months)
        if missing:
            preview = ", ".join(f"{year:04d}-{month:02d}" for year, month in missing[:8])
            raise ValueError(
                "NO FHI MSIS batch is missing requested national month(s): " + preview
            )


class NorwayMSISCrawler(BaseCrawler):
    """Crawler for FHI's national MSIS monthly statistics endpoint."""

    SOURCE_URL = DEFAULT_PORTAL_URL

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_API_BASE_URL,
        portal_url: str = DEFAULT_PORTAL_URL,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        timeout: int = 45,
        max_retries: int = 3,
        delay: float = 0.1,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; NO-FHI-MSIS)",
            timeout=timeout,
            max_retries=max_retries,
            delay=delay,
        )
        self.api_base_url = api_base_url.rstrip("/")
        self.portal_url = portal_url
        self.diagnoses_url = f"{self.api_base_url}/kodeverk/diagnoser"
        self.min_max_year_url = f"{self.api_base_url}/minMaxAarIntervall"
        self.monthly_url = f"{self.api_base_url}/etterDiagnoseFordeltPaaMaaned"
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/no")
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Referer": self.portal_url,
            }
        )

    @staticmethod
    def _response_url(response: requests.Response, url: str, params: Sequence[Tuple[str, str]]) -> str:
        response_url = _norm_text(getattr(response, "url", ""))
        if response_url:
            return response_url
        query = urlencode(params, doseq=True)
        return f"{url}?{query}" if query else url

    def _archive_response(
        self,
        *,
        endpoint_name: str,
        year: Optional[int],
        request_url: str,
        params: Sequence[Tuple[str, str]],
        response: requests.Response,
        payload: Any,
        retrieved_at: str,
        response_sha256: str,
    ) -> str:
        if not self.save_raw:
            return ""

        folder = self.raw_dir / ("catalog" if year is None else f"monthly/{year}")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_path = folder / f"{endpoint_name}_{stamp}.json"
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
                "content_type": _norm_text(getattr(response, "headers", {}).get("Content-Type")),
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
        year: Optional[int] = None,
    ) -> Tuple[Any, NORawProvenance]:
        request_params = list(params or [])
        response = self.get(url, params=request_params)
        content_type = _norm_text(response.headers.get("Content-Type")).casefold()
        if "json" not in content_type:
            raise NOContractError(
                f"FHI MSIS {endpoint_name} returned non-JSON content type: {content_type!r}"
            )

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise NOContractError(f"FHI MSIS {endpoint_name} returned invalid JSON") from exc

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
            year=year,
            request_url=request_url,
            params=request_params,
            response=response,
            payload=payload,
            retrieved_at=retrieved_at,
            response_sha256=response_sha256,
        )
        return payload, NORawProvenance(
            request_url=request_url,
            retrieved_at=retrieved_at,
            response_sha256=response_sha256,
            artifact_path=artifact,
        )

    def fetch_diagnoses(self) -> List[NODiagnosis]:
        payload, _ = self._request_json(
            self.diagnoses_url,
            endpoint_name="diagnoses",
        )
        return validate_diagnosis_catalog(payload)

    def fetch_source_year_range(self) -> Tuple[int, int]:
        """Return and validate the source-advertised history boundary."""

        payload, _ = self._request_json(
            self.min_max_year_url,
            endpoint_name="min_max_year",
        )
        if not isinstance(payload, dict):
            raise NOContractError("FHI MSIS min/max-year response must be an object")
        minimum = _as_non_bool_int(payload.get("minAar"), path="minMaxAar.minAar")
        maximum = _as_non_bool_int(payload.get("maxAar"), path="minMaxAar.maxAar")
        if minimum > maximum or minimum < 1900 or maximum > 2200:
            raise NOContractError(
                f"FHI MSIS returned an invalid year range: {minimum}..{maximum}"
            )
        return minimum, maximum

    @staticmethod
    def _monthly_params(year: int, diagnosis_codes: Sequence[str]) -> List[Tuple[str, str]]:
        params: List[Tuple[str, str]] = [
            ("fraAar", str(year)),
            ("tilAar", str(year)),
        ]
        params.extend(("diagnoseKodeListe", code) for code in diagnosis_codes)
        params.extend(
            [
                ("summerDiagnose", "false"),
                ("summerAlder", "true"),
                ("summerKjonn", "true"),
                ("summerGeografi", "true"),
                ("summerSmittested", "true"),
                ("summerSmittemaate", "true"),
                ("summerMaaned", "false"),
            ]
        )
        return params

    def fetch_year_rows(
        self,
        year: int,
        *,
        diagnoses: Sequence[NODiagnosis],
        target_months: Set[Tuple[int, int]],
        as_of: date,
        include_current_month: bool,
    ) -> List[Dict[str, str]]:
        """Fetch and normalize one year, preserving the API's national grain."""

        params = self._monthly_params(year, [item.code for item in diagnoses])
        payload, provenance = self._request_json(
            self.monthly_url,
            params=params,
            endpoint_name=f"monthly_{year}",
            year=year,
        )
        normalized = validate_monthly_payload(payload, selected_diagnoses=diagnoses)
        diagnoses_by_name = {item.name: item for item in diagnoses}
        current = (as_of.year, as_of.month)

        rows: List[Dict[str, str]] = []
        for item in normalized:
            month_key = (year, int(item["month"]))
            # Strictly discard future-month zero placeholders by calendar date,
            # not by value: zero is valid in a completed month.
            if month_key > current:
                continue
            if month_key == current and not include_current_month:
                continue
            if month_key not in target_months:
                continue

            diagnosis = diagnoses_by_name[str(item["label"])]
            report_date = date(year, month_key[1], 1)
            rows.append(
                {
                    "Date": report_date.isoformat(),
                    "RawDiseaseLabel": diagnosis.name,
                    "DiseaseCode": diagnosis.code,
                    "DiseaseGroup": diagnosis.group_name,
                    "Year": str(year),
                    "Month": str(month_key[1]),
                    "Cases": str(item["cases"]),
                    "Deaths": "",
                    "ReportingArea": "Norway national",
                    "DataStatus": "provisional" if month_key == current else "closed",
                    "AuthoritativeRevision": "true",
                    "UpdateMode": (
                        "dynamic_provisional"
                        if month_key == current
                        else "authoritative_revision"
                    ),
                    "Source": DEFAULT_SOURCE_NAME,
                    "SourceScope": DEFAULT_SOURCE_SCOPE,
                    "SourceURL": provenance.request_url,
                    "RetrievedAt": provenance.retrieved_at,
                    "SourceContract": OBSERVED_CONTRACT_VERSION,
                    "RawArtifact": provenance.artifact_path,
                    "RawSHA256": provenance.response_sha256,
                }
            )
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        return rows

    @staticmethod
    def _read_existing_rows(output_csv: Path) -> List[Dict[str, str]]:
        if not output_csv.exists():
            return []
        rows: List[Dict[str, str]] = []
        with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw_row in csv.DictReader(handle):
                rows.append({name: _norm_text(raw_row.get(name)) for name in CSV_FIELDNAMES})
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
                    writer.writerow({name: row.get(name, "") for name in CSV_FIELDNAMES})
            os.replace(temporary_path, output_csv)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[Iterable[Tuple[int, int]]] = None,
        start_year: int = DEFAULT_HISTORY_START_YEAR,
        end_year: Optional[int] = None,
        diagnosis_codes: Optional[Iterable[str]] = None,
        include_current_month: bool = False,
        as_of: Optional[date] = None,
    ) -> NOFetchSummary:
        """Fetch requested national months and merge them into ``output_csv``."""

        today = as_of or norway_today()
        target_list = effective_target_months(
            as_of=today,
            include_current_month=include_current_month,
            months=months,
            start_year=start_year,
            end_year=end_year,
        )
        if not target_list:
            raise ValueError("NO FHI MSIS request contains no eligible closed/current months")
        target_months = set(target_list)

        catalog = self.fetch_diagnoses()
        if diagnosis_codes is not None:
            selected_codes = {_norm_text(code) for code in diagnosis_codes if _norm_text(code)}
            known_codes = {item.code for item in catalog}
            unknown = sorted(selected_codes - known_codes)
            if unknown:
                raise ValueError(f"Unknown FHI MSIS diagnosis code(s): {', '.join(unknown)}")
            diagnoses = [item for item in catalog if item.code in selected_codes]
        else:
            diagnoses = catalog
        if not diagnoses:
            raise ValueError("NO FHI MSIS request selected no diagnoses")

        live_rows: List[Dict[str, str]] = []
        years = sorted({year for year, _ in target_months})
        for year in years:
            live_rows.extend(
                self.fetch_year_rows(
                    year,
                    diagnoses=diagnoses,
                    target_months=target_months,
                    as_of=today,
                    include_current_month=include_current_month,
                )
            )

        # A full-catalog fetch should cover every requested month.  Narrow
        # diagnosis extracts may legitimately be empty when the condition had
        # no observations in the requested year, so do not assert coverage for
        # those diagnostic calls.
        if diagnosis_codes is None:
            validate_no_national_rows(
                live_rows,
                target_months=target_months,
                as_of=today,
                include_current_month=include_current_month,
            )
        elif live_rows:
            validate_no_national_rows(
                live_rows,
                as_of=today,
                include_current_month=include_current_month,
            )

        existing_rows = self._read_existing_rows(Path(output_csv))
        preserved: List[Dict[str, str]] = []
        for row in existing_rows:
            try:
                existing_date = date.fromisoformat(_norm_text(row.get("Date")))
            except ValueError:
                continue
            if _month_key(existing_date) not in target_months:
                preserved.append(row)
        combined = preserved + live_rows
        combined.sort(
            key=lambda row: (
                _norm_text(row.get("Date")),
                _norm_text(row.get("RawDiseaseLabel")),
                _norm_text(row.get("DiseaseCode")),
            )
        )
        self._write_rows(Path(output_csv), combined)

        latest = max((date.fromisoformat(row["Date"]) for row in live_rows), default=None)
        return NOFetchSummary(
            row_count=len(live_rows),
            latest_date=latest,
            years_fetched=len(years),
            diagnoses_requested=len(diagnoses),
            source_url=self.portal_url,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        """Return normalized results for callers using the base crawler API."""

        output_csv = Path(kwargs.pop("output_csv", "data/current/no/norway_monthly.csv"))
        self.crawl_monthly_national(output_csv, **kwargs)
        results: List[CrawlerResult] = []
        for row in self._read_existing_rows(output_csv):
            results.append(
                CrawlerResult(
                    title=f"{row['RawDiseaseLabel']} - {row['Date'][:7]}",
                    url=row.get("SourceURL") or self.portal_url,
                    date=datetime.fromisoformat(row["Date"]),
                    year_month=row["Date"][:7],
                    metadata={
                        "country_code": "NO",
                        "source": DEFAULT_SOURCE_NAME,
                        "source_scope": DEFAULT_SOURCE_SCOPE,
                        "data_status": row.get("DataStatus"),
                    },
                    raw_data=dict(row),
                )
            )
        return results

    def parse(self, response: requests.Response) -> List[CrawlerResult]:
        """Validate an individual monthly response for base-class compatibility."""

        content_type = _norm_text(response.headers.get("Content-Type")).casefold()
        if "json" not in content_type:
            raise NOContractError("FHI MSIS parse response is not JSON")
        payload = response.json()
        if not isinstance(payload, list):
            raise NOContractError("FHI MSIS parse response must be an array")
        return [
            CrawlerResult(
                title=_norm_text(item.get("tekst")) or "FHI MSIS monthly row",
                url=getattr(response, "url", self.monthly_url),
                raw_data=item,
            )
            for item in payload
            if isinstance(item, dict)
        ]


__all__ = [
    "CSV_FIELDNAMES",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_HISTORY_START_YEAR",
    "DEFAULT_PORTAL_URL",
    "DEFAULT_REFRESH_RECENT_MONTHS",
    "DEFAULT_SOURCE_NAME",
    "DEFAULT_SOURCE_SCOPE",
    "MONTH_NAMES",
    "NOContractError",
    "NODiagnosis",
    "NOFetchSummary",
    "NORWAY_TIMEZONE",
    "NorwayMSISCrawler",
    "OBSERVED_CONTRACT_VERSION",
    "effective_target_months",
    "norway_today",
    "previous_closed_month",
    "validate_diagnosis_catalog",
    "validate_monthly_payload",
    "validate_no_national_rows",
]
