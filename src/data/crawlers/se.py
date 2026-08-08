"""Sweden Public Health Agency (FHM) SmiNet monthly statistics crawler.

FHM publishes national monthly totals on one HTML page per notifiable disease.
Each page normally links to a machine-readable SmiNet CSV.  The CSV host is
known to terminate some TLS handshakes, while the FHM page contains the same
national ``Totalt`` row.  This crawler therefore prefers the CSV and falls back
to the official HTML table without ever substituting the per-100,000 rate for
the case count.

Closed calendar months are emitted by default.  Callers may explicitly include
the current month; months beyond FHM's normal publication boundary are retained
only when the complete fetched source contains at least one non-zero value for
that month.  This prevents the source's future zero placeholders from becoming
false zero-incidence observations.  FHM states that already published
statistics can be revised daily, so normalized rows explicitly opt in to
authoritative revision semantics and retain both page and download provenance.
"""

from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

from src.core import get_logger

from .base import BaseCrawler, CrawlerResult

logger = get_logger(__name__)

DEFAULT_SOURCE_NAME = "Sweden Public Health Agency SmiNet"
DEFAULT_INDEX_URL = (
    "https://www.folkhalsomyndigheten.se/"
    "statistik-och-data/hitta-statistik-och-data/"
)
SOURCE_SCOPE = "fohm_sminet"
ONTOLOGY_SOURCE_ID = "SRC_SE_FOHM_SMINET"
NATIONAL_GEOGRAPHY_KEY = "country:SE:national"
FHM_PUBLICATION_DAY = 8

SWEDISH_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "maj": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}

OUTPUT_FIELDS = [
    "",
    "Disease",
    "RawDiseaseLabel",
    "DiseaseCode",
    "Year",
    "Month",
    "Date",
    "Cases",
    "Geography",
    "GeographyKey",
    "Scope",
    "Granularity",
    "DatasetStatus",
    "IsProvisional",
    "DataComplete",
    "AuthoritativeRevision",
    "UpdateMode",
    "SourceUpdatedAt",
    "RetrievedAt",
    "Source",
    "SourceURL",
    "DownloadURL",
    "RetrievalMethod",
    "PublicReleaseEnabled",
    "LicenseReviewStatus",
]


@dataclass(frozen=True)
class SEDiseasePage:
    """One candidate disease-statistics page discovered from the FHM index."""

    code: str
    index_label: str
    url: str


@dataclass(frozen=True)
class SEFetchSummary:
    """Summary of one normalized national-monthly crawl."""

    row_count: int
    latest_date: Optional[date]
    diseases_fetched: int
    pages_inspected: int
    csv_pages: int
    html_fallback_pages: int
    source_url: str
    latest_source_update: Optional[date]
    placeholder_months_omitted: tuple[tuple[int, int], ...] = ()


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").replace("\xa0", " ").split()).strip()


def _normalize_month_header(value: object) -> str:
    return _norm_text(value).rstrip(".").casefold()


def _parse_cases(value: object) -> Optional[int]:
    """Parse the case-count part of an FHM cell, deliberately ignoring rates."""

    text = _norm_text(value)
    if not text or text in {"-", "—", "..", "."}:
        return None
    count_text = text.split("/", 1)[0].replace(" ", "")
    match = re.search(r"-?\d+", count_text)
    if match is None:
        return None
    parsed = int(match.group(0))
    return parsed if parsed >= 0 else None


def _decode_csv_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_csv_with_delimiter(csv_text: str, delimiter: str) -> dict[int, int]:
    rows = [list(row) for row in csv.reader(io.StringIO(csv_text), delimiter=delimiter)]
    header_index: Optional[int] = None
    month_columns: dict[int, int] = {}
    for index, row in enumerate(rows):
        candidate = {
            SWEDISH_MONTHS[normalized]: column
            for column, value in enumerate(row)
            if (normalized := _normalize_month_header(value)) in SWEDISH_MONTHS
        }
        if len(candidate) >= 3:
            header_index = index
            month_columns = candidate
            break

    if header_index is None:
        return {}

    for row in rows[header_index + 1 :]:
        if not row:
            continue
        row_label = _norm_text(row[0]).casefold()
        if row_label != "totalt":
            continue
        totals: dict[int, int] = {}
        for month, column in month_columns.items():
            if column >= len(row):
                continue
            cases = _parse_cases(row[column])
            if cases is not None:
                totals[month] = cases
        return totals
    return {}


