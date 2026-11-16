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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.data.crawlers.cn_province_adapters import (
    ProvinceSourceConfig,
    province_adapter_registry,
)

from src.data.crawlers.cn_province_adapters.parsing import normalization as _province_normalization
from src.data.crawlers.cn_province_adapters.parsing import phsm_history as _phsm_history
from src.data.crawlers.cn_province_adapters.inner_mongolia import parse_named_monthly_deaths
from src.services.exceptions import TaskCancelledError
from src.data.crawlers.cn_province_adapters.parsing.normalization import (
    _norm, _is_cases_header, _is_report_total_label, _has_report_header,
    _normalize_count, _normalize_report_table,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "cn_province_sources.json"
DATACENTER_SOURCE_ID = "SRC_CN_PROV_DATACENTER"
MONTHLY_REPORT_SOURCE_ID = "SRC_CN_PROV_MONTHLY_REPORT"
MAX_REPORT_IMAGES = 12
MAX_REPORT_IMAGE_BYTES = 20 * 1024 * 1024
SPREADSHEET_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
MONTH_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月"
)
ATTACHMENT_PATTERN = re.compile(
    r"\.(?P<suffix>docx?|xlsx?|pdf|wps)(?![a-z0-9])",
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
    # Raw originals are transient; the ingestion recorder archives their HTTP
    # responses. Candidates are never formal source rows before visual review.
    image_documents: list[dict] = field(default_factory=list)
    article_text: str = ""


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
    # Audited legacy Word revision artifacts, including Hunan July 2026
    # (SHA256 c2d0353da61690e80479c597a60324aac6c7c5b5ed77d977c6b473574e489407).
    # Both antiword and LibreOffice retain these duplicated fragments. Keep
    # exact exceptions: suffix/fuzzy rules could swallow case definitions.
    normalized = {
        "其他感染性腹泻病他染": "其他感染性腹泻病",
        "流行性出血热出血热": "流行性出血热",
        "伤寒和副伤寒和副寒": "伤寒和副伤寒",
        # Jiangsu's September 2023 table uses # for its printed note that
        # mpox became a class B notifiable disease on September 20.
        "猴痘#": "猴痘",
    }.get(normalized, normalized)
    direct = _disease_index(path).get(normalized)
    if direct is not None:
        return direct
    # Only strip explicit footnote syntax. Arbitrary trailing characters can
    # change a measure/definition (死亡、疑似、累计), even after a known name.
    footnote = re.sub(r"(?:[①-⑳†‡※]+|[（(][\d*＊①-⑳]+[）)])$", "", normalized)
    return _disease_index(path).get(footnote)


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
    cases: int | None,
    source_id: str,
    source_url: str,
    source_name: str,
    quality_status: str,
    raw: Mapping[str, object],
    deaths: int | None = None,
) -> dict[str, object]:
    province = province_configs()[province_code]
    definition = (
        "CN_PROVINCE_DATACENTER_ONSET_V1"
        if source_id == DATACENTER_SOURCE_ID
        else "CN_PROVINCE_MONTHLY_REPORT_V1"
    )
    definition = str(disease.get("definition_version") or definition)
    result = {
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
    if deaths is not None:
        result["Deaths"] = deaths
    return result


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


def load_phsm_history_with_audit(workbook: str | Path, *, include_datacenter: bool = True,
                                 include_monthly_reports: bool = True,
                                 config_path: str | Path = DEFAULT_CONFIG,
                                 fail_on_unmapped: bool = True) -> PHSMHistoryLoad:
    return _phsm_history.load_phsm_history_with_audit(workbook,
        include_datacenter=include_datacenter, include_monthly_reports=include_monthly_reports,
        config_path=config_path, fail_on_unmapped=fail_on_unmapped, load_config=load_config,
        resolve_province_code=resolve_province_code, resolve_disease=resolve_disease,
        source_row=_source_row, audit_type=PHSMHistoryAudit, load_type=PHSMHistoryLoad,
        datacenter_source_id=DATACENTER_SOURCE_ID, monthly_source_id=MONTHLY_REPORT_SOURCE_ID)


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
        self.response_diagnostics: list[dict[str, object]] = []

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
        fallback_label = self.config.get("disease_id_labels", {}).get(
            str(disease_id)
        )
        diagnostic = {"year": year, "month": month, "disease_id": disease_id,
                      "url": response.url, "bytes": len(response.content)}
        try:
            rows = parse_datacenter_spreadsheet(
                response.content, report_date=date(year, month, 1),
                source_url=response.url, config_path=self.config_path,
                fallback_disease_label=fallback_label,
            )
        except (ValueError, ET.ParseError) as exc:
            self.response_diagnostics.append({**diagnostic, "status": "invalid_response", "error": str(exc)})
            raise ValueError(f"Data Center invalid response for {year}-{month:02d}, diseaseId={disease_id}: {exc}") from exc
        self.response_diagnostics.append({**diagnostic, "status": "parsed" if rows else "empty_or_unlabelled", "rows": len(rows)})
        return rows

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
    if len(table) <= 4:
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
    if not disease_headers and any(
        resolve_province_code(raw.get(2), config_path) is not None
        and any(_normalize_count(value) is not None for column, value in raw.items() if column >= 3)
        for raw in table[3:]
    ):
        raise ValueError(f"Data Center numeric province rows have no disease label or registered fallback ({source_url})")
    unknown = sorted({label for label in disease_headers.values() if resolve_disease(label, config_path) is None})
    if unknown:
        raise ValueError(f"Data Center contains unmapped disease labels: {unknown!r} ({source_url})")
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
        self.discovery_diagnostics: list[dict[str, object]] = []
        self.report_diagnostics: list[dict[str, object]] = []

    def _get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Retry once after a cookie challenge used by some commission sites."""

        for attempt in range(2):
            try:
                response = (self.session.post(url, data=data, timeout=self.timeout, headers=headers)
                            if data is not None else self.session.get(url, timeout=self.timeout, headers=headers))
                if response.status_code in {429, 502, 503, 504} and attempt == 0:
                    time.sleep(0.25)
                    continue
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt:
                    raise
                time.sleep(0.25)
        if response.status_code == 403 and response.cookies:
            response = (self.session.post(url, data=data, timeout=self.timeout, headers=headers)
                        if data is not None else self.session.get(url, timeout=self.timeout, headers=headers))
        response.raise_for_status()
        return response

    def discover(self, jurisdiction_code: str) -> list[MonthlyReportLink]:
        config = province_configs(self.config_path)[jurisdiction_code]
        self.discovery_diagnostics = []
        if not config.index_url:
            return []
        if config.index_json_article_url_template:
            return self._discover_json_index(config)
        links: dict[tuple[date, str], MonthlyReportLink] = {}
        budget = max(1, min(24, config.max_index_pages))
        index_urls = list(dict.fromkeys((config.index_url, *config.archive_index_urls)))
        if config.pagination_url_template:
            for page in range(1, budget):
                candidate = config.pagination_url_template.format(page=page)
                if self._is_column_page(candidate, config.index_url) and candidate not in index_urls:
                    index_urls.append(candidate)
        visited: set[str] = set()
        failures: list[Exception] = []
        current_month = datetime.now(ZoneInfo(config.timezone)).date().replace(day=1)
        while index_urls and len(visited) < budget:
            index_url = index_urls.pop(0)
            if index_url in visited:
                continue
            visited.add(index_url)
            try:
                response = self._get(index_url, data=dict(config.index_post_data)) if config.index_post_data else self._get(index_url)
            except (requests.RequestException, ValueError) as exc:
                failures.append(exc)
                self.discovery_diagnostics.append({"url": index_url, "status": "failed", "error": str(exc)})
                continue
            self.discovery_diagnostics.append({"url": response.url, "status": "fetched"})
            index_content: bytes | str = response.content
            if config.index_json_html_path:
                fragment = response.json()
                for key in config.index_json_html_path:
                    fragment = fragment.get(key) if isinstance(fragment, dict) else None
                if not isinstance(fragment, str):
                    raise ValueError(f"Province index JSON has no configured HTML fragment ({response.url})")
                index_content = fragment
            soup = BeautifulSoup(index_content, "lxml")
            base = soup.find("base", href=True)
            base_url = urljoin(response.url, str(base["href"])) if base else response.url
            anchors = list(soup.select("a[href], [data-link]"))
            if config.index_xml_records:
                embedded = soup.select_one('script[type="text/xml"]')
                if embedded is None:
                    raise ValueError(f"Province index has no configured XML records ({response.url})")
                try:
                    records = ET.fromstring(embedded.get_text()).findall("./recordset/record")
                except ET.ParseError as exc:
                    raise ValueError(f"Invalid province index XML records ({response.url})") from exc
                if not records:
                    raise ValueError(f"Province index XML records are empty ({response.url})")
                for record in records:
                    anchors.extend(BeautifulSoup(record.text or "", "lxml").select("a[href]"))
            for anchor in anchors:
                heading = anchor.select_one(".article__title")
                title = " ".join((heading or anchor).get_text(" ", strip=True).split())
                title = str(anchor.get("title") or title)
                href = anchor.get("data-link") or anchor.get("href")
                if not href or str(href).startswith(("javascript:", "#")):
                    continue
                url = urljoin(base_url, str(href))
                if urlsplit(url).scheme not in {"http", "https"}:
                    continue
                # Only documented index filenames, or a same-file next-page
                # query, are eligible. Article links never expand the crawl.
                if self._is_column_page(url, response.url) and (
                    re.search(r"(?:下一页|下页|next)", title, re.I)
                    or re.fullmatch(r"(?:index|list|nav_list)_\d+\.(?:s?html?)", urlsplit(url).path.rsplit("/", 1)[-1], re.I)
                ) and url not in visited and url not in index_urls:
                    index_urls.append(url)
                match = MONTH_PATTERN.search(title)
                if not match or "传染病" not in title or "全国" in title:
                    continue
                if jurisdiction_code == "CN-SD" and "山东省" not in title:
                    continue  # The official city mirror also lists city-only reports.
                if config.report_title_contains and not any(token in title for token in config.report_title_contains):
                    continue
                report_date = date(int(match["year"]), int(match["month"]), 1)
                if report_date > current_month:
                    self.discovery_diagnostics.append({"url": url, "status": "future_month_rejected", "month": report_date.isoformat()})
                    continue
                links[(report_date, url)] = MonthlyReportLink(
                    jurisdiction_code, report_date, title, url
                )
        if index_urls:
            self.discovery_diagnostics.append({"status": "page_budget_reached", "budget": budget})
        if not links and failures and len(failures) == len(visited):
            raise failures[0]
        return sorted(links.values(), key=lambda item: (item.report_date, item.url))

    def _discover_json_index(self, config: ProvinceSourceConfig) -> list[MonthlyReportLink]:
        """Read a bounded official JSON article listing with a fixed category."""
        links: dict[tuple[date, str], MonthlyReportLink] = {}
        current_month = datetime.now(ZoneInfo(config.timezone)).date().replace(day=1)
        seen = 0
        budget = max(1, min(24, config.max_index_pages))
        for page in range(1, budget + 1):
            data = {**dict(config.index_post_data), config.index_page_parameter: str(page)}
            response = self._get(config.index_url, data=data)
            payload = response.json()
            items = payload.get("list") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError(f"Province JSON listing has no article list ({response.url})")
            self.discovery_diagnostics.append({"url": response.url, "status": "fetched", "page": page, "articles": len(items)})
            if not items:
                break
            seen += len(items)
            for item in items:
                if not isinstance(item, dict) or "title" not in item or "id" not in item:
                    raise ValueError(f"Province JSON listing has an invalid article ({response.url})")
                title = str(item["title"])
                match = MONTH_PATTERN.search(title)
                if not match or "传染病" not in title or "全国" in title:
                    continue
                if config.report_title_contains and not any(token in title for token in config.report_title_contains):
                    continue
                report_date = date(int(match["year"]), int(match["month"]), 1)
                article_id = str(item["id"])
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", article_id):
                    raise ValueError("Province JSON listing has an unsafe article identifier")
                url = config.index_json_article_url_template.format(id=article_id)
                if urlsplit(url).netloc != urlsplit(config.index_url).netloc:
                    raise ValueError("Province JSON listing article URL must retain the configured origin")
                if report_date > current_month:
                    self.discovery_diagnostics.append({"url": url, "status": "future_month_rejected", "month": report_date.isoformat()})
                    continue
                links[(report_date, url)] = MonthlyReportLink(config.code, report_date, title, url)
            total = payload.get("count")
            if isinstance(total, int) and seen >= total:
                break
        else:
            self.discovery_diagnostics.append({"status": "page_budget_reached", "budget": budget})
        return sorted(links.values(), key=lambda item: (item.report_date, item.url))

    @staticmethod
    def _is_column_page(candidate: str, index_url: str) -> bool:
        target, index = urlsplit(candidate), urlsplit(index_url)
        if target.scheme not in {"http", "https"} or target.netloc != index.netloc:
            return False
        column = index.path if index.path.endswith("/") else index.path.rsplit("/", 1)[0] + "/"
        target_dir, _, filename = target.path.rpartition("/")
        if target_dir + "/" != column:
            return False
        page_keys = {"page", "pageno", "pagenum", "pageindex", "currentpage", "currpage"}
        target_query, index_query = parse_qs(target.query), parse_qs(index.query)
        def query_scope(query):
            scope = {key: value for key, value in query.items() if key.casefold() not in page_keys}
            if "paramJson" in scope and len(scope["paramJson"]) == 1:
                try:
                    params = json.loads(scope["paramJson"][0])
                except (TypeError, ValueError):
                    return scope
                if isinstance(params, dict):
                    scope["paramJson"] = {key: value for key, value in params.items() if key.casefold() not in page_keys}
            return scope
        target_scope, index_scope = query_scope(target_query), query_scope(index_query)
        if target_scope != index_scope:
            return False
        index_name = index.path.rsplit("/", 1)[-1]
        extensionless_page = bool(
            index_name and "." not in index_name
            and re.fullmatch(re.escape(re.sub(r"_\d+$", "", index_name)) + r"_\d+", filename)
        )
        return bool(
            re.fullmatch(r"(?:index|list|nav_list)(?:_\d+)?\.(?:s?html?)", filename, re.I)
            or target.path == index.path and target.query != index.query
            or extensionless_page
        )

    def fetch(self, link: MonthlyReportLink) -> ParsedMonthlyReport:
        config = province_configs(self.config_path)[link.jurisdiction_code]
        self.report_diagnostics = []
        image_documents = []
        structured_rows = []
        if link.report_date > datetime.now(ZoneInfo(config.timezone)).date().replace(day=1):
            raise ValueError(f"Cannot fetch future province report month: {link.report_date}")
        response = self._get(
            link.url,
            headers={"Referer": config.index_url} if config.index_url else None,
        )
        # Some Chinese government sites omit a charset in HTTP headers while
        # declaring UTF-8 in the HTML meta tag. Requests then defaults to
        # ISO-8859-1, which turns Chinese table labels into mojibake before
        # pandas can map them. Honor its byte-level encoding detection here.
        if response.encoding and response.encoding.lower() in {"iso-8859-1", "latin-1"}:
            response.encoding = response.apparent_encoding or response.encoding
        retrieved_at = datetime.now(timezone.utc).isoformat()
        page_hash = hashlib.sha256(response.content).hexdigest()
        parser = config.parser
        if parser == "discovery_pending":
            raise ValueError(
                f"{link.jurisdiction_code} source is {parser}; no source is configured"
            )
        soup = BeautifulSoup(response.content, "lxml")
        base = soup.find("base", href=True)
        base_url = urljoin(response.url, str(base["href"])) if base else response.url
        candidates: list[tuple[str, str]] = []
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            suffix = _attachment_suffix(href) or _attachment_suffix(
                anchor.get_text(" ", strip=True)
            )
            if suffix is not None:
                candidates.append((urljoin(base_url, href), suffix))
        # Prefer complete HTML disease tables when present, even for an
        # adapter previously classified as narrative-only. Some publishers
        # alternate formats or add detail tables without changing the index.
        try:
            tables = pd.read_html(io.StringIO(response.text), flavor="lxml")
        except ValueError:
            tables = []
        if tables:
            try:
                rows = self._normalize_tables(
                    tables, link=link, source_url=response.url,
                    artifact_sha256=page_hash,
                )
            except ValueError as exc:
                if "No registered disease rows parsed" not in str(exc):
                    raise
            else:
                structured_rows.extend(rows)

        # Capture article images, including publishers which do not use W020
        # filenames. Exclude navigation images by selecting the article body
        # when available; the visual reviewers later reject non-report layouts.
        body = soup.select_one(".TRS_Editor, .TRS_UEDITOR, #zoom, #zoomcon, .article-content, .article_content, .content-detail")
        image_urls = []
        image_locations = []
        for picture in (body or soup).select("img[src], img[data-src], img[data-original], img[data-lazy-src]"):
            raw_url = str(picture.get("data-original") or picture.get("data-src") or picture.get("data-lazy-src") or picture.get("src") or "")
            anchor = picture.find_parent("a", href=True)
            if anchor and re.search(r"\.(?:png|jpe?g|tiff?|webp|gif|bmp)$", urlsplit(str(anchor["href"])).path, re.I):
                raw_url = str(anchor["href"])  # linked original over thumbnail
            image_locations.append(raw_url)
        image_locations.extend(str(anchor["href"]) for anchor in (body or soup).select("a[href]")
                               if re.search(r"\.(?:png|jpe?g|tiff?|webp|gif|bmp)$", urlsplit(str(anchor["href"])).path, re.I))
        for raw_url in image_locations:
            image_url = urljoin(base_url, raw_url)
            if (urlsplit(image_url).scheme in {"http", "https"}
                    and (body is not None or re.search(r"(?:W020\d+|/ueditor/image/|/contentmanage/article/image/)", image_url, re.I))
                    and image_url not in image_urls):
                image_urls.append(image_url)
        if len(image_urls) > MAX_REPORT_IMAGES:
            self.report_diagnostics.append({"url": response.url, "status": "image_document_limit", "images": len(image_urls)})
            image_urls = []  # never review a silently truncated report
        for image_url in image_urls:
            try:
                image = self._get(image_url, headers={"Referer": response.url})
                image_type = image.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not image_type.startswith("image/"):
                    # A number of government servers return octet-stream.
                    from PIL import Image
                    with Image.open(io.BytesIO(image.content)) as decoded:
                        image_type = Image.MIME.get(decoded.format, "")
                if not image_type.startswith("image/") or len(image.content) > MAX_REPORT_IMAGE_BYTES:
                    raise ValueError("Unsupported or oversized report image")
                image_documents.append({"data": image.content, "media_type": image_type,
                                      "source_url": image.url, "sha256": hashlib.sha256(image.content).hexdigest()})
                self.report_diagnostics.append({"url": image.url, "status": "image_pending_review", "article_url": response.url})
            except Exception as exc:
                self.report_diagnostics.append({"url": image_url, "status": "image_capture_failed", "error": str(exc)})
        content_type = response.headers.get("Content-Type", "text/html")
        # Keep evidence separately per artifact. A page count must never be
        # attributed to an unrelated last attachment's URL/hash.
        narratives = [(" ".join(soup.stripped_strings), response.url, page_hash, content_type)]
        for attachment_url, suffix_hint in candidates:
            try:
                attachment = self._get(attachment_url, headers={"Referer": response.url})
            except TaskCancelledError:
                raise
            except (requests.RequestException, RuntimeError) as exc:
                # A failed supplemental carrier must not erase explicit HTML
                # facts. Replay may also lack bytes for a failed download.
                self.report_diagnostics.append({"url": attachment_url, "status": "attachment_fetch_failed", "error": str(exc)})
                continue
            candidate_hash = hashlib.sha256(attachment.content).hexdigest()
            content_type = attachment.headers.get(
                "Content-Type", "application/octet-stream"
            )
            try:
                attachment_tables = parse_attachment_tables(
                    attachment.content,
                    attachment_url,
                    suffix_hint=suffix_hint,
                )
            except Exception as exc:
                # Keep the article text as evidence when a PDF has no
                # machine-readable table; unsupported/invalid tables still
                # fail closed below if disease rows were actually found.
                attachment_tables = []
                self.report_diagnostics.append({"url": attachment.url, "status": "attachment_parse_failed", "error": str(exc)})
            if suffix_hint == ".pdf":
                needs_visual = not attachment_tables
                try:
                    import pdfplumber

                    with pdfplumber.open(io.BytesIO(attachment.content)) as pdf:
                        narratives.append((" ".join(page.extract_text() or "" for page in pdf.pages), attachment.url, candidate_hash, content_type))
                        # A text table on page1 must not hide a scanned page2.
                        # Large page images also catch scans carrying a small
                        # native header or an unreliable hidden text layer.
                        needs_visual |= any(
                            any(float(image.get("width", 0)) * float(image.get("height", 0))
                                >= float(page.width) * float(page.height) * .25
                                for image in page.images)
                            for page in pdf.pages)
                except Exception:
                    needs_visual = True
                if needs_visual:
                    image_documents.append({"data": attachment.content, "media_type": "application/pdf",
                                          "source_url": attachment.url, "sha256": candidate_hash})
                    self.report_diagnostics.append({"url": attachment.url, "status": "image_pending_review"})
                    attachment_tables = []
            if attachment_tables:
                try:
                    rows = self._normalize_tables(
                        attachment_tables, link=link, source_url=attachment.url,
                        artifact_sha256=candidate_hash,
                    )
                except ValueError as exc:
                    if "No registered disease rows parsed" not in str(exc):
                        raise
                else:
                    structured_rows.extend(rows)
        rows = _deduplicate_source_rows(
            [*structured_rows, *(row
            for narrative_text, artifact_url, artifact_hash, _ in narratives
            for row in _normalize_narrative_report(
                narrative_text, link=link, source_url=artifact_url,
                artifact_sha256=artifact_hash, config_path=self.config_path,
            )), *(row
            for narrative_text, artifact_url, artifact_hash, _ in narratives
            for row in (parse_named_monthly_deaths(
                narrative_text, report_date=link.report_date, title=link.title,
                source_url=artifact_url, artifact_sha256=artifact_hash,
                config_path=self.config_path, resolve_disease=resolve_disease,
                source_row=_source_row, source_id=MONTHLY_REPORT_SOURCE_ID,
            ) if link.jurisdiction_code == "CN-NM" else []))]
        )
        if not rows and not image_documents:
            article_text = narratives[0][0]
            summary_only = (not tables and not candidates and not image_urls
                            and "传染病" in article_text
                            and re.search(r"(?:发病总数|报告发病|报告病例|共报告|总病例数|病种名单|位居前)", article_text))
            self.report_diagnostics.append({
                "url": response.url,
                "report_month": link.report_date.strftime("%Y-%m"),
                "status": "summary_only" if summary_only else "no_disease_values",
                "html_table_count": len(tables),
                "attachment_count": len(candidates),
                "image_link_count": len(image_urls),
            })
            raise ValueError(f"No explicit disease values found in {response.url}")
        if rows and not structured_rows:
            self.report_diagnostics.append({"url": response.url, "status": "partial_narrative", "rows": len(rows)})
        return ParsedMonthlyReport(
            rows, response.url, page_hash, response.headers.get("Content-Type", "text/html"), retrieved_at,
            image_documents=image_documents, article_text=" ".join(soup.stripped_strings),
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
            normalized = _normalize_report_table(table, include_deaths=True)
            for item in normalized:
                disease = _resolve_report_disease(item["label"], self.config_path)
                if disease is None:
                    if not _is_report_total_label(item["label"]):
                        unmapped_labels.append(str(item["label"]))
                    continue
                valid_from = disease.get("valid_from")
                if valid_from and link.report_date < date.fromisoformat(str(valid_from)):
                    self.report_diagnostics.append({"url": source_url, "status": "before_definition_start", "disease": disease["code"], "month": link.report_date.isoformat(), "valid_from": valid_from})
                    continue
                row = _source_row(
                    report_date=link.report_date,
                    province_code=link.jurisdiction_code,
                    disease=disease,
                    raw_label=item["label"],
                    cases=item["cases"],
                    deaths=item.get("deaths"),
                    source_id=MONTHLY_REPORT_SOURCE_ID,
                    source_url=source_url,
                    source_name="Chinese provincial statutory infectious disease monthly report",
                    quality_status="raw",
                    raw={**item, "artifact_sha256": artifact_sha256, "title": link.title},
                )
                if item["cases"] is not None:
                    rows.append(row)
                    continue
                # Keep the metrics separate before reconciliation: a missing
                # case cell must not hide its death count or conflict with a
                # known case value in another carrier of this same report.
                row.pop("Deaths", None)
                if item["case_column_present"]:
                    rows.append({**row, "MissingValueStatus": "source_missing"})
                code = str(disease["code"])
                rows.append({
                    **row, "SourceDiseaseCode": code + "__deaths", "Diseases": code + "__deaths",
                    "RawDiseaseLabel": item["label"] + " [deaths]", "Measure": "deaths",
                    "Cases": item["deaths"],
                    "Raw": {**row["Raw"], "metric": "deaths", "paired_source_disease_code": code},
                })
        if unmapped_labels:
            raise ValueError(
                "Official province report contains unmapped disease labels: "
                f"{sorted(set(unmapped_labels))!r} ({source_url})"
            )
        result = _deduplicate_source_rows(rows)
        if not result:
            raise ValueError(f"No registered disease rows parsed from {source_url}")
        return result


def _normalize_narrative_report(text: str, *, link: MonthlyReportLink, source_url: str,
                                artifact_sha256: str, config_path: str | Path = DEFAULT_CONFIG
                                ) -> list[dict[str, object]]:
    return _province_normalization._normalize_narrative_report(text, link=link,
        source_url=source_url, artifact_sha256=artifact_sha256, config_path=config_path,
        load_config=load_config, source_row=_source_row, monthly_source_id=MONTHLY_REPORT_SOURCE_ID)


def parse_attachment_tables(
    content: bytes,
    url: str,
    *,
    suffix_hint: str | None = None,
) -> list[pd.DataFrame]:
    suffix = suffix_hint or _attachment_suffix(url)
    if suffix == ".wps":
        if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return _legacy_doc_tables(content)
        if content.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if "word/document.xml" in archive.namelist():
                        return _docx_tables(content)
            except zipfile.BadZipFile as exc:
                raise ValueError("Invalid WPS ZIP signature/document") from exc
        raise ValueError("Unsupported WPS attachment signature; expected an OLE Word or DOCX document")
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
        if previous is not None and (
            (previous["Cases"] is not None and row["Cases"] is not None and previous["Cases"] != row["Cases"])
            or (
                previous.get("Deaths") is not None
                and row.get("Deaths") is not None
                and previous["Deaths"] != row["Deaths"]
            )
        ):
            raise ValueError(f"Conflicting province source rows: {identity}")
        if (previous is None or previous["Cases"] is None and row["Cases"] is not None
                or previous.get("Deaths") is None and row.get("Deaths") is not None):
            result[identity] = row
    return [result[key] for key in sorted(result)]


__all__ = [
    "DATACENTER_SOURCE_ID", "MONTHLY_REPORT_SOURCE_ID", "MonthlyReportLink",
    "ParsedMonthlyReport", "ProvinceDataCenterCrawler", "ProvinceMonthlyReportCrawler",
    "ProvinceSourceConfig", "load_config", "load_phsm_history",
    "parse_attachment_tables", "parse_datacenter_spreadsheet", "province_configs",
    "province_geography_key", "resolve_disease", "resolve_province_code",
]
