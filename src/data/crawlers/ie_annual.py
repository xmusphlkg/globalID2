"""Ireland HPSC modern annual notification-history crawler.

HPSC publishes consolidated national annual tables for 2004 onward.  This
adapter intentionally stops at 2020: the National Notifiable Disease Hub is
the authoritative weekly source from 2021 W30, while pre-2004 reports use the
older notification regime and materially different source categories.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pdfplumber

from src.core import get_logger

from .base import BaseCrawler, CrawlerResult
from .ie import IEContractError, stable_disease_code

logger = get_logger(__name__)

DEFAULT_ANNUAL_SOURCE_NAME = "Ireland HPSC Annual Infectious Disease Statistics"
DEFAULT_ANNUAL_SOURCE_SCOPE = "hpsc_annual"
DEFAULT_ANNUAL_INDEX_URL = (
    "https://www.hpsc.ie/notifiablediseases/annualidstatistics/"
)
DEFAULT_ANNUAL_START_YEAR = 2004
DEFAULT_ANNUAL_END_YEAR = 2020
ANNUAL_CONTRACT_VERSION = "hpsc-annual-pdf-v1-observed-2026-08"


@dataclass(frozen=True)
class IEAnnualReportSpec:
    report_id: str
    url: str
    years: Tuple[int, ...]


ANNUAL_REPORTS: Tuple[IEAnnualReportSpec, ...] = (
    IEAnnualReportSpec(
        "hpsc_annual_2004_2014",
        "https://www.hpsc.ie/notifiablediseases/annualidstatistics/"
        "File%2C2393%2Cen.pdf",
        tuple(range(2004, 2015)),
    ),
    IEAnnualReportSpec(
        "hpsc_annual_2015_2018",
        "https://www.hpsc.ie/notifiablediseases/annualidstatistics/"
        "Annual_ID_Summary_Report_for_HPSC_Web_2015-2018.pdf",
        (2015, 2016, 2017),
    ),
    IEAnnualReportSpec(
        "hpsc_annual_2018_2022",
        "https://www.hpsc.ie/notifiablediseases/annualidstatistics/"
        "Annual_ID_Summary_Report_for_HPSC_Web_v8.0-2018-2022-21032023.pdf",
        (2018, 2019),
    ),
    IEAnnualReportSpec(
        "hpsc_annual_2020_2025",
        "https://www.hpsc.ie/notifiablediseases/annualidstatistics/"
        "Annual_ID_Summary_Report_for_HPSC_Web_v12.0-2020-2025-07052026.pdf",
        (2020,),
    ),
)

ANNUAL_CSV_FIELDNAMES = [
    "Date",
    "RawDiseaseLabel",
    "DiseaseCode",
    "Year",
    "Week",
    "YearWeek",
    "PeriodType",
    "Cases",
    "Deaths",
    "ValueStatus",
    "ReportingArea",
    "GeographyKey",
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
    "SourceReport",
    "RawArtifact",
    "RawSHA256",
    "PublicReleaseEnabled",
    "LicenseReviewStatus",
]


@dataclass(frozen=True)
class IEAnnualFetchSummary:
    row_count: int
    latest_date: Optional[date]
    years_fetched: Tuple[int, ...]
    diseases_catalogued: int
    source_url: str = DEFAULT_ANNUAL_INDEX_URL
    contract_version: str = ANNUAL_CONTRACT_VERSION


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").split()).strip()


def _parse_count(value: object, *, label: str, year: int) -> Optional[int]:
    raw = _text(value).replace(",", "")
    if raw.casefold() in {"na", "n/a", "not applicable"}:
        return None
    match = re.fullmatch(r"(\d+)(?:\s*[*^#§ǂ$]+)?", raw)
    if match is None:
        raise IEContractError(
            f"HPSC annual PDF has invalid count for {label!r} in {year}: {value!r}"
        )
    return int(match.group(1))


def parse_annual_pdf(
    content: bytes,
    *,
    report: IEAnnualReportSpec,
    retrieved_at: str,
    raw_artifact: str = "",
) -> List[Dict[str, str]]:
    """Extract the national all-classification table for selected years."""

    if not content.startswith(b"%PDF"):
        raise IEContractError(f"HPSC annual report {report.report_id} is not a PDF")
    digest = hashlib.sha256(content).hexdigest()
    selected = set(report.years)
    rows: List[Dict[str, str]] = []
    seen: set[Tuple[str, int]] = set()
    found_years: set[int] = set()

    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            for page in document.pages:
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    header = [_text(cell) for cell in table[0]]
                    year_columns = {
                        int(cell): index
                        for index, cell in enumerate(header)
                        if re.fullmatch(r"20\d{2}", cell)
                        and int(cell) in selected
                    }
                    if not year_columns:
                        continue
                    first_year_column = min(year_columns.values())
                    for raw_row in table[1:]:
                        padded = list(raw_row) + [None] * (len(header) - len(raw_row))
                        label = next(
                            (
                                _text(cell)
                                for cell in padded[:first_year_column]
                                if _text(cell)
                            ),
                            "",
                        )
                        if not label or label.casefold().startswith("table "):
                            continue
                        code = stable_disease_code(label)
                        for year, column in year_columns.items():
                            raw_value = padded[column] if column < len(padded) else None
                            if _text(raw_value) == "":
                                raise IEContractError(
                                    f"HPSC annual report {report.report_id} has a blank "
                                    f"national value for {label!r} in {year}"
                                )
                            cases = _parse_count(raw_value, label=label, year=year)
                            identity = (code, year)
                            if identity in seen:
                                raise IEContractError(
                                    f"Duplicate HPSC annual disease/year row: {identity}"
                                )
                            seen.add(identity)
                            found_years.add(year)
                            rows.append(
                                {
                                    "Date": f"{year:04d}-01-01",
                                    "RawDiseaseLabel": label,
                                    "DiseaseCode": code,
                                    "Year": str(year),
                                    "Week": "",
                                    "YearWeek": "",
                                    "PeriodType": "annual",
                                    "Cases": "" if cases is None else str(cases),
                                    "Deaths": "",
                                    "ValueStatus": (
                                        "not_applicable"
                                        if cases is None
                                        else "zero" if cases == 0 else "reported"
                                    ),
                                    "ReportingArea": "Ireland national",
                                    "GeographyKey": "country:IE:national",
                                    "DatasetStatus": "historical_final_revisable",
                                    "AuthoritativeRevision": "true",
                                    "UpdateMode": "authoritative_revision",
                                    "Source": DEFAULT_ANNUAL_SOURCE_NAME,
                                    "SourceScope": DEFAULT_ANNUAL_SOURCE_SCOPE,
                                    "SourceURL": report.url,
                                    "PortalURL": DEFAULT_ANNUAL_INDEX_URL,
                                    "RetrievedAt": retrieved_at,
                                    "SourceUpdatedAt": "",
                                    "SourceContract": ANNUAL_CONTRACT_VERSION,
                                    "SourceReport": report.report_id,
                                    "RawArtifact": raw_artifact,
                                    "RawSHA256": digest,
                                    "PublicReleaseEnabled": "false",
                                    "LicenseReviewStatus": "written_permission_required",
                                }
                            )
    except IEContractError:
        raise
    except Exception as exc:
        raise IEContractError(
            f"Could not parse HPSC annual report {report.report_id}"
        ) from exc

    missing_years = sorted(selected - found_years)
    if missing_years:
        raise IEContractError(
            f"HPSC annual report {report.report_id} is missing requested year(s): "
            + ", ".join(map(str, missing_years))
        )
    if not rows:
        raise IEContractError(f"HPSC annual report {report.report_id} has no rows")
    return sorted(rows, key=lambda row: (row["Date"], row["RawDiseaseLabel"]))


def validate_annual_rows(
    rows: Sequence[Mapping[str, object]], *, requested_years: Optional[set[int]] = None
) -> None:
    if not rows:
        raise IEContractError("HPSC annual batch contains no rows")
    seen: set[Tuple[str, int]] = set()
    present: set[int] = set()
    for index, row in enumerate(rows):
        try:
            year = int(_text(row.get("Year")))
            report_date = date.fromisoformat(_text(row.get("Date")))
            raw_cases = _text(row.get("Cases"))
            cases = int(raw_cases) if raw_cases else None
        except (TypeError, ValueError) as exc:
            raise IEContractError(f"Invalid HPSC annual row {index}") from exc
        label = _text(row.get("RawDiseaseLabel"))
        code = _text(row.get("DiseaseCode"))
        if report_date != date(year, 1, 1) or code != stable_disease_code(label):
            raise IEContractError(f"Invalid HPSC annual identity at row {index}")
        value_status = _text(row.get("ValueStatus"))
        if cases is None and value_status != "not_applicable":
            raise IEContractError(
                f"Blank HPSC annual count without not_applicable status at row {index}"
            )
        if cases is not None and cases < 0:
            raise IEContractError(f"Negative HPSC annual count at row {index}")
        identity = (code, year)
        if identity in seen:
            raise IEContractError(f"Duplicate HPSC annual row: {identity}")
        seen.add(identity)
        present.add(year)
    if requested_years:
        missing = sorted(requested_years - present)
        if missing:
            raise IEContractError(
                "HPSC annual batch is missing year(s): " + ", ".join(map(str, missing))
            )


class IrelandHPSCAnnualCrawler(BaseCrawler):
    """Fetch and merge the reviewed 2004–2020 annual PDF catalogue."""

    SOURCE_URL = DEFAULT_ANNUAL_INDEX_URL

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
            user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; IE-HPSC-Annual)",
            timeout=timeout,
            max_retries=max_retries,
            delay=delay,
        )
        self.save_raw = save_raw
        self.raw_dir = Path(raw_dir) if raw_dir else Path("data/raw/ie/annual")

    def _archive_pdf(self, report: IEAnnualReportSpec, content: bytes) -> str:
        if not self.save_raw:
            return ""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{report.report_id}.pdf"
        handle = tempfile.NamedTemporaryFile(
            "wb", dir=self.raw_dir, prefix=f".{path.name}.", suffix=".tmp", delete=False
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
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=ANNUAL_CSV_FIELDNAMES)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {field: row.get(field, "") for field in ANNUAL_CSV_FIELDNAMES}
                    )
            os.replace(temporary, path)
            path.chmod(0o644)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def crawl_annual_national(
        self,
        output_csv: Path,
        *,
        years: Optional[Iterable[int]] = None,
        start_year: int = DEFAULT_ANNUAL_START_YEAR,
        end_year: int = DEFAULT_ANNUAL_END_YEAR,
    ) -> IEAnnualFetchSummary:
        requested = {
            year
            for year in (years or range(start_year, end_year + 1))
            if DEFAULT_ANNUAL_START_YEAR <= int(year) <= DEFAULT_ANNUAL_END_YEAR
        }
        if not requested:
            raise ValueError("IE annual request contains no supported years")

        fetched: List[Dict[str, str]] = []
        covered: set[int] = set()
        for spec in ANNUAL_REPORTS:
            selected = tuple(year for year in spec.years if year in requested)
            if not selected:
                continue
            response = self.get(spec.url)
            content = response.content
            artifact = self._archive_pdf(spec, content)
            selected_spec = IEAnnualReportSpec(spec.report_id, spec.url, selected)
            fetched.extend(
                parse_annual_pdf(
                    content,
                    report=selected_spec,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    raw_artifact=artifact,
                )
            )
            covered.update(selected)

        if covered != requested:
            missing = sorted(requested - covered)
            raise IEContractError(
                "No reviewed HPSC annual report for year(s): "
                + ", ".join(map(str, missing))
            )
        validate_annual_rows(fetched, requested_years=requested)

        existing = self._read_rows(Path(output_csv))
        preserved = [
            row
            for row in existing
            if _text(row.get("Year")).isdigit()
            and int(_text(row.get("Year"))) not in requested
        ]
        combined = preserved + fetched
        combined.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        self._write_rows(Path(output_csv), combined)
        return IEAnnualFetchSummary(
            row_count=len(fetched),
            latest_date=date(max(requested), 1, 1),
            years_fetched=tuple(sorted(requested)),
            diseases_catalogued=len({row["DiseaseCode"] for row in fetched}),
        )

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        output = Path(
            kwargs.pop("output_csv", "data/current/ie/ireland_hpsc_annual.csv")
        )
        self.crawl_annual_national(output, **kwargs)
        return [
            CrawlerResult(
                title=f"{row['RawDiseaseLabel']} - {row['Year']}",
                url=row["SourceURL"],
                date=datetime.fromisoformat(row["Date"]),
                metadata={
                    "country_code": "IE",
                    "source": DEFAULT_ANNUAL_SOURCE_NAME,
                    "source_scope": DEFAULT_ANNUAL_SOURCE_SCOPE,
                },
                raw_data=row,
            )
            for row in self._read_rows(output)
        ]

    def parse(self, response) -> List[CrawlerResult]:
        raise NotImplementedError("Use crawl_annual_national with a report catalogue")


__all__ = [
    "ANNUAL_CONTRACT_VERSION",
    "ANNUAL_CSV_FIELDNAMES",
    "ANNUAL_REPORTS",
    "DEFAULT_ANNUAL_END_YEAR",
    "DEFAULT_ANNUAL_INDEX_URL",
    "DEFAULT_ANNUAL_SOURCE_NAME",
    "DEFAULT_ANNUAL_SOURCE_SCOPE",
    "DEFAULT_ANNUAL_START_YEAR",
    "IEAnnualFetchSummary",
    "IEAnnualReportSpec",
    "IrelandHPSCAnnualCrawler",
    "parse_annual_pdf",
    "validate_annual_rows",
]
