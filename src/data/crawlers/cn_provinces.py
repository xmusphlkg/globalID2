"""Chinese province-level historical and official monthly source adapters.

Province facts deliberately use ``country:CN-XX:national`` geography keys and
never ``country:CN:national``.  Both source feeds are retained independently;
the public projection decides which observation wins for an overlapping month.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.data.crawlers.cn_province_adapters import (
    ProvinceSourceConfig,
    province_adapter_registry,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "cn_province_sources.json"
DATACENTER_SOURCE_ID = "SRC_CN_PROV_DATACENTER"
MONTHLY_REPORT_SOURCE_ID = "SRC_CN_PROV_MONTHLY_REPORT"
SPREADSHEET_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
MONTH_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月"
)
ATTACHMENT_PATTERN = re.compile(
    r"\.(?P<suffix>docx?|xlsx?|pdf)(?![a-z0-9])",
    re.IGNORECASE,
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}


@dataclass(frozen=True)
class MonthlyReportLink:
    jurisdiction_code: str
    report_date: date
    title: str
    url: str


@dataclass(frozen=True)
class ParsedMonthlyReport:
    rows: list[dict[str, object]]
    source_url: str
    artifact_sha256: str
    content_type: str
    retrieved_at: str


@dataclass(frozen=True)
class PHSMHistoryAudit:
    source_rows: int
    imported_rows: int
    duplicate_rows: int
    blank_value_rows: int
    total_rows: int
    unmapped_disease_rows: int
    unmapped_disease_labels: tuple[str, ...]
    unmapped_province_rows: int
    unmapped_province_labels: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "source_rows": self.source_rows,
            "imported_rows": self.imported_rows,
            "duplicate_rows": self.duplicate_rows,
            "blank_value_rows": self.blank_value_rows,
            "total_rows": self.total_rows,
            "unmapped_disease_rows": self.unmapped_disease_rows,
            "unmapped_disease_labels": list(self.unmapped_disease_labels),
            "unmapped_province_rows": self.unmapped_province_rows,
            "unmapped_province_labels": list(self.unmapped_province_labels),
        }


@dataclass(frozen=True)
class PHSMHistoryLoad:
    rows: list[dict[str, object]]
    audit: PHSMHistoryAudit


def _norm(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").replace("\u3000", ""))


def _is_cases_header(value: str) -> bool:
    return "发病数" in value or "病例数" in value or value == "合计"


def _is_report_total_label(value: object) -> bool:
    normalized = _norm(value).casefold()
    if not normalized:
        return False
    if normalized == "total" or "合计" in normalized or "总计" in normalized:
        return True
    return normalized in {"甲类", "乙类", "丙类", "甲乙类", "甲乙丙类", "丙类类计"}


def _has_report_header(values: Sequence[str]) -> bool:
    disease_columns = {
        index
        for index, value in enumerate(values)
        if any(key in value for key in ("病种", "病名", "疾病", "传染病"))
    }
    cases_columns = {
        index for index, value in enumerate(values) if _is_cases_header(value)
    }
    return any(disease != cases for disease in disease_columns for cases in cases_columns)


@lru_cache(maxsize=4)
def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("cn_province_sources.json schema_version must be 1")
    return payload


@lru_cache(maxsize=4)
def province_configs(path: str | Path = DEFAULT_CONFIG) -> dict[str, ProvinceSourceConfig]:
    """Return the per-module province registry.

    ``path`` remains in the signature for compatibility with crawler callers;
    shared history and disease mappings still come from that JSON file, while
    province source ownership now lives in one module per jurisdiction.
    """

    load_config(path)  # Validate the shared source/disease contract.
    return dict(province_adapter_registry())


@lru_cache(maxsize=4)
def _province_alias_index(path: str | Path = DEFAULT_CONFIG) -> dict[str, str]:
    aliases: dict[str, str] = {}
    suffixes = ("省", "市", "壮族自治区", "回族自治区", "维吾尔自治区", "自治区")
    legacy_names = {
        "Beijing": "CN-BJ", "Tianjin": "CN-TJ", "Hebei": "CN-HE",
        "Shanxi": "CN-SX", "InnerMongolia": "CN-NM", "Liaoning": "CN-LN",
        "Jilin": "CN-JL", "Heilongjiang": "CN-HL", "Shanghai": "CN-SH",
        "Jiangsu": "CN-JS", "Zhejiang": "CN-ZJ", "Anhui": "CN-AH",
        "Fujian": "CN-FJ", "Jiangxi": "CN-JX", "Shandong": "CN-SD",
        "Henan": "CN-HA", "Hubei": "CN-HB", "Hunan": "CN-HN",
        "Guangdong": "CN-GD", "Guangxi": "CN-GX", "Hainan": "CN-HI",
        "Chongqing": "CN-CQ", "Sichuan": "CN-SC", "Guizhou": "CN-GZ",
        "Yunnan": "CN-YN", "Tibet": "CN-XZ", "Shaanxi": "CN-SN",
        "Gansu": "CN-GS", "Qinghai": "CN-QH", "Ningxia": "CN-NX",
        "Xinjiang": "CN-XJ",
    }
    aliases.update({_norm(key): value for key, value in legacy_names.items()})
    for code, item in province_configs(path).items():
        aliases[_norm(code)] = code
        aliases[_norm(item.name_en.replace(", China", ""))] = code
        zh = _norm(item.name_zh)
        aliases[zh] = code
        for suffix in suffixes:
            if zh.endswith(suffix):
                aliases[zh[: -len(suffix)]] = code
                break
    return aliases


def resolve_province_code(value: object, path: str | Path = DEFAULT_CONFIG) -> str | None:
    return _province_alias_index(path).get(_norm(value))


def province_geography_key(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not re.fullmatch(r"CN-[A-Z]{2}", normalized):
        raise ValueError(f"Unsupported Chinese province code: {code!r}")
    return f"country:{normalized}:national"


@lru_cache(maxsize=4)
def _disease_index(path: str | Path = DEFAULT_CONFIG) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for disease in load_config(path)["diseases"]:
        for label in disease["labels"]:
            key = _norm(label).casefold()
            previous = result.setdefault(key, disease)
            if previous["code"] != disease["code"]:
                raise ValueError(f"Ambiguous province disease label: {label!r}")
    return result


def resolve_disease(value: object, path: str | Path = DEFAULT_CONFIG) -> dict | None:
    return _disease_index(path).get(_norm(value).casefold())


def _resolve_report_disease(
    value: object,
    path: str | Path = DEFAULT_CONFIG,
) -> dict | None:
    """Resolve deterministic Word-conversion footnotes without broad fuzzy matching."""

    direct = resolve_disease(value, path)
    if direct is not None:
        return direct
    normalized = _norm(value).casefold()
    normalized = re.sub(r"^其中[:：]?", "", normalized)
    normalized = re.sub(r"^[（(]?\d+[）)、.]?", "", normalized)
    normalized = re.sub(r"^[\d*＊]+|[\d*＊]+$", "", normalized)
    direct = _disease_index(path).get(normalized)
    if direct is not None:
        return direct
    candidates = {
        str(disease["code"]): disease
        for label, disease in _disease_index(path).items()
        if len(label) >= 4
        and normalized.startswith(label)
        and len(normalized) - len(label) <= 6
    }
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _normalize_count(value: object) -> int | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    normalized = str(value).strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
        return None
    number = float(normalized)
    if not number.is_integer() or number < 0:
        raise ValueError(f"Province count must be a non-negative integer: {value!r}")
    return int(number)


def _attachment_suffix(url: str) -> str | None:
    """Return an attachment suffix from either the path or a download query."""

    matches = list(ATTACHMENT_PATTERN.finditer(unquote(str(url))))
    if not matches:
        return None
    return f".{matches[-1].group('suffix').lower()}"


def _source_row(
    *,
    report_date: date,
    province_code: str,
    disease: Mapping[str, object],
    raw_label: str,
    cases: int,
    source_id: str,
    source_url: str,
    source_name: str,
    quality_status: str,
    raw: Mapping[str, object],
) -> dict[str, object]:
    province = province_configs()[province_code]
    definition = (
        "CN_PROVINCE_DATACENTER_ONSET_V1"
        if source_id == DATACENTER_SOURCE_ID
        else "CN_PROVINCE_MONTHLY_REPORT_V1"
    )
    return {
        "Date": report_date.isoformat(),
        "RawDiseaseLabel": raw_label,
        "SourceDiseaseCode": str(disease["code"]),
        "DefinitionVersion": definition,
        "Diseases": str(disease["code"]),
        "disease_id": str(disease["concept_id"]),
        "Cases": cases,
        "GeographyKey": province_geography_key(province_code),
        "JurisdictionCode": province_code,
        "ParentCountryCode": "CN",
        "LocationType": "subdivision",
        "Province": province.name_en.replace(", China", ""),
        "ProvinceCN": province.name_zh,
        "ADCode": province.adcode,
        "SourceID": source_id,
        "Source": source_name,
        "SourceURL": source_url,
        "QualityStatus": quality_status,
        "AuthoritativeRevision": source_id == DATACENTER_SOURCE_ID,
        "RetrievedAt": datetime.now(timezone.utc).isoformat(),
        "Raw": dict(raw),
    }


def load_phsm_history(
    workbook: str | Path,
    *,
    include_datacenter: bool = True,
    include_monthly_reports: bool = True,
    config_path: str | Path = DEFAULT_CONFIG,
) -> list[dict[str, object]]:
    """Load the two PHSM workbook sheets without zero-filling absent cells."""

    return load_phsm_history_with_audit(
        workbook,
        include_datacenter=include_datacenter,
        include_monthly_reports=include_monthly_reports,
        config_path=config_path,
    ).rows


def load_phsm_history_with_audit(
    workbook: str | Path,
    *,
    include_datacenter: bool = True,
    include_monthly_reports: bool = True,
    config_path: str | Path = DEFAULT_CONFIG,
    fail_on_unmapped: bool = True,
) -> PHSMHistoryLoad:
    """Load PHSM history and account for every skipped source row.

    National/category totals and blank values are expected exclusions. Unknown
    province or disease labels fail closed by default so newly published labels
    cannot disappear silently during a refresh.
    """

    cfg = load_config(config_path)
    sheets: list[tuple[str, str]] = []
    if include_datacenter:
        sheets.append((cfg["history"]["datacenter_sheet"], DATACENTER_SOURCE_ID))
    if include_monthly_reports:
        sheets.append((cfg["history"]["monthly_report_sheet"], MONTHLY_REPORT_SOURCE_ID))

    rows_by_identity: dict[tuple[str, str, str, str], dict[str, object]] = {}
    source_rows = duplicate_rows = blank_value_rows = total_rows = 0
    unmapped_diseases: list[str] = []
    unmapped_provinces: list[str] = []
    for sheet_name, source_id in sheets:
        frame = pd.read_excel(workbook, sheet_name=sheet_name)
        required = {"year", "month", "disease_cn", "province", "value", "url"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{sheet_name} missing columns: {sorted(missing)}")
        for raw in frame.to_dict(orient="records"):
            source_rows += 1
            province_code = resolve_province_code(raw.get("province"), config_path)
            if province_code is None and _norm(raw.get("province")).casefold() in {
                "total", "全国", "全国合计"
            }:
                total_rows += 1
                continue
            if province_code is None:
                unmapped_provinces.append(str(raw.get("province") or "<blank>"))
                continue
            if _is_report_total_label(raw.get("disease_en")) or _is_report_total_label(
                raw.get("disease_cn")
            ):
                total_rows += 1
                continue
            disease = resolve_disease(raw.get("disease_cn"), config_path)
            if disease is None:
                disease = resolve_disease(raw.get("disease_en"), config_path)
            if disease is None:
                label = raw.get("disease_cn") or raw.get("disease_en") or "<blank>"
                unmapped_diseases.append(str(label))
                continue
            cases = _normalize_count(raw.get("value"))
            if cases is None:
                blank_value_rows += 1
                continue
            report_date = date(int(raw["year"]), int(raw["month"]), 1)
            source_url = str(raw.get("url") or "")
            row = _source_row(
                report_date=report_date,
                province_code=province_code,
                disease=disease,
                raw_label=str(raw.get("disease_cn") or raw.get("disease_en") or ""),
                cases=cases,
                source_id=source_id,
                source_url=source_url,
                source_name=(
                    "China Public Health Science Data Center"
                    if source_id == DATACENTER_SOURCE_ID
                    else "Chinese provincial statutory infectious disease monthly report"
                ),
                quality_status="validated" if source_id == DATACENTER_SOURCE_ID else "raw",
                raw=raw,
            )
            identity = (
                row["Date"], str(disease["code"]), province_code, source_id
            )
            previous = rows_by_identity.get(identity)
            if previous is not None and previous["Cases"] != cases:
                raise ValueError(f"Conflicting PHSM duplicate: {identity}")
            if previous is not None:
                duplicate_rows += 1
            rows_by_identity.setdefault(identity, row)
    rows = [rows_by_identity[key] for key in sorted(rows_by_identity)]
    audit = PHSMHistoryAudit(
        source_rows=source_rows,
        imported_rows=len(rows),
        duplicate_rows=duplicate_rows,
        blank_value_rows=blank_value_rows,
        total_rows=total_rows,
        unmapped_disease_rows=len(unmapped_diseases),
        unmapped_disease_labels=tuple(sorted(set(unmapped_diseases))),
        unmapped_province_rows=len(unmapped_provinces),
        unmapped_province_labels=tuple(sorted(set(unmapped_provinces))),
    )
    if fail_on_unmapped and (unmapped_diseases or unmapped_provinces):
        raise ValueError(
            "PHSM history contains unmapped labels: "
            f"diseases={list(audit.unmapped_disease_labels)!r}, "
            f"provinces={list(audit.unmapped_province_labels)!r}"
        )
    return PHSMHistoryLoad(rows=rows, audit=audit)


class ProvinceDataCenterCrawler:
    """Annual Public Health Science Data Center downloader based on CNIDS."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        config_path: str | Path = DEFAULT_CONFIG,
        timeout: float = 45,
        request_interval: float = 0.2,
        max_workers: int = 1,
        max_retries: int = 4,
    ) -> None:
        if max_workers < 1 or max_workers > 16:
            raise ValueError("max_workers must be between 1 and 16")
        if session is not None and max_workers > 1:
            raise ValueError("A custom session can only be used with max_workers=1")
        if max_retries < 0 or max_retries > 8:
            raise ValueError("max_retries must be between 0 and 8")
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._custom_session = session is not None
        self._thread_local = threading.local()
        self.config_path = Path(config_path)
        self.config = load_config(config_path)["datacenter"]
        self.timeout = timeout
        self.request_interval = request_interval
        self.max_workers = max_workers
        self.max_retries = max_retries

    def available_years(self) -> list[int]:
        """Return catalogued years plus safely parseable unpublished years.

        The Data Center's year selector currently stops at 2020 even though
        the report endpoint serves 2021.  CNIDS trusts the selector verbatim,
        which leaves that year undiscovered.  Probe the bounded set of later
        years and accept one only when the response retains source province
        labels and can be parsed without positional inference.
        """

        response = self._get_with_retry(
            self.config["availability_url"], session=self.session
        )
        catalogued = {int(item["code"]) for item in response.json()}
        if not catalogued:
            raise ValueError("Data Center returned an empty year catalogue")
        probe_id = int(self.config.get("probe_disease_id", 10))
        probe_month = int(self.config.get("probe_month", 1))
        probe_label = self.config.get("disease_id_labels", {}).get(str(probe_id))
        current_year = datetime.now(timezone.utc).year
        for year in range(max(catalogued) + 1, current_year + 1):
            url = self.config["download_url"].format(
                year=year, disease_id=probe_id, month=probe_month
            )
            response = self._get_with_retry(url, session=self.session)
            rows = parse_datacenter_spreadsheet(
                response.content,
                report_date=date(year, probe_month, 1),
                source_url=url,
                config_path=self.config_path,
                fallback_disease_label=probe_label,
            )
            if rows:
                catalogued.add(year)
        return sorted(catalogued)

    def fetch_year(self, year: int) -> list[dict[str, object]]:
        if year not in self.available_years():
            raise ValueError(f"Data Center does not publish year {year}")
        jobs = [
            (disease_id, month)
            for disease_id in self.config["disease_ids"]
            for month in range(1, 13)
        ]
        if self.max_workers == 1:
            batches = (self._fetch_report(year, *job) for job in jobs)
        else:
            executor = ThreadPoolExecutor(max_workers=self.max_workers)
            batches = executor.map(lambda job: self._fetch_report(year, *job), jobs)
        try:
            rows = [row for batch in batches for row in batch]
        finally:
            if self.max_workers > 1:
                executor.shutdown(wait=True, cancel_futures=True)
        return _deduplicate_source_rows(rows)

    def _request_session(self) -> requests.Session:
        if self.max_workers == 1 or self._custom_session:
            return self.session
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(DEFAULT_HEADERS)
            self._thread_local.session = session
        return session

    def _fetch_report(
        self,
        year: int,
        disease_id: int,
        month: int,
    ) -> list[dict[str, object]]:
        url = self.config["download_url"].format(
            year=year, disease_id=disease_id, month=month
        )
        response = self._get_with_retry(url, session=self._request_session())
        if self.request_interval:
            time.sleep(self.request_interval)
        if len(response.content) < 6 * 1024:
            return []
        fallback_label = self.config.get("disease_id_labels", {}).get(
            str(disease_id)
        )
        return parse_datacenter_spreadsheet(
            response.content,
            report_date=date(year, month, 1),
            source_url=url,
            config_path=self.config_path,
            fallback_disease_label=fallback_label,
        )

    def _get_with_retry(
        self,
        url: str,
        *,
        session: requests.Session,
    ) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = session.get(url, timeout=self.timeout)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    return response
                response.raise_for_status()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        if response is None:
            raise RuntimeError(f"No response returned for {url}")
        return response


