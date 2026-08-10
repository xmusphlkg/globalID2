"""Ireland HPSC historical weekly-report archive crawler.

Lenus preserves a sparse catalogue of HPSC's provisional national weekly
infectious-disease reports.  Each PDF contains both current-week counts and
year-to-date totals.  This adapter deliberately extracts only the source's
``Week Ending`` column; it never derives weekly values from cumulative data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from urllib.parse import unquote, urlparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pdfplumber

from .base import BaseCrawler, CrawlerResult
from .ie import IEContractError, IEWeek, stable_disease_code

DEFAULT_ARCHIVE_SOURCE_NAME = "Ireland HPSC Weekly Infectious Disease Report Archive"
DEFAULT_ARCHIVE_SOURCE_SCOPE = "hpsc_weekly_archive"
DEFAULT_ARCHIVE_START_YEAR = 2015
DEFAULT_ARCHIVE_END = (2021, 29)
DEFAULT_LENUS_DISCOVERY_URL = (
    "https://www.lenus.ie/server/api/discover/search/objects"
)
DEFAULT_LENUS_PORTAL_URL = "https://www.lenus.ie/"
DEFAULT_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_CONTRACT_VERSION = "hpsc-lenus-weekly-pdf-v1-observed-2026-08"
CC_BY_4_URI = "https://creativecommons.org/licenses/by/4.0/"

CATALOGUE_QUERIES = (
    '"Weekly Infectious Disease Report"',
    '"Statutory Notifications of Infectious Diseases"',
)

_TITLE_PREFIXES = (
    "hpsc - weekly infectious disease report",
    "statutory notifications of infectious diseases reported in ireland",
)
_PERIOD_RE = re.compile(
    r"Week\s*(?P<week>\d{1,2})\s*,\s*(?P<year>20\d{2}).*?"
    r"Notification\s+Period\s*:\s*(?P<start>\d{2}/\d{2}/\d{4})\s*[-–]\s*"
    r"(?P<end>\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

ARCHIVE_CSV_FIELDNAMES = [
    "Date", "RawDiseaseLabel", "DiseaseCode", "Year", "Week", "YearWeek",
    "PeriodStart", "PeriodEnd", "PeriodType", "Cases", "Deaths",
    "ValueStatus", "ReportingArea", "GeographyKey", "DatasetStatus",
    "AuthoritativeRevision", "UpdateMode", "Source", "SourceScope",
    "SourceURL", "PortalURL", "RetrievedAt", "SourceUpdatedAt",
    "SourceContract", "SourceReport", "LenusItemId", "LenusHandle",
    "LenusBitstreamId", "RawArtifact", "RawSHA256", "License",
    "LicenseURI", "PublicReleaseEnabled", "LicenseReviewStatus",
]


def _text(value: object) -> str:
    return " ".join(
        str(value or "").replace("\ufeff", "").replace("\xa0", " ").split()
    ).strip()


@dataclass(frozen=True, order=True)
class IEWeeklyArchiveReport:
    year: int
    week: int
    period_start: date
    period_end: date
    item_id: str
    handle: str
    title: str
    rights_uri: str
    source_url: str = ""
    download_url: str = ""
    archive_provider: str = "lenus"

    @property
    def monday(self) -> date:
        # The report label did not always follow ISO numbering.  In 2015, for
        # example, HPSC Week 17 covered 26 April--2 May (ISO week 18).  The
        # observed notification period is therefore the authoritative time
        # axis, while ``year``/``week`` retain the source report identity.
        return self.period_start + timedelta(days=1)

    @property
    def iso_period(self) -> Tuple[int, int]:
        iso = self.monday.isocalendar()
        return iso.year, iso.week

    @property
    def item_url(self) -> str:
        return self.source_url or (
            f"https://hdl.handle.net/{self.handle}" if self.handle else self.download_url
        )


@dataclass(frozen=True)
class IEWeeklyArchiveSummary:
    row_count: int
    latest_date: Optional[date]
    periods_fetched: Tuple[Tuple[int, int], ...]
    catalogue_periods: Tuple[Tuple[int, int], ...]
    missing_periods: Tuple[Tuple[int, int], ...]
    diseases_catalogued: int
    coverage_path: Path


def parse_catalogue_item(item: Mapping[str, object]) -> Optional[IEWeeklyArchiveReport]:
    """Parse one DSpace item, rejecting unrelated disease-specific reports."""

    title = _text(item.get("name"))
    if not title.casefold().startswith(_TITLE_PREFIXES):
        return None
    match = _PERIOD_RE.search(title)
    if match is None:
        # Some later Lenus records omit the notification period in catalogue
        # metadata. They are outside this adapter's pre-NDH boundary and do
        # not justify failing discovery of otherwise usable historical items.
        return None
    year, week = int(match.group("year")), int(match.group("week"))
    period_start = datetime.strptime(match.group("start"), "%d/%m/%Y").date()
    period_end = datetime.strptime(match.group("end"), "%d/%m/%Y").date()
    source_week = IEWeek.from_parts(year, week)
    if period_start.weekday() != 6 or period_end.weekday() != 5:
        raise IEContractError(f"Lenus report period is not Sunday-Saturday: {title!r}")
    if period_end - period_start != timedelta(days=6):
        raise IEContractError(f"Lenus report period is not seven days: {title!r}")
    if abs((period_start - (source_week.monday - timedelta(days=1))).days) > 7:
        raise IEContractError(f"Lenus report period disagrees with ISO week: {title!r}")

    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    rights = {
        _text(value.get("value"))
        for value in metadata.get("dc.rights.uri", [])
        if isinstance(value, Mapping) and _text(value.get("value"))
    }
    normalized_rights = {value.rstrip("/").casefold() for value in rights}
    rights_uri = (
        CC_BY_4_URI
        if CC_BY_4_URI.rstrip("/").casefold() in normalized_rights
        else next(iter(sorted(rights)), "")
    )
    item_id = _text(item.get("id") or item.get("uuid"))
    handle = _text(item.get("handle"))
    if not item_id or not handle:
        raise IEContractError(f"Lenus report lacks item identity: {title!r}")
    return IEWeeklyArchiveReport(
        year, week, period_start, period_end, item_id, handle, title, rights_uri,
        source_url=f"https://hdl.handle.net/{handle}",
    )


def report_identity_from_pdf(
    content: bytes,
    *,
    item_id: str,
    source_url: str,
    download_url: str,
) -> Optional[IEWeeklyArchiveReport]:
    """Identify a general weekly report recovered from a web archive snapshot."""

    if not content.startswith(b"%PDF"):
        return None
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            heading = "\n".join(
                page.extract_text() or "" for page in document.pages[:2]
            )
    except Exception:
        return None
    if (
        "Weekly Infectious Disease Report" not in heading
        or "Statutory Notifications of Infectious Diseases" not in heading
    ):
        return None
    match = _PERIOD_RE.search(_text(heading))
    if match is None:
        return None
    year, week = int(match.group("year")), int(match.group("week"))
    period_start = datetime.strptime(match.group("start"), "%d/%m/%Y").date()
    period_end = datetime.strptime(match.group("end"), "%d/%m/%Y").date()
    source_week = IEWeek.from_parts(year, week)
    if (
        period_start.weekday() != 6
        or period_end.weekday() != 5
        or period_end - period_start != timedelta(days=6)
        or abs((period_start - (source_week.monday - timedelta(days=1))).days) > 7
    ):
        raise IEContractError(
            f"Archived HPSC PDF period disagrees with ISO week: {source_url}"
        )
    return IEWeeklyArchiveReport(
        year=year,
        week=week,
        period_start=period_start,
        period_end=period_end,
        item_id=item_id,
        handle="",
        title=f"HPSC Weekly Infectious Disease Report: Week {week}, {year}",
        rights_uri="",
        source_url=source_url,
        download_url=download_url,
        archive_provider="internet_archive",
    )


def _parse_count(value: object, *, label: str) -> int:
    raw = _text(value).replace(",", "")
    if not re.fullmatch(r"\d+", raw):
        raise IEContractError(
            f"HPSC archive PDF has invalid current-week count for {label!r}: {value!r}"
        )
    return int(raw)


def _table_date_matches(value: object, expected: date) -> bool:
    raw = _text(value)
    for pattern in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            if datetime.strptime(raw, pattern).date() == expected:
                return True
        except ValueError:
            continue
    return False


def parse_weekly_archive_pdf(
    content: bytes,
    *,
    report: IEWeeklyArchiveReport,
    retrieved_at: str,
    bitstream_id: str = "",
    raw_artifact: str = "",
) -> List[Dict[str, str]]:
    """Extract Table 1's national current-week column from an archive PDF."""

    if not content.startswith(b"%PDF"):
        raise IEContractError(f"Lenus item {report.item_id} is not a PDF")
    digest = hashlib.sha256(content).hexdigest()
    rows: List[Dict[str, str]] = []
    seen: set[str] = set()
    table_total: Optional[int] = None

    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            document_text = "\n".join(page.extract_text() or "" for page in document.pages)
            if "Data are Provisional" not in document_text:
                raise IEContractError(
                    f"HPSC archive report {report.item_id} lost its provisional marker"
                )
            for page in document.pages:
                for table in page.extract_tables() or []:
                    if len(table) < 3:
                        continue
                    header = [_text(cell) for cell in table[0]]
                    if (
                        len(header) != 5
                        or header[0].casefold() != "infectious disease"
                        or header[1].casefold() != "week ending"
                        or "increase" not in header[4].casefold()
                    ):
                        continue
                    subheader = [_text(cell) for cell in table[1]]
                    if len(subheader) < 2 or not _table_date_matches(subheader[1], report.period_end):
                        raise IEContractError(
                            f"HPSC archive Table 1 week ending disagrees with catalogue for {report.item_id}"
                        )
                    for raw_row in table[2:]:
                        padded = list(raw_row) + [None] * (5 - len(raw_row))
                        label = _text(padded[0])
                        if not label:
                            continue
                        if label.casefold() == "total":
                            candidate = _parse_count(padded[1], label="Total")
                            if table_total is not None and table_total != candidate:
                                raise IEContractError(
                                    f"Conflicting HPSC archive totals for {report.item_id}"
                                )
                            table_total = candidate
                            continue
                        code = stable_disease_code(label)
                        if code in seen:
                            raise IEContractError(
                                f"Duplicate HPSC archive disease in {report.item_id}: {label!r}"
                            )
                        seen.add(code)
                        cases = _parse_count(padded[1], label=label)
                        iso_year, iso_week = report.iso_period
                        rows.append(
                            {
                                "Date": report.monday.isoformat(),
                                "RawDiseaseLabel": label,
                                "DiseaseCode": code,
                                "Year": str(iso_year),
                                "Week": str(iso_week),
                                "YearWeek": f"{iso_year:04d} W{iso_week:02d}",
                                "PeriodStart": report.period_start.isoformat(),
                                "PeriodEnd": report.period_end.isoformat(),
                                "PeriodType": "weekly",
                                "Cases": str(cases),
                                "Deaths": "",
                                "ValueStatus": "zero" if cases == 0 else "reported",
                                "ReportingArea": "Ireland national",
                                "GeographyKey": "country:IE:national",
                                "DatasetStatus": "historical_provisional_snapshot",
                                "AuthoritativeRevision": "false",
                                "UpdateMode": "immutable_published_snapshot",
                                "Source": DEFAULT_ARCHIVE_SOURCE_NAME,
                                "SourceScope": DEFAULT_ARCHIVE_SOURCE_SCOPE,
                                "SourceURL": report.item_url,
                                "PortalURL": DEFAULT_LENUS_PORTAL_URL,
                                "RetrievedAt": retrieved_at,
                                "SourceUpdatedAt": "",
                                "SourceContract": ARCHIVE_CONTRACT_VERSION,
                                "SourceReport": f"{report.year:04d}-W{report.week:02d}",
                                "LenusItemId": report.item_id,
                                "LenusHandle": report.handle,
                                "LenusBitstreamId": bitstream_id,
                                "RawArtifact": raw_artifact,
                                "RawSHA256": digest,
                                "License": "CC BY 4.0" if report.rights_uri.rstrip("/") == CC_BY_4_URI.rstrip("/") else "",
                                "LicenseURI": report.rights_uri,
                                "PublicReleaseEnabled": "false",
                                "LicenseReviewStatus": (
                                    "catalogue_declares_cc_by_4_0"
                                    if report.rights_uri.rstrip("/") == CC_BY_4_URI.rstrip("/")
                                    else "not_checked_for_ingestion"
                                ),
                            }
                        )
    except IEContractError:
        raise
    except Exception as exc:
        raise IEContractError(
            f"Could not parse HPSC archive PDF {report.item_id}"
        ) from exc

    if len(rows) < 68:
        raise IEContractError(
            f"HPSC archive PDF {report.item_id} yielded only {len(rows)} diseases"
        )
    if table_total is None:
        raise IEContractError(f"HPSC archive PDF {report.item_id} has no Table 1 total")
    if sum(int(row["Cases"]) for row in rows) != table_total:
        raise IEContractError(
            f"HPSC archive Table 1 total mismatch for {report.item_id}"
        )
    return sorted(rows, key=lambda row: row["RawDiseaseLabel"])


