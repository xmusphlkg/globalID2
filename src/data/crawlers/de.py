"""Germany RKI SurvStat 2.0 weekly national export adapter.

SurvStat is an ASP.NET WebForms application rather than a documented API.
This module owns that session contract (cookies, hidden fields and the ZIP
export) and treats a changed control/schema as a hard failure.  A configured
``export_url_template`` is supported for RKI-published saved exports and makes
recovery from a UI change operational without accepting unversioned HTML.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from bs4 import BeautifulSoup

from .base import BaseCrawler, CrawlerResult

DEFAULT_SOURCE_NAME = "Germany RKI SurvStat 2.0"
DEFAULT_SCOPE = "rki_survstat"
ONTOLOGY_SOURCE_ID = "SRC_DE_RKI_SURVSTAT"
DEFAULT_CREATE_URL = "https://survstat.rki.de/Content/Query/Create.aspx"
DEFAULT_MAIN_URL = "https://survstat.rki.de/Content/Query/Main.aspx"
HISTORY_START_YEAR = 2001
NATIONAL_GEOGRAPHY_KEY = "country:DE:national"
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?:\D+)([0-5]?\d)(?!\d)")


class DESurvStatContractError(ValueError):
    pass


@dataclass(frozen=True)
class DEFetchSummary:
    row_count: int
    years_fetched: int
    latest_date: Optional[date]


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").replace("\xa0", " ").split())


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-")


def _week_start(year: int, week: int) -> date:
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise DESurvStatContractError(f"Invalid ISO reporting week {year}/{week}") from exc


def _find_column(headers: Iterable[str], *aliases: str) -> Optional[str]:
    normalized = {_key(header): header for header in headers}
    for alias in aliases:
        target = _key(alias)
        if target in normalized:
            return normalized[target]
    return next((header for header in headers if any(_key(alias) in _key(header) for alias in aliases)), None)


def _normalized_row(*, label: str, year: int, week: int, cases: int, source_url: str, retrieved_at: datetime, raw_sha: str) -> Dict[str, str]:
    report_date = _week_start(year, week)
    source_code = _slug(label)
    return {
        "Date": report_date.isoformat(), "Year": str(year), "Week": str(week),
        "RawDiseaseLabel": label, "SourceDiseaseCode": source_code, "DiseaseCode": "__source_native__", "Cases": str(cases),
        "PeriodType": "weekly", "Geography": "Deutschland", "GeographyKey": NATIONAL_GEOGRAPHY_KEY,
        "DatasetStatus": "closed_revisable", "IsProvisional": "false", "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
        "Dimensions": json.dumps({"source_disease_code": source_code, "source_disease_label": label, "reference_definition": "default"}, ensure_ascii=False, sort_keys=True),
        "Source": DEFAULT_SOURCE_NAME, "SourceURL": source_url, "RetrievedAt": retrieved_at.isoformat().replace("+00:00", "Z"), "RawSHA256": raw_sha,
        "License": "RKI_data_usage_terms", "PublicReleaseEnabled": "true", "LicenseReviewStatus": "reviewed_source_attribution_required",
    }


def parse_survstat_csv(content: bytes, *, source_url: str, retrieved_at: datetime, export_year: Optional[int] = None) -> List[Dict[str, str]]:
    """Normalize a SurvStat CSV while retaining all pathogen categories."""
    decoded = ""
    # UTF-8 normally decodes first; German historical exports may instead be
    # cp1252.  A replacement marker is a reliable reason to retry.
    codecs = ("utf-16", "utf-8-sig", "cp1252", "latin-1") if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp1252", "latin-1")
    for codec in codecs:
        try:
            candidate = content.decode(codec)
        except UnicodeDecodeError:
            continue
        if "�" not in candidate:
            decoded = candidate; break
    delimiter = "\t" if decoded.count("\t") > max(decoded.count(";"), decoded.count(",")) else (";" if decoded.count(";") >= decoded.count(",") else ",")
    raw_sha = hashlib.sha256(content).hexdigest()

    # Current SurvStat exports use a two-row pivot header: disease categories
    # down the rows and reporting weeks across columns.  The selected year is
    # stored in the archived query PDF/session, so the caller supplies it.
    matrix = list(csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter))
    if export_year is not None and len(matrix) >= 3 and len(matrix[1]) > 2:
        week_cells = matrix[1][1:]
        if all(not _text(cell) or _text(cell).isdigit() for cell in week_cells):
            output: List[Dict[str, str]] = []
            seen: set[tuple[date, str]] = set()
            for line, values in enumerate(matrix[2:], start=3):
                label = _text(values[0] if values else "")
                if not label:
                    continue
                for week_text, value_text in zip(week_cells, values[1:]):
                    week_value = _text(week_text)
                    value = _text(value_text).replace(".", "").replace(" ", "")
                    if not week_value or not value or value in {"-", "—", "*"}:
                        continue
                    try:
                        week, cases = int(week_value), int(value)
                    except ValueError as exc:
                        raise DESurvStatContractError(f"SurvStat pivot row {line} has invalid week/count") from exc
                    row = _normalized_row(label=label, year=int(export_year), week=week, cases=cases, source_url=source_url, retrieved_at=retrieved_at, raw_sha=raw_sha)
                    identity = (date.fromisoformat(row["Date"]), row["SourceDiseaseCode"])
                    if identity in seen:
                        raise DESurvStatContractError(f"SurvStat export has duplicate national disease/week {identity!r}")
                    seen.add(identity); output.append(row)
            if output:
                return sorted(output, key=lambda row: (row["Date"], row["SourceDiseaseCode"]))

    reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter)
    if not reader.fieldnames:
        raise DESurvStatContractError("SurvStat export has no CSV header")
    disease_col = _find_column(reader.fieldnames, "Krankheit", "Erreger", "Pathogen", "Kategorie")
    year_col = _find_column(reader.fieldnames, "Meldejahr", "Jahr", "Reporting year", "Year")
    week_col = _find_column(reader.fieldnames, "Meldewoche", "Kalenderwoche", "Week", "Reporting week")
    value_col = _find_column(reader.fieldnames, "Anzahl Fälle", "Anzahl Faelle", "Fälle", "Faelle", "Cases", "Count")
    if not all((disease_col, year_col, week_col, value_col)):
        raise DESurvStatContractError(f"SurvStat CSV schema missing disease/year/week/count columns: {reader.fieldnames!r}")
    rows: List[Dict[str, str]] = []
    seen: set[tuple[date, str]] = set()
    for line, raw in enumerate(reader, start=2):
        label = _text(raw.get(disease_col))
        value = _text(raw.get(value_col)).replace(".", "").replace(" ", "")
        if not label or not value or value in {"-", "—", "*"}:
            continue
        try:
            year, week, cases = int(_text(raw.get(year_col))), int(_text(raw.get(week_col))), int(value)
        except ValueError as exc:
            raise DESurvStatContractError(f"SurvStat CSV row {line} has invalid year/week/count") from exc
        if cases < 0:
            raise DESurvStatContractError(f"SurvStat CSV row {line} has negative count")
        report_date = _week_start(year, week)
        source_code = _slug(label)
        identity = (report_date, source_code)
        if identity in seen:
            raise DESurvStatContractError(f"SurvStat export has duplicate national disease/week {identity!r}")
        seen.add(identity)
        rows.append(_normalized_row(label=label, year=year, week=week, cases=cases, source_url=source_url, retrieved_at=retrieved_at, raw_sha=raw_sha))
    if not rows:
        raise DESurvStatContractError("SurvStat export has no usable weekly national records")
    return sorted(rows, key=lambda row: (row["Date"], row["SourceDiseaseCode"]))


def parse_survstat_zip(content: bytes, *, source_url: str, retrieved_at: datetime, export_year: Optional[int] = None) -> List[Dict[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.casefold().endswith(".csv")]
            if len(names) != 1:
                raise DESurvStatContractError("SurvStat ZIP must contain exactly one CSV export")
            return parse_survstat_csv(archive.read(names[0]), source_url=source_url, retrieved_at=retrieved_at, export_year=export_year)
    except zipfile.BadZipFile as exc:
        raise DESurvStatContractError("SurvStat download is not a ZIP export") from exc


class GermanySurvStatCrawler(BaseCrawler):
    SOURCE_URL = DEFAULT_CREATE_URL

    def __init__(self, *, create_url: str = DEFAULT_CREATE_URL, export_url_template: str = "", save_raw: bool = False, raw_dir: Optional[Path] = None) -> None:
        super().__init__(user_agent="Mozilla/5.0 (compatible; GlobalID/2.0; DE-RKI-SurvStat)", timeout=120, max_retries=2, delay=0.3)
        self.create_url, self.export_url_template, self.save_raw = create_url, export_url_template, save_raw
        self.raw_dir = Path(raw_dir or "data/raw/de")

    def _form_payload(self, html: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.select_one("form")
        if form is None:
            raise DESurvStatContractError("SurvStat session page has no form")
        values: Dict[str, str] = {}
        for control in form.select("input[name], select[name], textarea[name]"):
            name = control.get("name")
            if not name or control.get("type") in {"submit", "button"}:
                continue
            if control.name == "select":
                selected = control.select_one("option[selected]") or control.select_one("option")
                if selected is not None:
                    values[name] = str(selected.get("value", ""))
            elif control.get("type") not in {"checkbox", "radio"} or control.has_attr("checked"):
                values[name] = str(control.get("value", ""))
        return values

    def _postback(self, html: str, *, target: str, value: Optional[str] = None):
        payload = self._form_payload(html)
        payload["__EVENTTARGET"] = target; payload["__EVENTARGUMENT"] = ""
        if value is not None:
            payload[target] = value
        return self.post(self.create_url, data=payload)

    def _download_year(self, year: int) -> tuple[bytes, str]:
        if self.export_url_template:
            url = self.export_url_template.format(year=year)
            response = self.get(url)
            return response.content, str(getattr(response, "url", url))
        # A completed export leaves server-side WebForms state behind.  Each
        # year must start a new ASP.NET session or later postbacks can lose the
        # empty filter row used below.
        self.session.cookies.clear()
        # Direct access to Create.aspx is rejected without the session state
        # established by the official query landing page.
        main_url = self.create_url.rsplit("/", 1)[0] + "/Main.aspx"
        self.get(main_url)
        initial = self.get(self.create_url)
        html = initial.text
        controls = self._form_payload(html)
        row = next((name for name in controls if name.endswith("DropDownListRowHierarchy")), None)
        column = next((name for name in controls if name.endswith("DropDownListColHierarchy")), None)
        if not row or not column:
            raise DESurvStatContractError("SurvStat WebForms control contract changed; configure a saved export URL while updating adapter")
        html = self._postback(html, target=row, value="[PathogenOut].[KategorieNz]").text
        html = self._postback(html, target=column, value="[ReportingDate].[Week]").text
        # The page exposes a year member selector after its filter hierarchy is
        # chosen.  Locate it rather than pinning generated repeater indexes.
        form = self._form_payload(html)
        filter_hierarchies = [
            name
            for name, value in form.items()
            if "DropDownListFilterHierarchy" in name and not value
        ]
        filter_hierarchy = sorted(filter_hierarchies)[-1] if filter_hierarchies else None
        if not filter_hierarchy:
            raise DESurvStatContractError("SurvStat did not expose a time filter selector")
        html = self._postback(html, target=filter_hierarchy, value="[ReportingDate].[WeekYear]").text
        soup = BeautifulSoup(html, "html.parser")
        member = next((select.get("name") for select in soup.select("select[name]") if any(str(year) in _text(option.get_text()) for option in select.select("option"))), None)
        if not member:
            raise DESurvStatContractError(f"SurvStat did not expose a selectable member for year {year}")
        option = next(option for option in soup.select(f'select[name="{member}"] option') if str(year) in _text(option.get_text()))
        html = self._postback(html, target=member, value=str(option.get("value", ""))).text
        payload = self._form_payload(html)
        download = next((name for name in payload if name.endswith("ButtonDownload")), "ctl00$ctl00$ContentPlaceHolderMain$ContentPlaceHolderAltGridFull$ButtonDownload")
        payload[download] = "ZIP herunterladen"
        response = self.post(self.create_url, data=payload)
        if not response.content.startswith(b"PK"):
            raise DESurvStatContractError("SurvStat did not return a ZIP; interactive query contract likely changed")
        return response.content, str(getattr(response, "url", self.create_url))

    def _archive(self, year: int, payload: bytes, url: str) -> None:
        if not self.save_raw:
            return
        folder = self.raw_dir / f"weekly/{year}"; folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest(); path = folder / f"survstat_{year}_{digest[:12]}.zip"
        path.write_bytes(payload)
        path.with_suffix(".json").write_text(json.dumps({"source_url": url, "year": year, "retrieved_at": datetime.now(timezone.utc).isoformat(), "sha256": digest}, indent=2) + "\n", encoding="utf-8")

    def crawl_weekly_national(self, output_csv: Path, *, years: Iterable[int]) -> DEFetchSummary:
        rows: List[Dict[str, str]] = []
        used_years = sorted(set(int(year) for year in years))
        for year in used_years:
            last_error: Optional[Exception] = None
            for _attempt in range(3):
                try:
                    payload, url = self._download_year(year)
                    break
                except DESurvStatContractError as exc:
                    last_error = exc
            else:
                raise DESurvStatContractError(
                    f"SurvStat year {year} failed after three fresh sessions: {last_error}"
                ) from last_error
            self._archive(year, payload, url)
            rows.extend(parse_survstat_zip(payload, source_url=url, retrieved_at=datetime.now(timezone.utc), export_year=year))
        # A normal run exports only one or two years for the revision window;
        # retain previous historical years and replace the refreshed weekly
        # disease facts idempotently.
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
        fields = sorted({key for row in rows for key in row}); output_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_csv.with_suffix(output_csv.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        os.replace(temporary, output_csv)
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        return DEFetchSummary(len(rows), len(used_years), latest)

    async def crawl(self, **kwargs) -> List[CrawlerResult]:
        del kwargs; return []

    def parse(self, response) -> List[CrawlerResult]:
        del response; return []


__all__ = ["DEFAULT_SCOPE", "DEFAULT_SOURCE_NAME", "DEFetchSummary", "DESurvStatContractError", "GermanySurvStatCrawler", "HISTORY_START_YEAR", "ONTOLOGY_SOURCE_ID", "parse_survstat_csv", "parse_survstat_zip"]