def _spreadsheet_rows(content: bytes) -> list[dict[int, str]]:
    root = ET.fromstring(content)
    worksheets = list(root.iter(SPREADSHEET_NS + "Worksheet"))
    if not worksheets:
        raise ValueError("Data Center response is not SpreadsheetML")
    result: list[dict[int, str]] = []
    for row in worksheets[0].iter(SPREADSHEET_NS + "Row"):
        values: dict[int, str] = {}
        column = 1
        for cell in row.findall(SPREADSHEET_NS + "Cell"):
            explicit = cell.attrib.get(SPREADSHEET_NS + "Index")
            if explicit:
                column = int(explicit)
            data = cell.find(SPREADSHEET_NS + "Data")
            values[column] = data.text if data is not None and data.text else ""
            column += 1
        result.append(values)
    return result


def parse_datacenter_spreadsheet(
    content: bytes,
    *,
    report_date: date,
    source_url: str,
    config_path: str | Path = DEFAULT_CONFIG,
    fallback_disease_label: str | None = None,
) -> list[dict[str, object]]:
    table = _spreadsheet_rows(content)
    if len(table) < 4:
        return []
    disease_headers = {
        column: label
        for column, value in table[1].items()
        if column >= 3 and (label := _norm(value))
    }
    if not disease_headers and fallback_disease_label:
        case_columns = [
            column
            for column, value in table[2].items()
            if column >= 3 and _is_cases_header(_norm(value))
        ]
        if len(case_columns) != 1:
            raise ValueError(
                "Data Center fallback requires exactly one case column: "
                f"found {case_columns!r} ({source_url})"
            )
        if resolve_disease(fallback_disease_label, config_path) is None:
            raise ValueError(
                "Data Center diseaseId fallback is not registered: "
                f"{fallback_disease_label!r}"
            )
        disease_headers = {case_columns[0]: fallback_disease_label}
    rows: list[dict[str, object]] = []
    for raw in table[3:]:
        province_code = resolve_province_code(raw.get(2), config_path)
        if province_code is None:
            continue
        for column, raw_label in disease_headers.items():
            disease = resolve_disease(raw_label, config_path)
            if disease is None:
                continue
            cases = _normalize_count(raw.get(column))
            if cases is None:
                continue
            rows.append(
                _source_row(
                    report_date=report_date,
                    province_code=province_code,
                    disease=disease,
                    raw_label=raw_label,
                    cases=cases,
                    source_id=DATACENTER_SOURCE_ID,
                    source_url=source_url,
                    source_name="China Public Health Science Data Center",
                    quality_status="validated",
                    raw={"spreadsheet_row": raw, "header_column": column},
                )
            )
    return _deduplicate_source_rows(rows)


