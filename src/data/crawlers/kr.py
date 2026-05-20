"""Korea KDCA notifiable infectious disease crawler.

The public data.go.kr catalogue entry 15139178 exposes KDCA's full-notification
infectious disease statistics via ``apis.data.go.kr/1790387/EIDAPIService``.
For the GlobalID storage grain we use the monthly ``PeriodRegion`` operation,
which returns national disease totals plus domestic/imported subtotals.
When no OpenAPI key is available, the same normalizer can ingest KDCA dportal
or KOSIS CSV/XLSX/JSON downloads.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote, urljoin

import requests

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Korea KDCA EID Open API"
DEFAULT_PORTAL_SOURCE_NAME = "Korea KDCA EID Portal Download"
DEFAULT_KOSIS_SOURCE_NAME = "Korea KOSIS Download"
DEFAULT_DOC_URL = "https://www.data.go.kr/data/15139178/openapi.do"
DEFAULT_DPORTAL_DISEASE_URL = "https://dportal.kdca.go.kr/pot/is/inftnsds.do"
DEFAULT_DPORTAL_REGION_URL = "https://dportal.kdca.go.kr/pot/is/summaryRgin.do"
DEFAULT_DPORTAL_STATS_AJAX_URL = (
    "https://dportal.kdca.go.kr/pot/is/selectBassDissStatsListAjax.do"
)
DEFAULT_KOSIS_URL = "https://kosis.kr/index/index.do"
DEFAULT_BASE_URL = "https://apis.data.go.kr/1790387/EIDAPIService"
DEFAULT_SOURCE_SCOPE = "kdca_open_api"
DEFAULT_SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
DEFAULT_DPORTAL_FILE_ENV = "KR_DPORTAL_FILE"
DEFAULT_DPORTAL_DIR_ENV = "KR_DPORTAL_DIR"
DEFAULT_KOSIS_FILE_ENV = "KR_KOSIS_FILE"
DEFAULT_PAGE_SIZE = 1000
DEFAULT_HISTORY_START_YEAR = 2001
DOWNLOAD_FILE_EXTENSIONS = {".csv", ".tsv", ".txt", ".json", ".xlsx", ".xls", ".html", ".htm"}
DEFAULT_DOWNLOAD_FIELDNAMES = [
    "",
    "Disease",
    "DiseaseCode",
    "DiseaseGroup",
    "Year",
    "Month",
    "Date",
    "Cases",
    "LocalCases",
    "ImportedCases",
    "Source",
    "SourceURL",
]


@dataclass
class KRFetchSummary:
    row_count: int
    latest_date: Optional[date]
    years_fetched: int
    source_url: str
    source_kind: str = "openapi"


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value).replace(",", "")
    if not txt or txt in {"-", "—", "N/A", "na", "null", "None"}:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


def _parse_count(value: object) -> int:
    parsed = _parse_int(value)
    return max(0, parsed or 0)


def _last_n_months(count: int = 3) -> Set[Tuple[int, int]]:
    now = datetime.now()
    months: Set[Tuple[int, int]] = set()
    for delta in range(max(1, count)):
        month = now.month - delta
        year = now.year
        if month <= 0:
            month += 12
            year -= 1
        months.add((year, month))
    return months


def parse_kdca_period_month(value: object) -> Optional[date]:
    """Parse KDCA period strings such as ``2024-01`` or ``2024년 1월``."""
    text = _norm_text(value)
    if not text:
        return None

    digits = re.findall(r"\d+", text)
    year: Optional[int] = None
    month: Optional[int] = None

    if len(digits) >= 2:
        year = int(digits[0])
        month = int(digits[1])
    elif len(digits) == 1:
        token = digits[0]
        if len(token) >= 6:
            year = int(token[:4])
            month = int(token[4:6])

    if year is None or month is None or not (1 <= month <= 12):
        return None
    return date(year, month, 1)


def _norm_key(value: object) -> str:
    return re.sub(r"[\s_\-./()]+", "", _norm_text(value).lower())


def _lookup(row: Dict[str, Any], names: Sequence[str]) -> object:
    key_map = {_norm_key(key): value for key, value in row.items()}
    for name in names:
        key = _norm_key(name)
        if key in key_map:
            return key_map[key]
    return None


def _first_text(row: Dict[str, Any], names: Sequence[str]) -> str:
    return _norm_text(_lookup(row, names))


def _is_total_label(value: object) -> bool:
    text = _norm_text(value).lower()
    return text in {"", "계", "합계", "총계", "누계", "소계", "total", "subtotal"}


def _month_from_column(value: object) -> Optional[int]:
    text = _norm_text(value)
    if not text:
        return None

    compact = re.sub(r"\s+", "", text).lower()
    match = re.fullmatch(r"column0?([1-9]|1[0-2])", compact)
    if match:
        return int(match.group(1))

    match = re.fullmatch(r"0?([1-9]|1[0-2])월", compact)
    if match:
        return int(match.group(1))

    match = re.fullmatch(r"0?([1-9]|1[0-2])", compact)
    if match:
        return int(match.group(1))

    english_months = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return english_months.get(compact)


def _year_from_text(value: object) -> Optional[int]:
    match = re.search(r"(19|20)\d{2}", _norm_text(value))
    if not match:
        return None
    year = int(match.group(0))
    if 1900 <= year <= 2100:
        return year
    return None


def _year_from_row(row: Dict[str, Any], fallback_year: Optional[int]) -> Optional[int]:
    for key in ("Year", "year", "연도", "년도", "시점", "기간", "Date", "period"):
        year = _year_from_text(_lookup(row, [key]))
        if year is not None:
            return year
    return fallback_year


def _year_from_path(path: Path) -> Optional[int]:
    # Prefer explicit year in file name, then try parent directories (many exports
    # are saved in year-scoped folders in common workflows).
    for candidate in [path.stem, path.name, *[parent.name for parent in path.parents]]:
        year = _year_from_text(candidate)
        if year is not None:
            return year
    return None


def _download_source_for_path(path: Path) -> Tuple[str, str]:
    lower_name = path.name.lower()
    if "kosis" in lower_name:
        return DEFAULT_KOSIS_SOURCE_NAME, DEFAULT_KOSIS_URL
    return DEFAULT_PORTAL_SOURCE_NAME, DEFAULT_DPORTAL_DISEASE_URL


def _append_normalized_row(
    rows: List[Dict[str, str]],
    *,
    report_date: date,
    disease_name: str,
    cases: int,
    disease_code: str = "",
    disease_group: str = "",
    local_cases: int = 0,
    imported_cases: int = 0,
    source_name: str,
    source_url: str,
) -> None:
    if _is_total_label(disease_name):
        return
    rows.append(
        {
            "Date": report_date.isoformat(),
            "RawDiseaseLabel": disease_name,
            "DiseaseCode": disease_code,
            "DiseaseGroup": disease_group,
            "Year": str(report_date.year),
            "Month": str(report_date.month),
            "Cases": str(max(0, cases)),
            "LocalCases": str(max(0, local_cases)),
            "ImportedCases": str(max(0, imported_cases)),
            "Source": source_name,
            "SourceURL": source_url,
        }
    )


def _dedupe_rows(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Set[Tuple[str, ...]] = set()
    deduped: List[Dict[str, str]] = []
    for row in rows:
        key = (
            row.get("Date", ""),
            row.get("RawDiseaseLabel", ""),
            row.get("DiseaseCode", ""),
            row.get("DiseaseGroup", ""),
            row.get("Cases", ""),
            row.get("LocalCases", ""),
            row.get("ImportedCases", ""),
            row.get("Source", ""),
            row.get("SourceURL", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def normalize_kdca_download_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    fallback_year: Optional[int] = None,
    source_name: str = DEFAULT_PORTAL_SOURCE_NAME,
    source_url: str = DEFAULT_DPORTAL_DISEASE_URL,
) -> List[Dict[str, str]]:
    """Normalize KDCA portal/KOSIS tabular exports to national monthly rows.

    The portal can export either a wide monthly table (``COLUMN1``..``COLUMN12``
    or ``1월``..``12월``) or rows with explicit date/value columns.  This parser
    accepts both shapes plus the already-normalized CSV written by this crawler.
    """
    normalized_rows: List[Dict[str, str]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        existing_date = parse_kdca_period_month(_lookup(row, ["Date"]))
        existing_label = _first_text(row, ["RawDiseaseLabel", "Disease"])
        existing_cases = _parse_int(_lookup(row, ["Cases"]))
        if existing_date is not None and existing_label and existing_cases is not None:
            _append_normalized_row(
                normalized_rows,
                report_date=existing_date,
                disease_name=existing_label,
                disease_code=_first_text(row, ["DiseaseCode"]),
                disease_group=_first_text(row, ["DiseaseGroup"]),
                cases=existing_cases,
                local_cases=_parse_count(_lookup(row, ["LocalCases"])),
                imported_cases=_parse_count(_lookup(row, ["ImportedCases"])),
                source_name=_first_text(row, ["Source"]) or source_name,
                source_url=_first_text(row, ["SourceURL"]) or source_url,
            )
            continue

        disease_name = _first_text(
            row,
            [
                "RawDiseaseLabel",
                "Disease",
                "감염병명",
                "질병명",
                "SUBTITLE",
                "icdNm",
                "감염병",
                "질병",
                "항목",
                "항목명",
            ],
        )
        disease_group = _first_text(
            row,
            ["DiseaseGroup", "TITLE", "icdGroupNm", "감염병급", "급수", "분류", "급"],
        )
        disease_code = _first_text(
            row,
            ["DiseaseCode", "icdCd", "local_code", "코드"],
        )
        if not disease_name or _is_total_label(disease_name):
            continue

        long_date = parse_kdca_period_month(
            _lookup(row, ["Date", "period", "기간", "시점", "연월", "년월", "TIME", "PRD_DE"])
        )
        long_cases = _parse_int(
            _lookup(row, ["Cases", "resultVal", "발생수", "환자수", "값", "DATA_VALUE", "value"])
        )
        if long_date is not None and long_cases is not None:
            _append_normalized_row(
                normalized_rows,
                report_date=long_date,
                disease_name=disease_name,
                disease_code=disease_code,
                disease_group=disease_group,
                cases=long_cases,
                local_cases=_parse_count(_lookup(row, ["LocalCases", "dmstcVal", "국내"])),
                imported_cases=_parse_count(
                    _lookup(row, ["ImportedCases", "outnatnVal", "국외", "해외유입"])
                ),
                source_name=source_name,
                source_url=source_url,
            )
            continue

        year = _year_from_row(row, fallback_year)
        if year is None:
            continue

        for key, value in row.items():
            month = _month_from_column(key)
            if month is None:
                continue
            cases = _parse_int(value)
            if cases is None:
                continue
            _append_normalized_row(
                normalized_rows,
                report_date=date(year, month, 1),
                disease_name=disease_name,
                disease_code=disease_code,
                disease_group=disease_group,
                cases=cases,
                source_name=source_name,
                source_url=source_url,
            )

    normalized_rows = _dedupe_rows(normalized_rows)
    normalized_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
    return normalized_rows


def _extract_body(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    response = payload.get("response")
    if isinstance(response, dict):
        body = response.get("body")
        return body if isinstance(body, dict) else {}
    body = payload.get("body")
    return body if isinstance(body, dict) else {}


def _extract_header(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    response = payload.get("response")
    if isinstance(response, dict):
        header = response.get("header")
        return header if isinstance(header, dict) else {}
    header = payload.get("header")
    return header if isinstance(header, dict) else {}


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    body = _extract_body(payload)
    items = body.get("items")
    if isinstance(items, dict):
        raw_item = items.get("item")
    else:
        raw_item = items

    if raw_item is None or raw_item == "":
        return []
    if isinstance(raw_item, list):
        return [item for item in raw_item if isinstance(item, dict)]
    if isinstance(raw_item, dict):
        return [raw_item]
    return []


def aggregate_period_region_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    months: Optional[Set[Tuple[int, int]]] = None,
    source_url: str = DEFAULT_DOC_URL,
) -> List[Dict[str, str]]:
    """Normalize KDCA PeriodRegion rows to national monthly CSV rows."""
    aggregate: Dict[Tuple[date, str, str], Dict[str, int]] = {}

    for row in rows:
        month_date = parse_kdca_period_month(row.get("period"))
        if month_date is None:
            continue
        if months is not None and (month_date.year, month_date.month) not in months:
            continue

        disease_name = _norm_text(row.get("icdNm"))
        if not disease_name:
            continue
        disease_group = _norm_text(row.get("icdGroupNm"))

        local_cases = _parse_count(row.get("dmstcVal"))
        imported_cases = _parse_count(row.get("outnatnVal"))
        total_cases = _parse_count(row.get("resultVal"))
        if total_cases == 0 and (local_cases or imported_cases):
            total_cases = local_cases + imported_cases

        key = (month_date, disease_name, disease_group)
        bucket = aggregate.setdefault(
            key,
            {"cases": 0, "local_cases": 0, "imported_cases": 0},
        )
        bucket["cases"] += total_cases
        bucket["local_cases"] += local_cases
        bucket["imported_cases"] += imported_cases

    output_rows: List[Dict[str, str]] = []
    for month_date, disease_name, disease_group in sorted(aggregate):
        totals = aggregate[(month_date, disease_name, disease_group)]
        output_rows.append(
            {
                "Date": month_date.isoformat(),
                "RawDiseaseLabel": disease_name,
                "DiseaseCode": "",
                "DiseaseGroup": disease_group,
                "Year": str(month_date.year),
                "Month": str(month_date.month),
                "Cases": str(totals["cases"]),
                "LocalCases": str(totals["local_cases"]),
                "ImportedCases": str(totals["imported_cases"]),
                "Source": DEFAULT_SOURCE_NAME,
                "SourceURL": source_url,
            }
        )
    return output_rows


class KoreaKDCAOpenAPICrawler(BaseCrawler):
    """Crawler for Korea KDCA notifiable infectious disease OpenAPI."""

    SOURCE_URL = DEFAULT_DOC_URL

    def __init__(
        self,
        *,
        service_key: Optional[str] = None,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; KR-KDCA)",
            timeout=120,
            max_retries=3,
            delay=0.2,
        )
        cfg = get_country_bootstrap_config("KR")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.base_url = str(crawler_cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.doc_url = str(cfg.get("data_source_url") or DEFAULT_DOC_URL)
        self.page_size = int(crawler_cfg.get("page_size") or DEFAULT_PAGE_SIZE)
        self.portal_url = str(crawler_cfg.get("portal_url") or DEFAULT_DPORTAL_DISEASE_URL)
        self.portal_stats_url = str(
            crawler_cfg.get("portal_stats_url")
            or urljoin(self.portal_url, "selectBassDissStatsListAjax.do")
            or DEFAULT_DPORTAL_STATS_AJAX_URL
        )
        self.service_key_env = str(
            crawler_cfg.get("service_key_env") or DEFAULT_SERVICE_KEY_ENV
        )
        self.dportal_file_env = str(
            crawler_cfg.get("dportal_file_env") or DEFAULT_DPORTAL_FILE_ENV
        )
        self.dportal_dir_env = str(
            crawler_cfg.get("dportal_dir_env") or DEFAULT_DPORTAL_DIR_ENV
        )
        self.kosis_file_env = str(
            crawler_cfg.get("kosis_file_env") or DEFAULT_KOSIS_FILE_ENV
        )
        resolved_key = service_key or os.getenv(self.service_key_env) or os.getenv(
            "KDCA_EID_SERVICE_KEY"
        )
        self.service_key = unquote(resolved_key.strip()) if resolved_key else ""
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/kr")

    def _manual_source_paths(
        self,
        *,
        source_file: Optional[Path] = None,
        source_dir: Optional[Path] = None,
    ) -> List[Path]:
        paths: List[Path] = []

        def add_file(candidate: object) -> None:
            text = _norm_text(candidate)
            if not text:
                return
            for piece in re.split(r"[,;]", text):
                part = _norm_text(piece)
                if not part:
                    continue
                path = Path(part).expanduser()
                if path.is_file() and path.suffix.lower() in DOWNLOAD_FILE_EXTENSIONS:
                    paths.append(path)

        def add_dir(candidate: object) -> None:
            text = _norm_text(candidate)
            if not text:
                return
            for piece in re.split(r"[,;]", text):
                part = _norm_text(piece)
                if not part:
                    continue
                path = Path(part).expanduser()
                if not path.is_dir():
                    continue
                for child in sorted(path.iterdir()):
                    if child.is_file() and child.suffix.lower() in DOWNLOAD_FILE_EXTENSIONS:
                        paths.append(child)

        add_file(source_file)
        add_dir(source_dir)
        add_file(os.getenv(self.dportal_file_env))
        add_file(os.getenv(self.kosis_file_env))
        add_dir(os.getenv(self.dportal_dir_env))
        add_dir(self.raw_dir / "portal")
        add_dir(self.raw_dir / "manual")

        unique: List[Path] = []
        seen: Set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(path)
        return unique

    @staticmethod
    def _decode_text(path: Path) -> str:
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_json_rows(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            if all(isinstance(item, dict) for item in payload):
                return [item for item in payload if isinstance(item, dict)]
            rows: List[Dict[str, Any]] = []
            for item in payload:
                rows.extend(KoreaKDCAOpenAPICrawler._extract_json_rows(item))
            return rows

        if not isinstance(payload, dict):
            return []

        for key in ("rows", "data", "items", "item", "result", "list"):
            value = payload.get(key)
            extracted = KoreaKDCAOpenAPICrawler._extract_json_rows(value)
            if extracted:
                return extracted

        response_items = _extract_items(payload)
        if response_items:
            return response_items

        value = payload.get("value")
        extracted = KoreaKDCAOpenAPICrawler._extract_json_rows(value)
        if extracted:
            return extracted

        rows: List[Dict[str, Any]] = []
        for value in payload.values():
            rows.extend(KoreaKDCAOpenAPICrawler._extract_json_rows(value))
        return rows

    def _read_json_rows(self, path: Path) -> List[Dict[str, Any]]:
        payload = json.loads(self._decode_text(path))
        return self._extract_json_rows(payload)

    def _read_csv_rows(self, path: Path) -> List[Dict[str, Any]]:
        text = self._decode_text(path)
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
        rows: List[Dict[str, Any]] = []
        for row in reader:
            cleaned = {str(key or ""): value for key, value in row.items() if key is not None}
            if any(_norm_text(value) for value in cleaned.values()):
                rows.append(cleaned)
        return rows

    @staticmethod
    def _looks_like_header(cells: List[str]) -> bool:
        if not cells:
            return False
        return any(
            _month_from_column(cell) is not None
            or _norm_key(cell)
            in {
                "rawdiseaselabel",
                "disease",
                "감염병명",
                "질병명",
                "subtitle",
                "title",
                "date",
                "year",
                "연도",
                "시점",
                "기간",
            }
            for cell in cells
        )

    def _read_excel_or_html_rows(self, path: Path) -> List[Dict[str, Any]]:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "Reading KR Excel/HTML downloads requires pandas plus the matching "
                "Excel/HTML parser dependency."
            ) from exc

        if path.suffix.lower() in {".html", ".htm"}:
            frames = pd.read_html(path)
        else:
            try:
                raw_frames = pd.read_excel(path, sheet_name=None, dtype=str, header=None)
            except Exception:
                if path.suffix.lower() != ".xls":
                    raise
                frames = pd.read_html(path)
            else:
                frames = list(raw_frames.values())

        rows: List[Dict[str, Any]] = []
        for frame in frames:
            frame = frame.dropna(how="all")
            if frame.empty:
                continue
            header_idx = 0
            for idx in range(min(len(frame.index), 12)):
                cells = [_norm_text(value) for value in frame.iloc[idx].tolist()]
                if self._looks_like_header(cells):
                    header_idx = idx
                    break
            headers = [
                _norm_text(value) or f"col_{pos}"
                for pos, value in enumerate(frame.iloc[header_idx].tolist())
            ]
            data = frame.iloc[header_idx + 1 :].copy()
            data.columns = headers
            for record in data.to_dict(orient="records"):
                cleaned = {str(key): value for key, value in record.items()}
                if any(_norm_text(value) for value in cleaned.values()):
                    rows.append(cleaned)
        return rows

    def _read_download_rows(self, path: Path) -> List[Dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self._read_json_rows(path)
        if suffix in {".csv", ".tsv", ".txt"}:
            return self._read_csv_rows(path)
        if suffix in {".xlsx", ".xls", ".html", ".htm"}:
            return self._read_excel_or_html_rows(path)
        raise ValueError(f"Unsupported KR download file type: {path}")

    def load_download_rows(
        self,
        paths: Sequence[Path],
        *,
        months: Optional[Set[Tuple[int, int]]] = None,
    ) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for path in paths:
            raw_rows = self._read_download_rows(path)
            logger.debug(
                f"[KR-KDCA] Loaded download rows | file={path} rows={len(raw_rows)}"
            )
            source_name, source_url = _download_source_for_path(path)
            normalized = normalize_kdca_download_rows(
                raw_rows,
                fallback_year=_year_from_path(path),
                source_name=source_name,
                source_url=source_url,
            )
            if months is not None:
                normalized = [
                    row
                    for row in normalized
                    if (parsed := parse_kdca_period_month(row.get("Date"))) is not None
                    and (parsed.year, parsed.month) in months
                ]
            rows.extend(normalized)

        rows = _dedupe_rows(rows)
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        return rows

    def _operation_url(self, operation: str) -> str:
        return f"{self.base_url}/{operation.strip('/')}"

    def _fetch_bass_stats_list(
        self,
        year: int,
        *,
        disease_groups: str = "01,02,03",
        search_type: str = "1",
        patient_type: str = "1",
    ) -> List[Dict[str, Any]]:
        """Fetch one year of KDCA dportal monthly disease rows."""
        form = {
            "frmNm": "dissMonthFrm",
            "icdgrpCdArr": disease_groups,
            "startDt": str(year),
            "searchType": search_type,
            "patntType": patient_type,
        }
        try:
            response = self.post(self.portal_stats_url, data=form)
        except requests.exceptions.SSLError as ssl_exc:
            fallback_urls = []
            if self.portal_stats_url.startswith("https://"):
                fallback_urls.append(f"http://{self.portal_stats_url[8:]}")
            if self.portal_stats_url.startswith("http://"):
                fallback_urls.append(f"https://{self.portal_stats_url[7:]}")
            last_error: Exception | None = ssl_exc
            response = None
            for fallback_url in dict.fromkeys(fallback_urls):
                try:
                    time.sleep(self.delay)
                    response = self.session.post(
                        fallback_url, data=form, timeout=self.timeout, verify=False
                    )
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    last_error = exc
                    logger.warning(
                        f"[KR-KDCA] SSL fallback failed for dportal AJAX | "
                        f"year={year} url={fallback_url} error={exc}"
                    )
            if response is None:
                raise RuntimeError(
                    f"KR dportal stats request failed for year={year}: {last_error}"
                ) from ssl_exc
            logger.debug(
                f"[KR-KDCA] dportal AJAX fallback succeeded with verify=False | "
                f"year={year} url={response.url}"
            )
        response_text = response.text.strip()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"KDCA dportal stats AJAX endpoint returned non-JSON for year={year}: "
                f"{response_text[:200]}"
            ) from exc

        rows = self._extract_json_rows(payload)
        if not rows:
            result = _norm_text(payload.get("result"))
            message = _norm_text(payload.get("resultMsg") or payload.get("message"))
            if result and result.lower() in {"false", "0", "fail"}:
                raise RuntimeError(
                    f"KDCA dportal stats request failed for year={year}: {message or 'unknown error'}"
                )
            value = payload.get("value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                result = _norm_text(value.get("result"))
                message = _norm_text(value.get("resultMsg") or value.get("message"))
                if result and result.lower() in {"false", "0", "fail"}:
                    raise RuntimeError(
                        f"KDCA dportal stats request failed for year={year}: "
                        f"{message or 'unknown error'}"
                    )
            logger.warning(
                f"[KR-KDCA] KDCA dportal returned no rows for year={year}; treating as empty"
            )
            return []

        logger.debug(
            f"[KR-KDCA] Portal stats rows fetched | url={self.portal_stats_url} year={year} rows={len(rows)}"
        )
        self._save_raw_payload("portal", page_no=year, params=form, payload=payload)
        return rows

    def _fetch_portal_monthly_rows(
        self,
        years: Sequence[int],
        target_months: Set[Tuple[int, int]],
    ) -> List[Dict[str, str]]:
        """Fetch, normalize, and month-filter monthly rows from dportal endpoint."""
        rows: List[Dict[str, str]] = []
        for year in sorted(set(int(value) for value in years if value)):
            raw_rows = self._fetch_bass_stats_list(year)
            parsed_rows = normalize_kdca_download_rows(
                raw_rows,
                fallback_year=year,
                source_name=DEFAULT_PORTAL_SOURCE_NAME,
                source_url=self.portal_stats_url,
            )
            for row in parsed_rows:
                parsed = parse_kdca_period_month(row.get("Date"))
                if parsed is None or (parsed.year, parsed.month) not in target_months:
                    continue
                rows.append(row)

        return rows

    def _save_raw_payload(
        self,
        operation: str,
        page_no: int,
        params: Dict[str, object],
        payload: Any,
    ) -> None:
        if not self.save_raw:
            return
        safe_operation = re.sub(r"[^A-Za-z0-9_.-]+", "_", operation.strip("/"))
        path = self.raw_dir / safe_operation / f"page_{page_no:04d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = {"params": {k: v for k, v in params.items() if k != "serviceKey"}, "payload": payload}
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")

    def fetch_operation(
        self,
        operation: str,
        *,
        params: Dict[str, object],
        page_size: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch all pages for one KDCA operation and return flattened items."""
        if not self.service_key:
            raise RuntimeError(
                f"KR KDCA OpenAPI service key is missing. Set {self.service_key_env} "
                "to the decoded data.go.kr service key, or provide a KDCA dportal/"
                "KOSIS export file via KR_DPORTAL_FILE, KR_KOSIS_FILE, or --source-file."
            )

        url = self._operation_url(operation)
        size = int(page_size or self.page_size)
        all_items: List[Dict[str, Any]] = []
        page_no = 1

        while True:
            query = {
                "serviceKey": self.service_key,
                "resType": "2",
                "pageNo": page_no,
                "numOfRows": size,
                **params,
            }
            time.sleep(self.delay)
            response = self.session.get(url, params=query, timeout=self.timeout)
            response_text = response.text.strip()
            if response.status_code in {401, 403} or response_text == "Unauthorized":
                raise RuntimeError(
                    "KR KDCA OpenAPI request was unauthorized. Check the data.go.kr service key."
                )
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise RuntimeError(f"KR KDCA OpenAPI request failed: {exc}") from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"KR KDCA OpenAPI returned non-JSON response: {response_text[:200]}"
                ) from exc

            header = _extract_header(payload)
            result_code = _norm_text(header.get("resultCode"))
            result_msg = _norm_text(header.get("resultMsg"))
            if result_code and result_code not in {"00", "0", "INFO-000"}:
                raise RuntimeError(
                    f"KR KDCA OpenAPI returned resultCode={result_code}: {result_msg}"
                )

            self._save_raw_payload(operation, page_no, query, payload)
            items = _extract_items(payload)
            all_items.extend(items)

            body = _extract_body(payload)
            total_count = _parse_int(body.get("totalCount"))
            if total_count is None:
                if not items or len(items) < size:
                    break
            elif page_no * size >= total_count:
                break
            if not items:
                break
            page_no += 1

        return all_items

    def fetch_period_region_monthly(
        self,
        *,
        start_year: int,
        end_year: int,
    ) -> List[Dict[str, Any]]:
        """Fetch monthly national counts split by domestic/imported infection region."""
        return self.fetch_operation(
            "PeriodRegion",
            params={
                "searchPeriodType": "2",
                "searchStartYear": str(start_year),
                "searchEndYear": str(end_year),
            },
        )

    @staticmethod
    def _write_national_csv(output_csv: Path, national_rows: List[Dict[str, str]]) -> None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=DEFAULT_DOWNLOAD_FIELDNAMES)
            writer.writeheader()
            for idx, row in enumerate(national_rows, start=1):
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": row["RawDiseaseLabel"],
                        "DiseaseCode": row["DiseaseCode"],
                        "DiseaseGroup": row["DiseaseGroup"],
                        "Year": row["Year"],
                        "Month": row["Month"],
                        "Date": row["Date"],
                        "Cases": row["Cases"],
                        "LocalCases": row["LocalCases"],
                        "ImportedCases": row["ImportedCases"],
                        "Source": row["Source"],
                        "SourceURL": row["SourceURL"],
                    }
                )

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[List[Tuple[int, int]]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        source_file: Optional[Path] = None,
        source_dir: Optional[Path] = None,
    ) -> KRFetchSummary:
        """Fetch and normalize KDCA monthly data to a national CSV."""
        target_months = set(months) if months is not None else _last_n_months(3)
        if not target_months:
            raise ValueError("KR monthly crawl requires at least one target month")

        fetch_start_year = start_year or min(year for year, _ in target_months)
        fetch_end_year = end_year or max(year for year, _ in target_months)
        manual_paths = self._manual_source_paths(
            source_file=source_file,
            source_dir=source_dir,
        )
        explicit_manual = source_file is not None or source_dir is not None
        if manual_paths and (explicit_manual or not self.service_key):
            national_rows = self.load_download_rows(manual_paths, months=target_months)
            if national_rows:
                self._write_national_csv(output_csv, national_rows)
                latest_date = max(
                    (
                        datetime.strptime(row["Date"], "%Y-%m-%d").date()
                        for row in national_rows
                    ),
                    default=None,
                )
                logger.info(
                    "[KR-KDCA] CSV written from portal/KOSIS download | "
                    f"path={output_csv} rows={len(national_rows)} files={len(manual_paths)} latest={latest_date}"
                )
                return KRFetchSummary(
                    row_count=len(national_rows),
                    latest_date=latest_date,
                    years_fetched=max(0, fetch_end_year - fetch_start_year + 1),
                    source_url=DEFAULT_DPORTAL_DISEASE_URL,
                    source_kind="download",
                )
            if explicit_manual:
                raise RuntimeError(
                    "KR portal/KOSIS download files were provided, but no usable "
                    "monthly rows were parsed."
                )

        if not self.service_key:
            logger.info(
                "[KR-KDCA] No OpenAPI key; falling back to dportal AJAX endpoint"
            )
            portal_rows = self._fetch_portal_monthly_rows(
                years=range(fetch_start_year, fetch_end_year + 1),
                target_months=target_months,
            )
            if portal_rows:
                self._write_national_csv(output_csv, portal_rows)
                latest_date = max(
                    (
                        datetime.strptime(row["Date"], "%Y-%m-%d").date()
                        for row in portal_rows
                    ),
                    default=None,
                )
                logger.info(
                    "[KR-KDCA] CSV written from dportal AJAX | "
                    f"path={output_csv} rows={len(portal_rows)} "
                    f"years={fetch_start_year}-{fetch_end_year} latest={latest_date}"
                )
                return KRFetchSummary(
                    row_count=len(portal_rows),
                    latest_date=latest_date,
                    years_fetched=max(0, fetch_end_year - fetch_start_year + 1),
                    source_url=self.portal_stats_url,
                    source_kind="portal",
                )

            searched = ", ".join(str(path) for path in manual_paths) or (
                f"${self.dportal_file_env}, ${self.kosis_file_env}, "
                f"${self.dportal_dir_env}, {self.raw_dir / 'portal'}"
            )
            raise RuntimeError(
                "KR KDCA OpenAPI service key is missing and no portal/KOSIS "
                "download rows were available. Set DATA_GO_KR_SERVICE_KEY, or "
                "download KDCA/KOSIS CSV/XLSX/JSON and pass --source-file/"
                f"--source-dir. Checked: {searched}"
            )

        raw_rows = self.fetch_period_region_monthly(
            start_year=fetch_start_year,
            end_year=fetch_end_year,
        )
        national_rows = aggregate_period_region_rows(
            raw_rows,
            months=target_months,
            source_url=self.doc_url,
        )
        if not national_rows:
            raise RuntimeError("[KR-KDCA] No national monthly rows parsed from OpenAPI source")

        national_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        self._write_national_csv(output_csv, national_rows)

        latest_date = max(
            (datetime.strptime(row["Date"], "%Y-%m-%d").date() for row in national_rows),
            default=None,
        )
        logger.info(
            f"[KR-KDCA] CSV written | path={output_csv} rows={len(national_rows)} "
            f"years={fetch_start_year}-{fetch_end_year} latest={latest_date}"
        )
        return KRFetchSummary(
            row_count=len(national_rows),
            latest_date=latest_date,
            years_fetched=max(0, fetch_end_year - fetch_start_year + 1),
            source_url=self.doc_url,
            source_kind="openapi",
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/kr/korea_national_monthly.csv"
        )
        months = kwargs.get("months")
        source_file = kwargs.get("source_file")
        source_dir = kwargs.get("source_dir")
        summary = self.crawl_monthly_national(
            output_csv,
            months=months,
            source_file=Path(source_file) if source_file else None,
            source_dir=Path(source_dir) if source_dir else None,
        )
        return [
            CrawlerResult(
                title="Korea KDCA notifiable infectious disease monthly data",
                url=self.doc_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": DEFAULT_SOURCE_SCOPE,
                    "country_code": "KR",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "years_fetched": summary.years_fetched,
                    "source_kind": summary.source_kind,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_monthly_national``."""
        return []
