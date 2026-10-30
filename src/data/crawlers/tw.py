"""Taiwan, China CDC NIDSS open-data crawler.

Taiwan, China CDC's NIDSS dashboard exposes per-disease open CSV files under
``https://od.cdc.gov.tw/eic/Age_County_Gender_<code>.csv``.  Each CSV is a
monthly age/county/gender detail table.  This crawler discovers the disease
codes from the public dashboard and aggregates rows to national monthly totals,
which match the existing ``(time, disease_id, country_id)`` storage grain.
"""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from src.core import get_logger
from src.core.country_library import get_country_bootstrap_config
from src.core.tls import RequestsTLSChainFallback

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Taiwan, China CDC NIDSS Open Data"
DEFAULT_INDEX_URL = "https://nidss.cdc.gov.tw/Home/Index"
DEFAULT_MONTHLY_EXPORT_URL = "https://nidss.cdc.gov.tw/Export/NidssReportTimer"
# These NIDSS source series use diagnosis year/month, also accepted by the
# original CSV normalizer. TB registration dates and reported-only diseases
# are deliberately not substituted for onset/confirmed-case series.
DIAGNOSIS_DATE_DISEASE_CODES = frozenset({"042", "044", "090", "098", "0461"})
DEFAULT_MONTHLY_CSV_TEMPLATE = (
    "https://od.cdc.gov.tw/eic/Age_County_Gender_{disease_code}.csv"
)
DEFAULT_WEEKLY_CSV_TEMPLATE = (
    "https://od.cdc.gov.tw/eic/Weekly_Age_County_Gender_{disease_code}.csv"
)
TWCA_SSL_CA_CERT = (
    Path(__file__).resolve().parents[1]
    / "certs"
    / "twca_ssl_certification_authority_2023.pem"
)


@dataclass(frozen=True)
class TWDiseaseSource:
    code: str
    name: str
    monthly_csv_url: str
    weekly_csv_url: str


@dataclass
class TWFetchSummary:
    row_count: int
    latest_date: Optional[date]
    diseases_fetched: int
    source_url: str


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _parse_int(value: object) -> Optional[int]:
    txt = _norm_text(value).replace(",", "")
    if not txt:
        return None
    try:
        return int(float(txt))
    except ValueError:
        return None


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