class ProvinceMonthlyReportCrawler:
    """Config-driven official-page discovery and table/attachment parser."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        config_path: str | Path = DEFAULT_CONFIG,
        timeout: float = 45,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.config_path = Path(config_path)
        self.timeout = timeout

    def _get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Retry once after a cookie challenge used by some commission sites."""

        response = self.session.get(url, timeout=self.timeout, headers=headers)
        if response.status_code == 403 and response.cookies:
            response = self.session.get(url, timeout=self.timeout, headers=headers)
        response.raise_for_status()
        return response

    def discover(self, jurisdiction_code: str) -> list[MonthlyReportLink]:
        config = province_configs(self.config_path)[jurisdiction_code]
        if not config.index_url:
            return []
        response = self._get(config.index_url)
        soup = BeautifulSoup(response.content, "lxml")
        links: dict[tuple[date, str], MonthlyReportLink] = {}
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            match = MONTH_PATTERN.search(title)
            if not match or "传染病" not in title:
                continue
            report_date = date(int(match["year"]), int(match["month"]), 1)
            url = urljoin(response.url, str(anchor["href"]))
            links[(report_date, url)] = MonthlyReportLink(
                jurisdiction_code, report_date, title, url
            )
        return sorted(links.values(), key=lambda item: (item.report_date, item.url))

    def fetch(self, link: MonthlyReportLink) -> ParsedMonthlyReport:
        config = province_configs(self.config_path)[link.jurisdiction_code]
        response = self._get(
            link.url,
            headers={"Referer": config.index_url} if config.index_url else None,
        )
        retrieved_at = datetime.now(timezone.utc).isoformat()
        page_hash = hashlib.sha256(response.content).hexdigest()
        parser = config.parser
        if parser in {"narrative_only", "discovery_pending"}:
            raise ValueError(
                f"{link.jurisdiction_code} source is {parser}; no detailed table is inferred"
            )
        if parser == "html_table":
            try:
                tables = pd.read_html(io.BytesIO(response.content), flavor="lxml")
                rows = self._normalize_tables(
                    tables,
                    link=link,
                    source_url=response.url,
                    artifact_sha256=page_hash,
                )
            except ValueError:
                pass
            else:
                return ParsedMonthlyReport(
                    rows, response.url, page_hash,
                    response.headers.get("Content-Type", "text/html"), retrieved_at,
                )

        soup = BeautifulSoup(response.content, "lxml")
        candidates: list[tuple[str, str]] = []
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            suffix = _attachment_suffix(href) or _attachment_suffix(
                anchor.get_text(" ", strip=True)
            )
            if suffix is not None:
                candidates.append((urljoin(response.url, href), suffix))
        if not candidates:
            # Some publishers alternate between attachments and an inline
            # table without changing the section or report naming scheme.
            try:
                tables = pd.read_html(io.BytesIO(response.content), flavor="lxml")
            except ValueError:
                tables = []
            rows = self._normalize_tables(
                tables,
                link=link,
                source_url=response.url,
                artifact_sha256=page_hash,
            )
            return ParsedMonthlyReport(
                rows,
                response.url,
                page_hash,
                response.headers.get("Content-Type", "text/html"),
                retrieved_at,
            )
        attachment_url, suffix_hint = candidates[0]
        attachment = self._get(attachment_url, headers={"Referer": response.url})
        artifact_hash = hashlib.sha256(attachment.content).hexdigest()
        tables = parse_attachment_tables(
            attachment.content,
            attachment_url,
            suffix_hint=suffix_hint,
        )
        rows = self._normalize_tables(
            tables, link=link, source_url=attachment_url, artifact_sha256=artifact_hash
        )
        return ParsedMonthlyReport(
            rows, attachment_url, artifact_hash,
            attachment.headers.get("Content-Type", "application/octet-stream"),
            retrieved_at,
        )

    def _normalize_tables(
        self,
        tables: Sequence[pd.DataFrame],
        *,
        link: MonthlyReportLink,
        source_url: str,
        artifact_sha256: str,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        unmapped_labels: list[str] = []
        for table in tables:
            normalized = _normalize_report_table(table)
            for item in normalized:
                disease = _resolve_report_disease(item["label"], self.config_path)
                if disease is None:
                    if not _is_report_total_label(item["label"]):
                        unmapped_labels.append(str(item["label"]))
                    continue
                rows.append(
                    _source_row(
                        report_date=link.report_date,
                        province_code=link.jurisdiction_code,
                        disease=disease,
                        raw_label=item["label"],
                        cases=item["cases"],
                        source_id=MONTHLY_REPORT_SOURCE_ID,
                        source_url=source_url,
                        source_name="Chinese provincial statutory infectious disease monthly report",
                        quality_status="raw",
                        raw={**item, "artifact_sha256": artifact_sha256, "title": link.title},
                    )
                )
        if unmapped_labels:
            raise ValueError(
                "Official province report contains unmapped disease labels: "
                f"{sorted(set(unmapped_labels))!r} ({source_url})"
            )
        result = _deduplicate_source_rows(rows)
        if not result:
            raise ValueError(f"No registered disease rows parsed from {source_url}")
        return result


def _normalize_report_table(table: pd.DataFrame) -> list[dict[str, object]]:
    frame = table.copy()
    # Word-exported HTML often leaves the real header inside the first few
    # body rows (for example a title row followed by 病名/发病数/死亡数).
    existing_columns = [
        "".join(_norm(piece) for piece in column)
        if isinstance(column, tuple)
        else _norm(column)
        for column in frame.columns
    ]
    has_existing_header = _has_report_header(existing_columns)
    if not has_existing_header:
        for position, values in enumerate(frame.head(10).itertuples(index=False, name=None)):
            normalized_values = [_norm(value) for value in values]
            if _has_report_header(normalized_values):
                frame = frame.iloc[position + 1 :].copy()
                frame.columns = normalized_values
                break
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = ["".join(_norm(piece) for piece in column) for column in frame.columns]
    else:
        frame.columns = [_norm(column) for column in frame.columns]
    # Preserve every cell when Word exports duplicate display headers.
    seen_columns: dict[str, int] = {}
    unique_columns: list[str] = []
    for column in frame.columns:
        seen_columns[column] = seen_columns.get(column, 0) + 1
        count = seen_columns[column]
        unique_columns.append(column if count == 1 else f"{column}__{count}")
    frame.columns = unique_columns
    disease_column = next(
        (column for column in frame.columns if any(key in column for key in ("病种", "病名", "疾病", "传染病"))),
        None,
    )
    cases_column = next(
        (column for column in frame.columns if _is_cases_header(column)),
        None,
    )
    if disease_column is None or cases_column is None:
        return []
    result: list[dict[str, object]] = []
    for raw in frame.to_dict(orient="records"):
        label = _norm(raw.get(disease_column))
        raw_cases = _norm(raw.get(cases_column))
        if (
            any(key in label for key in ("病种", "病名", "疾病", "传染病"))
            and _is_cases_header(raw_cases)
        ):
            break
        cases = _normalize_count(raw.get(cases_column))
        if label and cases is not None:
            result.append({"label": label, "cases": cases, "source_row": raw})
    return result


def parse_attachment_tables(
    content: bytes,
    url: str,
    *,
    suffix_hint: str | None = None,
) -> list[pd.DataFrame]:
    suffix = suffix_hint or _attachment_suffix(url)
    if suffix == ".docx":
        return _docx_tables(content)
    if suffix in {".xlsx", ".xls"}:
        return list(pd.read_excel(io.BytesIO(content), sheet_name=None).values())
    if suffix == ".pdf":
        import pdfplumber

        tables: list[pd.DataFrame] = []
        carried_header: list[str] | None = None
        with pdfplumber.open(io.BytesIO(content)) as document:
            for page in document.pages:
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    first_row = [_norm(value) for value in table[0]]
                    has_header = _has_report_header(first_row)
                    if has_header:
                        carried_header = [str(value or "") for value in table[0]]
                        body = table[1:]
                    elif carried_header is not None and len(table[0]) == len(carried_header):
                        body = table
                    else:
                        continue
                    if body:
                        tables.append(pd.DataFrame(body, columns=carried_header))
        return tables
    if suffix == ".doc":
        return _legacy_doc_tables(content)
    raise ValueError(f"Unsupported monthly report attachment: {url}")


def _docx_tables(content: bytes) -> list[pd.DataFrame]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    result: list[pd.DataFrame] = []
    for table in root.findall(".//w:tbl", ns):
        matrix: list[list[str]] = []
        for row in table.findall("./w:tr", ns):
            cells = []
            for cell in row.findall("./w:tc", ns):
                cells.append("".join(node.text or "" for node in cell.findall(".//w:t", ns)))
            matrix.append(cells)
        if len(matrix) > 1:
            width = max(len(row) for row in matrix)
            padded = [row + [""] * (width - len(row)) for row in matrix]
            result.append(pd.DataFrame(padded[1:], columns=padded[0]))
    return result


def _antiword_tables(text: str) -> list[pd.DataFrame]:
    """Recover pipe-delimited disease tables emitted by antiword."""

    matrices: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current.append([cell.strip() for cell in stripped[1:-1].split("|")])
        elif current:
            matrices.append(current)
            current = []
    if current:
        matrices.append(current)

    result: list[pd.DataFrame] = []
    for matrix in matrices:
        header_position = None
        for position, row in enumerate(matrix[:10]):
            values = [_norm(value) for value in row]
            if _has_report_header(values):
                header_position = position
                break
        if header_position is None or header_position + 1 >= len(matrix):
            continue
        header = matrix[header_position]
        width = len(header)
        rows = [row[:width] + [""] * max(0, width - len(row)) for row in matrix[header_position + 1 :]]
        result.append(pd.DataFrame(rows, columns=header))
    return result


def _legacy_doc_tables(content: bytes) -> list[pd.DataFrame]:
    """Convert a legacy Word document in an isolated temporary directory."""

    with tempfile.TemporaryDirectory(prefix="globalid-cn-doc-") as directory:
        root = Path(directory)
        source = root / "input.doc"
        source.write_bytes(content)

        antiword = shutil.which("antiword")
        if antiword:
            converted = subprocess.run(
                [antiword, "-w", "0", str(source)],
                capture_output=True,
                check=False,
                timeout=60,
            )
            if converted.returncode == 0:
                tables = _antiword_tables(converted.stdout.decode("utf-8", errors="replace"))
                parsed_row_count = sum(len(_normalize_report_table(table)) for table in tables)
                if parsed_row_count >= 20:
                    return tables

        office = shutil.which("soffice") or shutil.which("libreoffice")
        if not office:
            raise ValueError(
                "Legacy .doc requires antiword or LibreOffice Writer"
            )
        profile = root / "profile"
        profile.mkdir()
        converted = subprocess.run(
            [
                office,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--norestore",
                "--nodefault",
                "--nofirststartwizard",
                "--convert-to",
                "docx",
                "--outdir",
                str(root),
                str(source),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        target = root / "input.docx"
        if converted.returncode != 0 or not target.exists():
            detail = converted.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Legacy .doc conversion failed: {detail or 'no DOCX output'}")
        return _docx_tables(target.read_bytes())


def _deduplicate_source_rows(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for row in rows:
        identity = (
            str(row["Date"]), str(row["SourceDiseaseCode"]),
            str(row["JurisdictionCode"]), str(row["SourceID"]),
        )
        previous = result.get(identity)
        if previous is not None and previous["Cases"] != row["Cases"]:
            raise ValueError(f"Conflicting province source rows: {identity}")
        result.setdefault(identity, row)
    return [result[key] for key in sorted(result)]


__all__ = [
    "DATACENTER_SOURCE_ID", "MONTHLY_REPORT_SOURCE_ID", "MonthlyReportLink",
    "ParsedMonthlyReport", "ProvinceDataCenterCrawler", "ProvinceMonthlyReportCrawler",
    "ProvinceSourceConfig", "load_config", "load_phsm_history",
    "parse_attachment_tables", "parse_datacenter_spreadsheet", "province_configs",
    "province_geography_key", "resolve_disease", "resolve_province_code",
]