def validate_archive_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    requested_periods: Optional[set[Tuple[int, int]]] = None,
) -> None:
    if not rows:
        raise IEContractError("HPSC weekly archive batch contains no rows")
    seen: set[Tuple[str, int, int]] = set()
    present: set[Tuple[int, int]] = set()
    for index, row in enumerate(rows):
        try:
            year, week = int(_text(row.get("Year"))), int(_text(row.get("Week")))
            monday = date.fromisoformat(_text(row.get("Date")))
            cases = int(_text(row.get("Cases")))
            period_start = date.fromisoformat(_text(row.get("PeriodStart")))
            period_end = date.fromisoformat(_text(row.get("PeriodEnd")))
        except (TypeError, ValueError) as exc:
            raise IEContractError(f"Invalid HPSC archive row {index}") from exc
        label, code = _text(row.get("RawDiseaseLabel")), _text(row.get("DiseaseCode"))
        if (
            monday != date.fromisocalendar(year, week, 1)
            or monday != period_start + timedelta(days=1)
            or period_end - period_start != timedelta(days=6)
        ):
            raise IEContractError(f"Invalid HPSC archive week identity at row {index}")
        if code != stable_disease_code(label) or cases < 0:
            raise IEContractError(f"Invalid HPSC archive disease/count at row {index}")
        identity = (code, year, week)
        if identity in seen:
            raise IEContractError(f"Duplicate HPSC archive row: {identity}")
        seen.add(identity)
        source_report = _text(row.get("SourceReport"))
        source_match = re.fullmatch(r"(20\d{2})-W(\d{2})", source_report)
        present.add(
            (int(source_match.group(1)), int(source_match.group(2)))
            if source_match is not None
            else (year, week)
        )
    if requested_periods:
        missing = requested_periods - present
        if missing:
            raise IEContractError(
                "HPSC archive batch is missing requested report(s): "
                + ", ".join(f"{year}-W{week:02d}" for year, week in sorted(missing))
            )