def _decode_csv_response(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_monthly_export(payload: bytes, *, disease: TWDiseaseSource, months, date_basis: str, population: str) -> List[Dict[str, str]]:
    """Read the national total from an official monthly ODS, never its subtotals."""
    table_ns = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("content.xml")
        if info.file_size > 20_000_000:
            raise ValueError("NIDSS monthly export exceeds the XML size limit")
        root = ET.fromstring(archive.read(info))
    grid = []
    for row in root.iter(table_ns + "table-row"):
        cells = []
        for cell in row:
            repeated = int(cell.get(table_ns + "number-columns-repeated", "1"))
            if repeated > 2000:
                raise ValueError("NIDSS monthly export has an oversized row")
            cells.extend([_norm_text(" ".join(cell.itertext()))] * repeated)
        grid.append(cells)
    labels = [row[0] for row in grid if row]
    if f"日期種類:{date_basis}" not in labels or "病例種類:確定病例" not in labels:
        raise ValueError("NIDSS monthly export changed date basis or case definition")
    if f"感染來源:{population}" not in labels:
        raise ValueError("NIDSS monthly export changed population scope")
    period_row = next((i for i, row in enumerate(grid) if "年月" in row), None)
    totals = [row for row in grid if row and row[0] == "合計"]
    if period_row is None or len(totals) != 1 or period_row + 2 >= len(grid):
        raise ValueError("NIDSS export lacks one monthly header and national total")
    years, month_numbers = grid[period_row + 1], grid[period_row + 2]
    expected = set(map(tuple, months))
    current_year = None
    found = set()
    output = []
    for index in range(1, len(month_numbers)):
        year = years[index] if index < len(years) else ""
        if re.fullmatch(r"20\d{2}", year):
            current_year = int(year)
        month = _parse_int(month_numbers[index])
        if current_year is None or month is None or not 1 <= month <= 12:
            continue
        key = (current_year, month)
        if key not in expected:
            continue
        if key in found or index >= len(totals[0]):
            raise ValueError("NIDSS monthly export has duplicate or missing monthly cells")
        count_text = totals[0][index].replace(",", "")
        if not re.fullmatch(r"\d+", count_text):
            raise ValueError("NIDSS monthly export has a missing or invalid count")
        count = int(count_text)
        imported = population == "境外移入病例"
        unsplit = population == "本土病例及境外移入病例"
        output.append({
            "Date": date(current_year, month, 1).isoformat(),
            "RawDiseaseLabel": disease.name, "DiseaseCode": disease.code,
            "Year": str(current_year), "Month": str(month), "Cases": str(count),
            "LocalCases": "" if unsplit else "0" if imported else str(count),
            "ImportedCases": "" if unsplit else str(count) if imported else "0",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": DEFAULT_MONTHLY_EXPORT_URL,
        })
        found.add(key)
    if found != expected:
        raise ValueError("NIDSS monthly export does not cover the requested months")
    return output


def normalize_monthly_exports(exports, *, disease: TWDiseaseSource) -> List[Dict[str, str]]:
    expected = {"本國籍及非本國籍"} if disease.code in {"042", "044"} else {"本土病例", "境外移入病例"}
    populations = [item["metadata"]["population"] for item in exports]
    if populations == ["本土病例及境外移入病例"] and disease.code not in {"042", "044"}:
        expected = set(populations)
    if set(populations) != expected or len(populations) != len(expected):
        raise ValueError("NIDSS export batch has missing or duplicate populations")
    combined = {}
    expected_months = None
    for item in exports:
        metadata = item["metadata"]
        basis = "診斷日" if disease.code in DIAGNOSIS_DATE_DISEASE_CODES else "發病日"
        if metadata["date_basis"] != basis:
            raise ValueError("NIDSS export date basis differs from the source contract")
        months = set(map(tuple, metadata["months"]))
        if metadata["code"] != disease.code or (expected_months is not None and months != expected_months):
            raise ValueError("NIDSS export batch mixes diseases or month ranges")
        expected_months = months
        rows = parse_monthly_export(item["payload"], disease=disease, months=months, date_basis=metadata["date_basis"], population=metadata["population"])
        for row in rows:
            previous = combined.get(row["Date"])
            if previous is None:
                combined[row["Date"]] = row
            else:
                for field in ("Cases", "LocalCases", "ImportedCases"):
                    previous[field] = str(int(previous[field]) + int(row[field]))
    return [combined[key] for key in sorted(combined)]


def aggregate_monthly_csv_rows(
    disease: TWDiseaseSource,
    rows: Iterable[Dict[str, str]],
    *,
    months: Optional[Set[Tuple[int, int]]] = None,
) -> List[Dict[str, str]]:
    """Aggregate Taiwan, China CDC detail rows to national monthly rows."""
    aggregate: Dict[Tuple[int, int], Dict[str, int]] = {}

    for row in rows:
        # Most NIDSS extracts use onset year/month, while a small set of
        # surveillance datasets (including AIDS/HIV codes 042/044) expose the
        # same grain under diagnosis year/month instead.
        year = _parse_int(row.get("發病年份") or row.get("診斷年份"))
        month = _parse_int(row.get("發病月份") or row.get("診斷月份"))
        cases = _parse_int(row.get("確定病例數"))
        if year is None or month is None or cases is None:
            continue
        if not (1 <= month <= 12):
            continue
        if months is not None and (year, month) not in months:
            continue

        bucket = aggregate.setdefault(
            (year, month),
            {"cases": 0, "local_cases": 0, "imported_cases": 0},
        )
        safe_cases = max(0, cases)
        bucket["cases"] += safe_cases

        imported_flag = _norm_text(row.get("是否為境外移入"))
        if imported_flag == "1":
            bucket["imported_cases"] += safe_cases
        else:
            bucket["local_cases"] += safe_cases

    output_rows: List[Dict[str, str]] = []
    for year, month in sorted(aggregate):
        totals = aggregate[(year, month)]
        output_rows.append(
            {
                "Date": date(year, month, 1).isoformat(),
                "RawDiseaseLabel": disease.name,
                "DiseaseCode": disease.code,
                "Year": str(year),
                "Month": str(month),
                "Cases": str(totals["cases"]),
                "LocalCases": str(totals["local_cases"]),
                "ImportedCases": str(totals["imported_cases"]),
                "Source": DEFAULT_SOURCE_NAME,
                "SourceURL": disease.monthly_csv_url,
            }
        )
    return output_rows


class TaiwanNIDSSCrawler(BaseCrawler):
    """Crawler for Taiwan, China CDC NIDSS monthly open data CSVs."""

    SOURCE_URL = DEFAULT_INDEX_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; TW-NIDSS)",
            timeout=120,
            max_retries=3,
            delay=0.3,
        )
        cfg = get_country_bootstrap_config("TW")
        crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
        self.index_url = str(crawler_cfg.get("index_url") or DEFAULT_INDEX_URL)
        self.monthly_csv_template = str(
            crawler_cfg.get("monthly_csv_url_template") or DEFAULT_MONTHLY_CSV_TEMPLATE
        )
        self.weekly_csv_template = str(
            crawler_cfg.get("weekly_csv_url_template") or DEFAULT_WEEKLY_CSV_TEMPLATE
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/tw")
        self._tls_fallback = RequestsTLSChainFallback(
            extra_ca_certs=[TWCA_SSL_CA_CERT],
            log_label="[TW-NIDSS]",
        )
        self._http_fallback_hosts: Set[str] = set()
        self._csv_transport_unavailable = False
        # CSV retries are owned by download_csv_bytes. Avoid multiplying its
        # three attempts by the base session's transport retries.
        for scheme in ("http", "https"):
            self.session.mount(
                f"{scheme}://od.cdc.gov.tw/eic/",
                requests.adapters.HTTPAdapter(max_retries=0),
            )

    def fetch_disease_index(self) -> List[TWDiseaseSource]:
        """Discover NIDSS disease codes that are exposed in the dashboard selector."""
        from bs4 import BeautifulSoup

        response = self.get(self.index_url)
        soup = BeautifulSoup(response.text, "html.parser")

        discovered: Dict[str, str] = {}
        selector = soup.find("select", id="DiseaseList")
        if selector is not None:
            for option in selector.find_all("option"):
                value = _norm_text(option.get("value"))
                label = _norm_text(option.get_text(" ", strip=True))
                if not value.startswith("disease?id=") or not label:
                    continue
                code = value.split("=", 1)[1].strip()
                if code and code != "0":
                    discovered.setdefault(code, label)

        if not discovered:
            for link in soup.find_all("a", href=True):
                href = str(link.get("href") or "")
                if "/nndss/disease?id=" not in href.lower():
                    continue
                code = href.split("id=", 1)[1].split("&", 1)[0].strip()
                label = _norm_text(link.get_text(" ", strip=True))
                if code and label:
                    discovered.setdefault(code, label)

        diseases = [
            TWDiseaseSource(
                code=code,
                name=name,
                monthly_csv_url=self.monthly_csv_template.format(disease_code=code),
                weekly_csv_url=self.weekly_csv_template.format(disease_code=code),
            )
            for code, name in discovered.items()
        ]
        logger.info(f"[TW-NIDSS] Index complete | diseases={len(diseases)}")
        return diseases

    def _download_csv_text(self, disease: TWDiseaseSource) -> Optional[str]:
        raw = self.download_csv_bytes(disease)
        return _decode_csv_response(raw) if raw is not None else None

    def download_csv_bytes(self, disease: TWDiseaseSource) -> Optional[bytes]:
        """Download an undecoded response entity for immutable collection."""
        if self._csv_transport_unavailable:
            return None
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                time.sleep(self.delay)
                response = self._get_csv_response(disease.monthly_csv_url)
                if response.status_code == 404:
                    logger.warning(
                        f"[TW-NIDSS] CSV missing | code={disease.code} name={disease.name}"
                    )
                    return None
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" in content_type.lower():
                    logger.warning(
                        f"[TW-NIDSS] CSV returned HTML | code={disease.code} url={disease.monthly_csv_url}"
                    )
                    return None
                return response.content
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    f"[TW-NIDSS] CSV download failed | code={disease.code} "
                    f"attempt={attempt}/3 error={exc}"
                )
                time.sleep(attempt)

        if last_error is not None:
            self._csv_transport_unavailable = isinstance(
                last_error, (requests.ConnectionError, requests.Timeout)
            )
            logger.warning(
                f"[TW-NIDSS] CSV skipped after retries | code={disease.code} "
                f"name={disease.name} error={last_error}"
            )
        return None

    def fetch_monthly_exports(self, disease: TWDiseaseSource, *, months=None) -> List[Dict[str, Any]]:
        """Download official ODS fallback bytes with explicit source semantics.

        No normalized CSV is substituted for raw evidence. The caller persists
        each ODS and its request context before offline organization.
        """
        from bs4 import BeautifulSoup

        target = sorted(set(map(tuple, months)) if months is not None else _last_n_months(3))
        if not target:
            raise ValueError("NIDSS monthly export needs a requested month")
        page_url = f"https://nidss.cdc.gov.tw/nndss/disease?id={disease.code}"
        page = self._tls_fallback.request(self.session, "GET", page_url, timeout=self.timeout)
        page.raise_for_status()
        soup = BeautifulSoup(page.content, "html.parser")
        form = soup.select_one('form[action="/Export/NidssReportTimer"]')
        if form is None:
            raise ValueError(f"NIDSS export unavailable for disease {disease.code}")
        data = {node["name"]: node.get("value", "") for node in form.select("input[name]")}
        if data.get("pty_disease") != disease.code:
            raise ValueError("NIDSS export form returned a different disease")
        case_options = {node.get("value"): _norm_text(node.get_text()) for node in soup.select('select[name="pty_sickclass_value"] option')}
        if case_options.get("determined_cnt") != "確定病例":
            raise ValueError("NIDSS export lacks confirmed cases; reported cases are not substituted")
        date_options = { _norm_text(node.get_text()): node.get("value") for node in soup.select('select[name="pty_date_type_value"] option') }
        date_basis = "診斷日" if disease.code in DIAGNOSIS_DATE_DISEASE_CODES else "發病日"
        if date_basis not in date_options:
            raise ValueError(f"NIDSS export lacks the required {date_basis} basis")
        population_options = {node.get("value"): _norm_text(node.get_text()) for node in soup.select('select[name="pty_immigration_value"] option')}
        populations = [("0,1", "本國籍及非本國籍")] if disease.code in {"042", "044"} else [("0", "本土病例"), ("1", "境外移入病例")]
        if population_options == {"0,1": "本土病例及境外移入病例"}:
            populations = [("0,1", "本土病例及境外移入病例")]
        if any(population_options.get(code) != label for code, label in populations):
            raise ValueError("NIDSS export population dimensions differ from the CSV contract")
        data.update(pty_period_value="ym", pty_y_s=str(target[0][0]), pty_m_s=str(target[0][1]), pty_y_e=str(target[-1][0]), pty_m_e=str(target[-1][1]), pty_date_type_value=date_options[date_basis], pty_sickclass_value="determined_cnt")
        exports = []
        for population_code, population in populations:
            response = self._tls_fallback.request(self.session, "POST", DEFAULT_MONTHLY_EXPORT_URL, data={**data, "pty_immigration_value": population_code}, timeout=self.timeout)
            response.raise_for_status()
            if not response.content.startswith(b"PK"):
                raise ValueError("NIDSS export did not return an ODS workbook")
            exports.append({"payload": response.content, "source_url": DEFAULT_MONTHLY_EXPORT_URL, "metadata": {"code": disease.code, "name": disease.name, "date_basis": date_basis, "population": population, "months": target}})
        return exports

    def _get_csv_response(self, url: str) -> requests.Response:
        parsed = urlparse(url)
        if parsed.hostname in self._http_fallback_hosts:
            try:
                return self.session.get(
                    urlunparse(parsed._replace(scheme="http")),
                    timeout=self.timeout,
                )
            except requests.RequestException:
                # A failed fallback must not pin the rest of this collection
                # to HTTP when the verified HTTPS endpoint may have recovered.
                self._http_fallback_hosts.discard(parsed.hostname)
        try:
            return self._tls_fallback.request(
                self.session,
                "GET",
                url,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as exc:
            error = str(exc).lower()
            handshake_eof = any(marker in error for marker in (
                "unexpected_eof_while_reading", "unexpected eof while reading",
                "eof occurred in violation of protocol",
            ))
            if (
                parsed.scheme != "https" or parsed.hostname != "od.cdc.gov.tw"
                or not handshake_eof or "certificate" in error
            ):
                raise

            # The Taiwan CDC open-data CDN intermittently closes TLS handshakes
            # before returning a certificate, so a CA-bundle retry cannot help.
            # Its public CSV endpoint also exposes HTTP; restrict the downgrade
            # to this exact host and retain the HTTPS URL as provenance.
            fallback_url = urlunparse(parsed._replace(scheme="http"))
            logger.warning(
                f"[TW-NIDSS] HTTPS handshake unavailable; using official HTTP CSV endpoint | "
                f"url={fallback_url} error={exc}"
            )
            response = self.session.get(fallback_url, timeout=self.timeout)
            if (
                response.status_code == 200 and response.content
                and "html" not in response.headers.get("content-type", "").lower()
            ):
                self._http_fallback_hosts.add(parsed.hostname)
            return response

    def _save_raw_csv(self, disease: TWDiseaseSource, csv_text: str) -> None:
        if not self.save_raw:
            return
        path = self.raw_dir / "monthly" / f"{disease.code}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(csv_text, encoding="utf-8")
        logger.debug(f"[TW-NIDSS] Saved raw CSV | path={path}")

    @staticmethod
    def _parse_csv_rows(csv_text: str) -> List[Dict[str, str]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        return [dict(row) for row in reader]

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[List[Tuple[int, int]]] = None,
        disease_codes: Optional[List[str]] = None,
    ) -> TWFetchSummary:
        """Fetch and aggregate Taiwan, China NIDSS monthly CSVs to a national CSV."""
        diseases = self.fetch_disease_index()
        if not diseases:
            raise RuntimeError("[TW-NIDSS] No diseases discovered from NIDSS dashboard")

        target_months = set(months) if months is not None else _last_n_months(3)
        requested_codes = {code.strip() for code in disease_codes or [] if code.strip()}

        all_rows: List[Dict[str, str]] = []
        fetched_count = 0
        failed_count = 0
        skipped_count = 0
        for disease in diseases:
            if requested_codes and disease.code not in requested_codes:
                continue

            try:
                csv_text = self._download_csv_text(disease)
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    f"[TW-NIDSS] CSV skipped after unexpected error | "
                    f"code={disease.code} name={disease.name} error={exc}"
                )
                continue
            if not csv_text:
                try:
                    exports = self.fetch_monthly_exports(disease, months=target_months)
                    all_rows.extend(normalize_monthly_exports(exports, disease=disease))
                    fetched_count += 1
                except (requests.RequestException, ValueError, zipfile.BadZipFile) as exc:
                    skipped_count += 1
                    logger.warning(f"[TW-NIDSS] Monthly export unavailable | code={disease.code} error={exc}")
                continue

            self._save_raw_csv(disease, csv_text)
            detail_rows = self._parse_csv_rows(csv_text)
            national_rows = aggregate_monthly_csv_rows(
                disease,
                detail_rows,
                months=target_months,
            )
            if national_rows:
                all_rows.extend(national_rows)
            fetched_count += 1

        if not all_rows:
            raise RuntimeError(
                "[TW-NIDSS] No national monthly rows parsed from CSV source "
                f"(fetched=0, skipped={skipped_count}, failed={failed_count})"
            )

        if failed_count or skipped_count:
            logger.warning(
                f"[TW-NIDSS] Completed with skipped disease CSVs | "
                f"fetched={fetched_count} skipped={skipped_count} failed={failed_count}"
            )

        all_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "",
            "Disease",
            "DiseaseCode",
            "Year",
            "Month",
            "Date",
            "Cases",
            "LocalCases",
            "ImportedCases",
            "Source",
            "SourceURL",
        ]
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for idx, row in enumerate(all_rows, start=1):
                writer.writerow(
                    {
                        "": str(idx),
                        "Disease": row["RawDiseaseLabel"],
                        "DiseaseCode": row["DiseaseCode"],
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

        latest_date = max(
            (datetime.strptime(row["Date"], "%Y-%m-%d").date() for row in all_rows),
            default=None,
        )
        logger.info(
            f"[TW-NIDSS] CSV written | path={output_csv} "
            f"rows={len(all_rows)} diseases={fetched_count} latest={latest_date}"
        )
        return TWFetchSummary(
            row_count=len(all_rows),
            latest_date=latest_date,
            diseases_fetched=fetched_count,
            source_url=self.index_url,
        )

    async def crawl(self, **kwargs: Any) -> List[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/tw/taiwan_national_monthly.csv"
        )
        months = kwargs.get("months")
        summary = self.crawl_monthly_national(output_csv, months=months)
        return [
            CrawlerResult(
                title="Taiwan, China CDC NIDSS monthly open data",
                url=self.index_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": "nidss_open_data",
                    "country_code": "TW",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "diseases_fetched": summary.diseases_fetched,
                },
            )
        ]

    def parse(self, response: Any) -> List[CrawlerResult]:
        """BaseCrawler contract; parsing is integrated in ``crawl_monthly_national``."""
        return []