def parse_monthly_csv(content: bytes | str) -> dict[int, int]:
    """Return national monthly case totals from a SmiNet CSV export."""

    csv_text = _decode_csv_bytes(content) if isinstance(content, bytes) else content
    delimiters = [";", "\t", ","]
    if "\t" in csv_text:
        delimiters = ["\t", ";", ","]
    elif ";" not in csv_text:
        delimiters = [",", "\t", ";"]
    for delimiter in delimiters:
        totals = _parse_csv_with_delimiter(csv_text, delimiter)
        if totals:
            return totals
    return {}


def _monthly_article(soup: BeautifulSoup, year: int) -> Optional[Tag]:
    article = soup.select_one("#region-article")
    if not isinstance(article, Tag):
        return None
    headings = " ".join(
        _norm_text(heading.get_text(" ", strip=True))
        for heading in article.select("h1, h2, h3")
    ).casefold()
    if "månadsstatistik" not in headings or str(year) not in headings:
        return None
    return article


def parse_monthly_html(html: str, year: int) -> dict[int, int]:
    """Parse the nationwide ``Totalt`` row from the official FHM HTML table."""

    soup = BeautifulSoup(html, "html.parser")
    article = _monthly_article(soup, year)
    if article is None:
        return {}

    for table in article.select("table"):
        header_cells = table.select("thead tr th")
        if not header_cells:
            first_row = table.select_one("tr")
            header_cells = first_row.find_all(["th", "td"], recursive=False) if first_row else []
        month_columns = {
            SWEDISH_MONTHS[normalized]: column
            for column, cell in enumerate(header_cells)
            if (normalized := _normalize_month_header(cell.get_text(" ", strip=True)))
            in SWEDISH_MONTHS
        }
        if len(month_columns) < 3:
            continue

        for row in table.select("tbody tr") or table.select("tr")[1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells or _norm_text(cells[0].get_text(" ", strip=True)).casefold() != "totalt":
                continue
            totals: dict[int, int] = {}
            for month, column in month_columns.items():
                if column >= len(cells):
                    continue
                total_element = cells[column].select_one("strong.total")
                # The explicit ``strong.total`` is authoritative.  The text
                # fallback remains count-safe because ``_parse_cases`` stops at
                # the slash before the ``em.per1000`` rate.
                value = (
                    total_element.get_text(" ", strip=True)
                    if total_element is not None
                    else cells[column].get_text(" ", strip=True)
                )
                cases = _parse_cases(value)
                if cases is not None:
                    totals[month] = cases
            return totals
    return {}


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _publication_boundary(reference: date) -> tuple[int, int]:
    """Return the latest month considered published under FHM's day-8 rule."""

    latest_year, latest_month = _previous_month(reference.year, reference.month)
    if reference.day < FHM_PUBLICATION_DAY:
        latest_year, latest_month = _previous_month(latest_year, latest_month)
    return latest_year, latest_month


def closed_months(
    months: Optional[Iterable[tuple[int, int]]] = None,
    *,
    count: int = 3,
    today: Optional[date] = None,
    include_current_month: bool = False,
) -> list[tuple[int, int]]:
    """Return eligible months under FHM's publication calendar.

    FHM publishes the preceding month's first totals on day 8.  Before then,
    that month is still represented by zero-valued placeholders in the HTML
    table and must not be interpreted as a real zero-incidence observation.
    ``include_current_month`` permits fetching through the source-local current
    month so the crawler can apply its all-source non-zero placeholder gate.
    Calendar-future months remain ineligible in both modes.
    """

    reference = today or datetime.now(timezone.utc).date()
    latest_published = _publication_boundary(reference)
    upper = (
        (reference.year, reference.month)
        if include_current_month
        else latest_published
    )
    if months is not None:
        return sorted(
            {
                (int(year), int(month))
                for year, month in months
                if 1 <= int(month) <= 12
                and (int(year), int(month)) <= upper
            }
        )

    resolved: set[tuple[int, int]] = set()
    year, month = upper
    for _ in range(max(1, count)):
        resolved.add((year, month))
        year, month = _previous_month(year, month)
    return sorted(resolved)


def _source_update_from_url(url: str) -> Optional[date]:
    match = re.search(r"_(\d{8})\.csv(?:$|[?#])", url, flags=re.IGNORECASE)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


class SwedenSmiNetCrawler(BaseCrawler):
    """Discover and normalize FHM SmiNet national monthly disease totals."""

    SOURCE_URL = DEFAULT_INDEX_URL

    def __init__(
        self,
        *,
        index_url: str = DEFAULT_INDEX_URL,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        timeout: float = 30.0,
        delay: float = 0.2,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; SE-FHM-SmiNet)",
            timeout=max(1, int(timeout)),
            max_retries=2,
            delay=delay,
        )
        self.index_url = index_url
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/se")
        self.http_client = http_client or httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 (compatible; GlobalID/2.0; SE-FHM-SmiNet)"},
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
        self._owns_http_client = http_client is None
        self._csv_unavailable_hosts: set[str] = set()

    def _get_response(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.delay:
            time.sleep(self.delay)
        response = self.http_client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def fetch_index_html(self) -> str:
        return self._get_response(self.index_url).text

    def discover_disease_pages(self, index_html: Optional[str] = None) -> list[SEDiseasePage]:
        """Discover unique statistics pages; SmiNet eligibility is checked per page."""

        html = index_html if index_html is not None else self.fetch_index_html()
        soup = BeautifulSoup(html, "html.parser")
        target = soup.select_one("#alphabet-filter-target")
        if target is None:
            raise RuntimeError("[SE-FHM] Statistics index has no alphabet-filter target")

        index_host = urlparse(self.index_url).hostname
        discovered: dict[str, SEDiseasePage] = {}
        for link in target.select("a[href]"):
            absolute = urljoin(self.index_url, str(link.get("href") or ""))
            parsed = urlparse(absolute)
            if parsed.hostname != index_host:
                continue
            clean_url = urlunparse(parsed._replace(query="", fragment=""))
            path = unquote(parsed.path).rstrip("/")
            slug = path.rsplit("/", 1)[-1]
            if not slug.endswith("-statistik"):
                continue
            code = slug[: -len("-statistik")]
            label = _norm_text(link.get_text(" ", strip=True))
            if not code or not label:
                continue
            discovered.setdefault(
                clean_url,
                SEDiseasePage(code=code, index_label=label, url=clean_url),
            )
        return sorted(discovered.values(), key=lambda item: item.code)

    @staticmethod
    def page_url_for_year(page: SEDiseasePage, year: int) -> str:
        query = urlencode(
            [("scope[]", "all"), ("tab", "tab-region"), ("year[]", str(year))]
        )
        parsed = urlparse(page.url)
        return urlunparse(parsed._replace(query=query, fragment=""))

    def _fetch_page_html(self, page: SEDiseasePage, year: int) -> tuple[str, str]:
        requested_url = self.page_url_for_year(page, year)
        response = self._get_response(requested_url)
        return response.text, str(response.url)

    def _fetch_csv_bytes(self, url: str) -> bytes:
        host = (urlparse(url).hostname or "").casefold()
        if host in self._csv_unavailable_hosts:
            raise RuntimeError(f"machine CSV host disabled after transport failure: {host}")
        try:
            response = self._get_response(url)
        except httpx.TransportError:
            if host:
                self._csv_unavailable_hosts.add(host)
            raise
        content_type = response.headers.get("content-type", "").casefold()
        if "html" in content_type or response.content.lstrip().startswith(b"<"):
            raise ValueError(f"machine CSV endpoint returned HTML: {url}")
        return response.content

    @staticmethod
    def _page_metadata(
        html: str,
        *,
        page: SEDiseasePage,
        year: int,
        page_url: str,
    ) -> tuple[str, Optional[str], Optional[date]]:
        soup = BeautifulSoup(html, "html.parser")
        article = _monthly_article(soup, year)
        if article is None:
            return page.index_label, None, None

        heading = soup.select_one("h1")
        label = _norm_text(heading.get_text(" ", strip=True)) if heading else page.index_label
        label = re.sub(r"\s*[–—-]\s*statistik\s*$", "", label, flags=re.IGNORECASE).strip()

        csv_link = article.select_one('a[href*="sminet"][href$=".csv" i]')
        if csv_link is None:
            csv_link = article.select_one('a[href*=".csv" i]')
        csv_url = (
            urljoin(page_url, str(csv_link.get("href") or "")) if csv_link else None
        )
        return label or page.index_label, csv_url, (
            _source_update_from_url(csv_url) if csv_url else None
        )

    def _save_raw_artifact(
        self,
        page: SEDiseasePage,
        year: int,
        *,
        html: str,
        csv_content: Optional[bytes],
    ) -> None:
        if not self.save_raw:
            return
        target = self.raw_dir / str(year)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{page.code}.html").write_text(html, encoding="utf-8")
        if csv_content is not None:
            (target / f"{page.code}.csv").write_bytes(csv_content)

    @staticmethod
    def _normalized_rows(
        *,
        page: SEDiseasePage,
        disease_label: str,
        year: int,
        totals: dict[int, int],
        requested_months: set[tuple[int, int]],
        provisional_months: set[tuple[int, int]],
        page_url: str,
        csv_url: Optional[str],
        source_updated_at: Optional[date],
        retrieved_at: str,
        retrieval_method: str,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for month, cases in sorted(totals.items()):
            month_key = (year, month)
            if month_key not in requested_months:
                continue
            is_provisional = month_key in provisional_months
            rows.append(
                {
                    "Date": date(year, month, 1).isoformat(),
                    "RawDiseaseLabel": disease_label,
                    "DiseaseCode": page.code,
                    "Year": str(year),
                    "Month": str(month),
                    "Cases": str(cases),
                    "Geography": "SE:national",
                    "GeographyKey": NATIONAL_GEOGRAPHY_KEY,
                    "Scope": "all",
                    "Granularity": "monthly",
                    "DatasetStatus": (
                        "provisional" if is_provisional else "closed_revisable"
                    ),
                    "IsProvisional": "true" if is_provisional else "false",
                    "DataComplete": "false" if is_provisional else "TRUE",
                    "AuthoritativeRevision": "true",
                    "UpdateMode": (
                        "dynamic_provisional"
                        if is_provisional
                        else "authoritative_revision"
                    ),
                    "SourceUpdatedAt": source_updated_at.isoformat()
                    if source_updated_at
                    else "",
                    "RetrievedAt": retrieved_at,
                    "Source": DEFAULT_SOURCE_NAME,
                    "SourceURL": page_url,
                    "DownloadURL": csv_url or "",
                    "RetrievalMethod": retrieval_method,
                    "PublicReleaseEnabled": "true",
                    "LicenseReviewStatus": "approved_for_public_release",
                }
            )
        return rows

    @staticmethod
    def _write_output(output_csv: Path, rows: list[dict[str, str]]) -> None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for index, row in enumerate(rows, start=1):
                writer.writerow(
                    {
                        **row,
                        "": str(index),
                        "Disease": row["RawDiseaseLabel"],
                    }
                )

    def crawl_monthly_national(
        self,
        output_csv: Path,
        *,
        months: Optional[Iterable[tuple[int, int]]] = None,
        disease_codes: Optional[Iterable[str]] = None,
        today: Optional[date] = None,
        include_current_month: bool = False,
    ) -> SEFetchSummary:
        """Fetch nationwide monthly cases, preferring machine CSV per disease page."""

        effective_today = today or datetime.now(timezone.utc).date()
        requested = closed_months(
            months,
            count=3,
            today=effective_today,
            include_current_month=include_current_month,
        )
        if not requested:
            raise ValueError("[SE-FHM] No eligible calendar months were requested")
        requested_set = set(requested)
        published_boundary = _publication_boundary(effective_today)
        provisional_months = {
            month_key
            for month_key in requested_set
            if month_key > published_boundary
        }
        years = sorted({year for year, _ in requested})
        requested_codes = {
            _norm_text(code).casefold() for code in disease_codes or [] if _norm_text(code)
        }

        pages = self.discover_disease_pages()
        if requested_codes:
            pages = [page for page in pages if page.code.casefold() in requested_codes]
        if not pages:
            raise RuntimeError("[SE-FHM] No matching statistics pages discovered")

        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        all_rows: list[dict[str, str]] = []
        diseases_with_rows: set[str] = set()
        pages_inspected = 0
        csv_pages = 0
        html_fallback_pages = 0
        latest_source_update: Optional[date] = None

        # Probe the newest requested year first.  The FHM index mixes SmiNet
        # diseases with unrelated public-health topics; only pages that expose
        # a monthly SmiNet table in the probe year are requested for additional
        # historical years.  This avoids candidates × years request growth.
        probe_year = max(years)
        ordered_years = [probe_year, *(year for year in years if year != probe_year)]
        for page in pages:
            recognized_sminet_page = False
            for year in ordered_years:
                if year != probe_year and not recognized_sminet_page:
                    continue
                pages_inspected += 1
                try:
                    html, page_url = self._fetch_page_html(page, year)
                except Exception as exc:
                    logger.warning(
                        f"[SE-FHM] Disease page failed | code={page.code} year={year} error={exc}"
                    )
                    continue

                disease_label, csv_url, source_updated_at = self._page_metadata(
                    html,
                    page=page,
                    year=year,
                    page_url=page_url,
                )
                html_totals = parse_monthly_html(html, year)
                if not html_totals:
                    # The index also contains non-infectious statistics pages.
                    continue
                recognized_sminet_page = True

                csv_content: Optional[bytes] = None
                totals: dict[int, int] = {}
                retrieval_method = "html_fallback"
                if csv_url:
                    csv_host = (urlparse(csv_url).hostname or "").casefold()
                    if csv_host not in self._csv_unavailable_hosts:
                        try:
                            csv_content = self._fetch_csv_bytes(csv_url)
                            totals = parse_monthly_csv(csv_content)
                            if totals:
                                retrieval_method = "machine_csv"
                                csv_pages += 1
                        except Exception as exc:
                            logger.warning(
                                f"[SE-FHM] Machine CSV unavailable; using HTML table | "
                                f"code={page.code} year={year} error={exc}"
                            )

                if not totals:
                    totals = html_totals
                    html_fallback_pages += 1

                self._save_raw_artifact(
                    page,
                    year,
                    html=html,
                    csv_content=csv_content,
                )
                rows = self._normalized_rows(
                    page=page,
                    disease_label=disease_label,
                    year=year,
                    totals=totals,
                    requested_months=requested_set,
                    provisional_months=provisional_months,
                    page_url=page_url,
                    csv_url=csv_url,
                    source_updated_at=source_updated_at,
                    retrieved_at=retrieved_at,
                    retrieval_method=retrieval_method,
                )
                if rows:
                    all_rows.extend(rows)
                    diseases_with_rows.add(page.code)
                    if source_updated_at and (
                        latest_source_update is None
                        or source_updated_at > latest_source_update
                    ):
                        latest_source_update = source_updated_at

        if not all_rows:
            raise RuntimeError(
                "[SE-FHM] No national monthly rows parsed from eligible source pages"
            )

        rows_by_month: dict[tuple[int, int], list[dict[str, str]]] = {}
        for row in all_rows:
            report_date = date.fromisoformat(row["Date"])
            rows_by_month.setdefault((report_date.year, report_date.month), []).append(row)
        placeholder_months_omitted = {
            month_key
            for month_key in provisional_months
            if not any(int(row["Cases"]) > 0 for row in rows_by_month.get(month_key, []))
        }
        if placeholder_months_omitted:
            all_rows = [
                row
                for row in all_rows
                if (
                    date.fromisoformat(row["Date"]).year,
                    date.fromisoformat(row["Date"]).month,
                )
                not in placeholder_months_omitted
            ]
            omitted_text = ",".join(
                f"{year:04d}-{month:02d}"
                for year, month in sorted(placeholder_months_omitted)
            )
            logger.info(
                f"[SE-FHM] Omitted provisional all-zero placeholder month(s) | "
                f"months={omitted_text}"
            )

        diseases_with_rows = {row["DiseaseCode"] for row in all_rows}
        all_rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"], row["DiseaseCode"]))
        self._write_output(Path(output_csv), all_rows)
        latest_date = max(
            (date.fromisoformat(row["Date"]) for row in all_rows),
            default=None,
        )
        logger.info(
            f"[SE-FHM] CSV written | path={output_csv} rows={len(all_rows)} "
            f"diseases={len(diseases_with_rows)} latest={latest_date} "
            f"csv_pages={csv_pages} html_fallback_pages={html_fallback_pages}"
        )
        return SEFetchSummary(
            row_count=len(all_rows),
            latest_date=latest_date,
            diseases_fetched=len(diseases_with_rows),
            pages_inspected=pages_inspected,
            csv_pages=csv_pages,
            html_fallback_pages=html_fallback_pages,
            source_url=self.index_url,
            latest_source_update=latest_source_update,
            placeholder_months_omitted=tuple(sorted(placeholder_months_omitted)),
        )

    async def crawl(self, **kwargs: Any) -> list[CrawlerResult]:
        output_csv = Path(
            kwargs.get("output_csv") or "data/current/se/sweden_sminet_monthly.csv"
        )
        summary = self.crawl_monthly_national(
            output_csv,
            months=kwargs.get("months"),
            disease_codes=kwargs.get("disease_codes"),
            today=kwargs.get("today"),
            include_current_month=bool(kwargs.get("include_current_month", False)),
        )
        return [
            CrawlerResult(
                title="Sweden FHM SmiNet national monthly statistics",
                url=self.index_url,
                date=datetime.now(timezone.utc),
                metadata={
                    "source": SOURCE_SCOPE,
                    "ontology_source_id": ONTOLOGY_SOURCE_ID,
                    "country_code": "SE",
                    "row_count": summary.row_count,
                    "latest_date": summary.latest_date.isoformat()
                    if summary.latest_date
                    else None,
                    "diseases_fetched": summary.diseases_fetched,
                    "public_release_enabled": True,
                },
            )
        ]

    def parse(self, response: Any) -> list[CrawlerResult]:
        """BaseCrawler contract; page parsing needs an explicitly selected year."""

        return []

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()
        self.session.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


__all__ = [
    "DEFAULT_INDEX_URL",
    "DEFAULT_SOURCE_NAME",
    "FHM_PUBLICATION_DAY",
    "NATIONAL_GEOGRAPHY_KEY",
    "ONTOLOGY_SOURCE_ID",
    "SEDiseasePage",
    "SEFetchSummary",
    "SOURCE_SCOPE",
    "SwedenSmiNetCrawler",
    "closed_months",
    "parse_monthly_csv",
    "parse_monthly_html",
]