class IrelandHPSCWeeklyArchiveCrawler(BaseCrawler):
    """Discover, license-check, download, and parse archived weekly reports."""

    SOURCE_URL = DEFAULT_LENUS_DISCOVERY_URL

    def __init__(
        self,
        *,
        save_raw: bool = False,
        raw_dir: Optional[Path] = None,
        timeout: int = 60,
        max_retries: int = 3,
        delay: float = 0.1,
    ) -> None:
        super().__init__(
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; IE-HPSC-Weekly-Archive)",
            timeout=timeout,
            max_retries=max_retries,
            delay=delay,
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/ie/weekly_archive")
        self._snapshot_cache: Dict[str, bytes] = {}

    def discover_wayback_catalogue(self) -> Tuple[IEWeeklyArchiveReport, ...]:
        """Recover official HPSC PDFs that are absent from the Lenus catalogue."""

        payload = self.get(
            DEFAULT_WAYBACK_CDX_URL,
            params={
                "url": "www.hpsc.ie/notifiablediseases/weeklyidreports/*",
                "from": str(DEFAULT_ARCHIVE_START_YEAR),
                "to": str(DEFAULT_ARCHIVE_END[0]),
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype,digest",
                "filter": ["statuscode:200", "mimetype:application/pdf"],
                # The legacy HPSC site reused generic download URLs.  URL-key
                # collapse loses historical bodies; digest collapse retains
                # every distinct official PDF snapshot.
                "collapse": "digest",
            },
        ).json()
        if not isinstance(payload, list) or not payload:
            raise IEContractError("Internet Archive CDX response contract changed")
        reports: Dict[Tuple[int, int], IEWeeklyArchiveReport] = {}
        for record in payload[1:]:
            if not isinstance(record, list) or len(record) < 5:
                continue
            timestamp, original, _, _, digest = map(_text, record[:5])
            filename = unquote(urlparse(original).path.rsplit("/", 1)[-1]).casefold()
            download_url = f"https://web.archive.org/web/{timestamp}id_/{original}"
            # On the legacy CMS this exact slot was the general notification
            # report; neighbouring generic slots were outbreak and C. difficile
            # reports and must not be mixed into this source.
            if filename == "file,1425,en.pdf":
                content = self.get(download_url).content
                report = report_identity_from_pdf(
                    content,
                    item_id=f"wayback:{digest or hashlib.sha256(original.encode()).hexdigest()}",
                    source_url=original,
                    download_url=download_url,
                )
                if report is not None:
                    if (report.year, report.week) < (DEFAULT_ARCHIVE_START_YEAR, 1):
                        continue
                    self._snapshot_cache[report.item_id] = content
                    reports[(report.year, report.week)] = report
                continue
            if "outbreak" in filename or "id" not in filename or "week" not in filename:
                continue
            filename_match = re.search(
                r"(?:weekly\s+)?id(?:\s+report)?\s+week\s+(\d{1,2})(?:\s+(20\d{2}))?",
                filename,
                re.IGNORECASE,
            )
            if filename_match is None:
                continue
            week = int(filename_match.group(1))
            year = int(filename_match.group(2) or timestamp[:4])
            try:
                monday = date.fromisocalendar(year, week, 1)
            except ValueError:
                continue
            if (year, week) < (DEFAULT_ARCHIVE_START_YEAR, 1) or (year, week) > DEFAULT_ARCHIVE_END:
                continue
            report = IEWeeklyArchiveReport(
                year=year,
                week=week,
                period_start=monday - timedelta(days=1),
                period_end=monday + timedelta(days=5),
                item_id=f"wayback:{digest or hashlib.sha256(original.encode()).hexdigest()}",
                handle="",
                title=f"HPSC Weekly Infectious Disease Report: Week {week}, {year}",
                rights_uri="",
                source_url=original,
                download_url=download_url,
                archive_provider="internet_archive",
            )
            reports[(report.year, report.week)] = report
        return tuple(sorted(reports.values()))

    def discover_catalogue(self) -> Tuple[IEWeeklyArchiveReport, ...]:
        items: Dict[str, Mapping[str, object]] = {}
        for query in CATALOGUE_QUERIES:
            page = 0
            while True:
                payload = self.get(
                    DEFAULT_LENUS_DISCOVERY_URL,
                    params={"query": query, "page": page, "size": 100},
                ).json()
                try:
                    result = payload["_embedded"]["searchResult"]
                    objects = result["_embedded"]["objects"]
                    page_info = result["page"]
                except (KeyError, TypeError) as exc:
                    raise IEContractError("Lenus discovery response contract changed") from exc
                for wrapper in objects:
                    item = wrapper.get("_embedded", {}).get("indexableObject", {})
                    item_id = _text(item.get("id") or item.get("uuid"))
                    if item_id:
                        items[item_id] = item
                page += 1
                if page >= int(page_info["totalPages"]):
                    break

        reports: Dict[Tuple[int, int], IEWeeklyArchiveReport] = {}
        for item in items.values():
            report = parse_catalogue_item(item)
            if report is None:
                continue
            key = (report.year, report.week)
            previous = reports.get(key)
            if previous is not None and previous.item_id != report.item_id:
                raise IEContractError(
                    f"Lenus has duplicate general weekly reports for {report.year}-W{report.week:02d}"
                )
            reports[key] = report
        # Prefer Lenus when both archives contain the same official PDF; its
        # item record adds a stable handle and richer metadata. Wayback fills
        # weeks that Lenus does not catalogue.
        for report in self.discover_wayback_catalogue():
            reports.setdefault((report.year, report.week), report)
        if not reports:
            raise IEContractError("Lenus discovery returned no HPSC weekly reports")
        return tuple(sorted(reports.values()))

    def _fetch_pdf(self, report: IEWeeklyArchiveReport) -> Tuple[str, bytes]:
        if report.archive_provider == "internet_archive":
            content = self._snapshot_cache.get(report.item_id)
            if content is None:
                content = self.get(report.download_url).content
            return report.item_id.removeprefix("wayback:"), content
        bundles = self.get(
            f"https://www.lenus.ie/server/api/core/items/{report.item_id}/bundles"
        ).json().get("_embedded", {}).get("bundles", [])
        originals = [bundle for bundle in bundles if _text(bundle.get("name")) == "ORIGINAL"]
        if len(originals) != 1:
            raise IEContractError(f"Lenus item {report.item_id} lacks one ORIGINAL bundle")
        bitstreams_url = originals[0].get("_links", {}).get("bitstreams", {}).get("href")
        bitstreams = self.get(bitstreams_url).json().get("_embedded", {}).get("bitstreams", [])
        pdfs = [entry for entry in bitstreams if _text(entry.get("name")).casefold().endswith(".pdf")]
        if len(pdfs) != 1:
            raise IEContractError(f"Lenus item {report.item_id} lacks one PDF bitstream")
        bitstream = pdfs[0]
        content_url = bitstream.get("_links", {}).get("content", {}).get("href")
        content = self.get(content_url).content
        return _text(bitstream.get("id") or bitstream.get("uuid")), content

    def _archive_pdf(self, report: IEWeeklyArchiveReport, content: bytes) -> str:
        if not self.save_raw:
            return ""
        year_dir = self.raw_dir / str(report.year)
        year_dir.mkdir(parents=True, exist_ok=True)
        path = year_dir / f"hpsc_weekly_{report.year}_W{report.week:02d}.pdf"
        handle = tempfile.NamedTemporaryFile(
            "wb", dir=year_dir, prefix=f".{path.name}.", suffix=".tmp", delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(content)
            os.replace(temporary, path)
            path.chmod(0o644)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return str(path)

    @staticmethod
    def _read_rows(path: Path) -> List[Dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=ARCHIVE_CSV_FIELDNAMES)
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in ARCHIVE_CSV_FIELDNAMES})
            os.replace(temporary, path)
            path.chmod(0o644)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_coverage(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
            path.chmod(0o644)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def crawl_weekly_archive(
        self,
        output_csv: Path,
        *,
        periods: Optional[Iterable[Tuple[int, int]]] = None,
        start_year: int = DEFAULT_ARCHIVE_START_YEAR,
        catalogue: Optional[Sequence[IEWeeklyArchiveReport]] = None,
    ) -> IEWeeklyArchiveSummary:
        catalogue = tuple(
            report for report in (catalogue or self.discover_catalogue())
            if report.year >= start_year
            and (report.year, report.week) <= DEFAULT_ARCHIVE_END
        )
        catalogue_by_period = {(report.year, report.week): report for report in catalogue}
        requested = set(periods) if periods is not None else set(catalogue_by_period)
        unavailable = requested - set(catalogue_by_period)
        if unavailable:
            raise IEContractError(
                "Requested HPSC archive reports are not catalogued: "
                + ", ".join(f"{y}-W{w:02d}" for y, w in sorted(unavailable))
            )
        if not requested:
            raise IEContractError("No HPSC weekly archive reports are available in range")

        fetched: List[Dict[str, str]] = []
        for key in sorted(requested):
            report = catalogue_by_period[key]
            bitstream_id, content = self._fetch_pdf(report)
            artifact = self._archive_pdf(report, content)
            fetched.extend(
                parse_weekly_archive_pdf(
                    content, report=report,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    bitstream_id=bitstream_id, raw_artifact=artifact,
                )
            )
        validate_archive_rows(fetched, requested_periods=requested)

        existing = self._read_rows(Path(output_csv))
        requested_labels = {f"{year:04d}-W{week:02d}" for year, week in requested}
        preserved = [
            row for row in existing
            if _text(row.get("SourceReport")) not in requested_labels
        ]
        combined = preserved + fetched
        combined.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        self._write_rows(Path(output_csv), combined)

        expected: set[Tuple[int, int]] = set()
        cursor = date.fromisocalendar(start_year, 1, 1)
        end = date.fromisocalendar(*DEFAULT_ARCHIVE_END, 1)
        while cursor <= end:
            iso = cursor.isocalendar()
            expected.add((iso.year, iso.week))
            cursor += timedelta(days=7)
        missing = tuple(sorted(expected - set(catalogue_by_period)))
        coverage_path = Path(output_csv).with_suffix(".coverage.json")
        self._write_coverage(
            coverage_path,
            {
                "source_scope": DEFAULT_ARCHIVE_SOURCE_SCOPE,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "supported_boundary": {"start_year": start_year, "end": "2021-W29"},
                "catalogued_reports": [f"{y}-W{w:02d}" for y, w in sorted(catalogue_by_period)],
                "missing_reports": [f"{y}-W{w:02d}" for y, w in missing],
                "missing_week_semantics": "not_archived_not_zero",
                "license_gate": "skipped for ingestion; metadata retained; public release disabled",
            },
        )
        return IEWeeklyArchiveSummary(
            row_count=len(fetched),
            latest_date=max(
                (catalogue_by_period[key].monday for key in requested), default=None
            ),
            periods_fetched=tuple(sorted(requested)),
            catalogue_periods=tuple(sorted(catalogue_by_period)),
            missing_periods=missing,
            diseases_catalogued=len({row["DiseaseCode"] for row in fetched}),
            coverage_path=coverage_path,
        )

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        output = Path(kwargs.pop("output_csv", "data/current/ie/ireland_hpsc_weekly_archive.csv"))
        self.crawl_weekly_archive(output, **kwargs)
        return [
            CrawlerResult(
                title=f"{row['RawDiseaseLabel']} - {row['YearWeek']}",
                url=row["SourceURL"],
                date=datetime.fromisoformat(row["Date"]),
                metadata={"country_code": "IE", "source_scope": DEFAULT_ARCHIVE_SOURCE_SCOPE},
                raw_data=row,
            )
            for row in self._read_rows(output)
        ]

    def parse(self, response) -> List[CrawlerResult]:
        raise NotImplementedError("Use crawl_weekly_archive with the Lenus catalogue")


__all__ = [
    "ARCHIVE_CONTRACT_VERSION", "ARCHIVE_CSV_FIELDNAMES", "CC_BY_4_URI",
    "DEFAULT_ARCHIVE_END", "DEFAULT_ARCHIVE_SOURCE_NAME",
    "DEFAULT_ARCHIVE_SOURCE_SCOPE", "DEFAULT_ARCHIVE_START_YEAR",
    "DEFAULT_LENUS_DISCOVERY_URL", "IEWeeklyArchiveReport",
    "IEWeeklyArchiveSummary", "IrelandHPSCWeeklyArchiveCrawler",
    "parse_catalogue_item", "parse_weekly_archive_pdf", "report_identity_from_pdf",
    "validate_archive_rows",
]
