"""Austria AGES Radar monthly national notification crawler.

AGES publishes one Radar issue per month.  The issue page contains the
machine-readable table used here; raw CSV bytes and their issue URL are kept
so a later revision can be reproduced.  The table also contains cumulative
columns, which are deliberately ignored: only its single calendar-month
column is an observation.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseCrawler, CrawlerResult

DEFAULT_SOURCE_NAME = "Austria AGES Radar for Infectious Diseases"
DEFAULT_SCOPE = "ages_radar"
ONTOLOGY_SOURCE_ID = "SRC_AT_AGES_RADAR"
DEFAULT_LANDING_URL = "https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases"
HISTORY_START_YEAR = 2025
NATIONAL_GEOGRAPHY_KEY = "country:AT:national"

_MONTHS = {
    **{name.casefold(): number for number, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)},
    "jan": 1, "feb": 2, "mar": 3, "mrz": 3, "mär": 3, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
    + r")\s+(20\d{2}|\d{2})\b",
    re.I,
)


class ATAGESContractError(ValueError):
    """The AGES issue no longer exposes the expected monthly CSV contract."""


@dataclass(frozen=True)
class AGESIssue:
    detail_url: str
    csv_url: str
    report_month: date
    retrieved_at: datetime


@dataclass(frozen=True)
class ATFetchSummary:
    row_count: int
    months_fetched: int
    latest_date: Optional[date]


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").replace("\xa0", " ").split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _month_from_headers(headers: Iterable[str]) -> date:
    candidates: list[date] = []
    for header in headers:
        text = _text(header)
        # Cumulative and historical-comparison columns must never be
        # differentiated into a monthly value.
        if "-" in text or "median" in text.casefold():
            continue
        match = _MONTH_RE.search(text)
        if match:
            raw_year = int(match.group(2))
            year = 2000 + raw_year if raw_year < 100 else raw_year
            candidates.append(date(year, _MONTHS[match.group(1).casefold()], 1))
    if len(set(candidates)) != 1:
        raise ATAGESContractError("AGES CSV must contain exactly one non-cumulative month column")
    return candidates[0]


def parse_ages_csv(
    content: bytes,
    *,
    issue: AGESIssue,
) -> List[Dict[str, str]]:
    """Parse one official AGES table at its native disease/month grain."""
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames or len(reader.fieldnames) < 2:
        raise ATAGESContractError("AGES CSV does not have a disease and monthly column")
    disease_field = reader.fieldnames[0]
    month = _month_from_headers(reader.fieldnames[1:])
    month_field = next(field for field in reader.fieldnames[1:] if _MONTH_RE.search(_text(field)) and "-" not in _text(field) and "median" not in _text(field).casefold())
    output: List[Dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(reader, start=2):
        label = _text(raw.get(disease_field))
        value = _text(raw.get(month_field)).replace(" ", "")
        if not label or not value or value in {"-", "—", "NA", "N/A"}:
            continue
        try:
            cases = int(value)
        except ValueError as exc:
            raise ATAGESContractError(f"AGES CSV row {index} has invalid count {value!r}") from exc
        if cases < 0:
            raise ATAGESContractError(f"AGES CSV row {index} has negative count")
        source_code = _slug(label)
        if source_code in seen:
            raise ATAGESContractError(f"AGES CSV has duplicate disease row {label!r}")
        seen.add(source_code)
        # Keep the source category as a dimension.  The generic source-native
        # series is intentional: AGES can add a category without a deploy
        # silently discarding that fact.
        output.append({
            "Date": month.isoformat(), "Year": str(month.year), "Month": str(month.month),
            "RawDiseaseLabel": label, "SourceDiseaseCode": source_code,
            "DiseaseCode": "__source_native__", "Cases": str(cases),
            "PeriodType": "monthly", "Geography": "Austria", "GeographyKey": NATIONAL_GEOGRAPHY_KEY,
            "DatasetStatus": "closed_revisable", "IsProvisional": "false",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "Dimensions": json.dumps({"source_disease_code": source_code, "source_disease_label": label}, ensure_ascii=False, sort_keys=True),
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": issue.detail_url, "DownloadURL": issue.csv_url,
            "RetrievedAt": issue.retrieved_at.isoformat().replace("+00:00", "Z"),
            "RawSHA256": _sha256(content), "License": "not_specified_by_AGES", "PublicReleaseEnabled": "false", "LicenseReviewStatus": "pending",
        })
    if not output:
        raise ATAGESContractError("AGES CSV contains no monthly disease rows")
    return sorted(output, key=lambda row: (row["Date"], row["SourceDiseaseCode"]))


class AustriaAGESRadarCrawler(BaseCrawler):
    SOURCE_URL = DEFAULT_LANDING_URL

    def __init__(self, *, landing_url: str = DEFAULT_LANDING_URL, save_raw: bool = False, raw_dir: Optional[Path] = None) -> None:
        super().__init__(user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; AT-AGES-Radar)", timeout=90, max_retries=3, delay=0.2)
        self.landing_url, self.save_raw = landing_url, save_raw
        self.raw_dir = Path(raw_dir or "data/raw/at")

    def _archive(self, *, issue: AGESIssue, content: bytes) -> None:
        if not self.save_raw:
            return
        folder = self.raw_dir / f"monthly/{issue.report_month:%Y-%m}"
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"ages_radar_{issue.report_month:%Y_%m}_{_sha256(content)[:12]}"
        csv_path = folder / f"{stem}.csv"
        meta_path = folder / f"{stem}.json"
        csv_path.write_bytes(content)
        meta_path.write_text(json.dumps({"source_url": issue.detail_url, "download_url": issue.csv_url, "report_month": issue.report_month.isoformat(), "retrieved_at": issue.retrieved_at.isoformat(), "sha256": _sha256(content), "public_release_enabled": False, "license_review_status": "pending"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def discover_issues(self) -> List[AGESIssue]:
        landing = self.get(self.landing_url)
        soup = BeautifulSoup(landing.text, "html.parser")
        detail_urls = sorted({urljoin(self.landing_url, anchor.get("href")) for anchor in soup.select('a[href*="/details/ages-radar-for-infectious-diseases-"]')})
        if not detail_urls:
            raise ATAGESContractError("AGES landing page exposes no Radar issue links")
        issues: List[AGESIssue] = []
        for detail_url in detail_urls:
            page = self.get(detail_url)
            detail = BeautifulSoup(page.text, "html.parser")
            csv_anchor = next((a for a in detail.select("a[href]") if ".csv" in str(a.get("href")).casefold()), None)
            if csv_anchor is None:
                continue
            csv_url = urljoin(detail_url, str(csv_anchor.get("href")))
            # Read only the header once; it is the authoritative reporting
            # period and avoids guessing from publication date.
            csv_response = self.get(csv_url)
            reader = csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";")
            headers = next(reader, [])
            report_month = _month_from_headers(headers[1:])
            issue = AGESIssue(detail_url, str(getattr(csv_response, "url", csv_url)), report_month, datetime.now(timezone.utc))
            self._archive(issue=issue, content=csv_response.content)
            issues.append(issue)
        if not issues:
            raise ATAGESContractError("AGES issue pages expose no machine-readable CSV files")
        return sorted({issue.report_month: issue for issue in issues}.values(), key=lambda item: item.report_month)

    def crawl_monthly_national(self, output_csv: Path, *, months: Optional[Sequence[tuple[int, int]]] = None, backfill_history: bool = False) -> ATFetchSummary:
        issues = self.discover_issues()
        wanted = set(months or [])
        if backfill_history:
            wanted |= {(issue.report_month.year, issue.report_month.month) for issue in issues}
        selected = [issue for issue in issues if not wanted or (issue.report_month.year, issue.report_month.month) in wanted]
        if not selected:
            raise ATAGESContractError("No AGES issues match requested months; source archive may be incomplete")
        rows: List[Dict[str, str]] = []
        for issue in selected:
            response = self.get(issue.csv_url)
            self._archive(issue=issue, content=response.content)
            rows.extend(parse_ages_csv(response.content, issue=issue))
        # Normal dynamic updates fetch only a small revision window.  Preserve
        # all previously fetched history and replace only identical
        # disease/month facts with the newly authoritative issue table.
        merged: Dict[tuple[str, str], Dict[str, str]] = {}
        if output_csv.exists():
            with output_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                for cached in csv.DictReader(handle):
                    key = (_text(cached.get("Date")), _text(cached.get("SourceDiseaseCode")))
                    if all(key):
                        merged[key] = {name: _text(value) for name, value in cached.items()}
        for row in rows:
            merged[(row["Date"], row["SourceDiseaseCode"])] = row
        rows = sorted(merged.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
        fieldnames = sorted({key for row in rows for key in row})
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_csv.with_suffix(output_csv.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader(); writer.writerows(rows)
        os.replace(temporary, output_csv)
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        return ATFetchSummary(len(rows), len(selected), latest)

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        del kwargs
        return []

    def parse(self, response) -> List[CrawlerResult]:
        del response
        return []


__all__ = ["ATAGESContractError", "ATFetchSummary", "AGESIssue", "AustriaAGESRadarCrawler", "DEFAULT_SCOPE", "DEFAULT_SOURCE_NAME", "HISTORY_START_YEAR", "NATIONAL_GEOGRAPHY_KEY", "ONTOLOGY_SOURCE_ID", "parse_ages_csv"]
