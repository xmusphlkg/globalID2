"""User-curated ID_TH Tableau archive, preserving source weeks and both metrics.

The archive does not establish a report/onset date basis or verified historical
week calendar. January 1 is a storage anchor only; source year/week dimensions
are the observation identity. Never reinterpret these periods as ISO weeks.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

from .base import BaseCrawler

DEFAULT_SCOPE = "th_id_th_history"
DEFAULT_SOURCE_NAME = "Thailand ID_TH Weekly Archive"
ONTOLOGY_SOURCE_ID = "SRC_TH_ID_TH_ARCHIVE"
REPOSITORY = "https://github.com/xmusphlkg/ID_TH"
API_URL = "https://api.github.com/repos/xmusphlkg/ID_TH"
RAW_URL = "https://raw.githubusercontent.com/xmusphlkg/ID_TH"
EXCLUDED_STEMS = {
    "เล_อกท_กคร_งเพ_อแสดงอ_ตราป_วย": "Tableau incidence-rate UI instruction, not a disease",
    "ตารางการกระจายผ_ป_วยจ_งหว_ด_data": "Unattributed worksheet, disease identity unknown",
}
COLUMNS = ("สัปดาห์-value", "สัปดาห์-alias", "จังหวัด-value", "จังหวัด-alias", "AGG(ข้อมูล)-alias")


class IDTHContractError(ValueError):
    """Fail closed when the preserved archive contract changes."""


def git_blob_sha(payload):
    return hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()


def source_file(path):
    parts = PurePosixPath(path).parts
    if len(parts) != 4 or parts[0] != "Data" or parts[1] not in {"WeeklyCasesData", "WeeklyDeathsData"}:
        return None
    if not parts[2].isdigit() or not parts[3].endswith(".csv"):
        return None
    year = int(parts[2]) - 543
    if not 2020 <= year <= datetime.now(timezone.utc).year:
        raise IDTHContractError(f"Unreviewed archive year: {parts[2]}")
    return year, "cases" if parts[1] == "WeeklyCasesData" else "deaths", PurePosixPath(parts[3]).stem


def selected_files(tree):
    if tree.get("truncated"):
        raise IDTHContractError("GitHub archive tree was truncated")
    files = [item for item in tree.get("tree", []) if item.get("type") == "blob" and source_file(item["path"])]
    if not files:
        raise IDTHContractError("No national weekly archive files in repository tree")
    return sorted(files, key=lambda item: item["path"])


def raw_url(commit, path):
    return f"{RAW_URL}/{commit}/{quote(path, safe='/')}"


def parse_weekly_csv(payload, *, path, commit, blob_sha, retrieved_at=""):
    details = source_file(path)
    if details is None:
        raise IDTHContractError(f"Not a top-level national archive CSV: {path}")
    year, metric, code = details
    if code in EXCLUDED_STEMS:
        raise IDTHContractError(f"Excluded archive worksheet: {EXCLUDED_STEMS[code]}")
    if git_blob_sha(payload) != blob_sha:
        raise IDTHContractError(f"Git blob integrity mismatch: {path}")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise IDTHContractError(f"Archive weekly schema changed: {path}")
    observed, annual = {}, None
    for raw in reader:
        # Explicit Tableau national total only. Province rows are not additive
        # with this total, and age-file subdirectories are excluded upstream.
        if raw["จังหวัด-value"] != "%all%":
            continue
        value = raw["AGG(ข้อมูล)-alias"]
        if not re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", value or ""):
            raise IDTHContractError(f"Non-count national cell in {path}: {value!r}")
        count = int(value.replace(",", ""))
        week = raw["สัปดาห์-value"]
        if week == "%all%":
            if annual is not None:
                raise IDTHContractError(f"Duplicate annual national total: {path}")
            annual = count
            continue
        if not week.isdigit() or not 1 <= int(week) <= 53:
            raise IDTHContractError(f"Invalid source week in {path}: {week!r}")
        week = int(week)
        if week in observed:
            raise IDTHContractError(f"Duplicate national week in {path}: {week}")
        observed[week] = count
    if not observed or annual is None or sum(observed.values()) != annual:
        raise IDTHContractError(f"National weekly cells do not reconcile to annual total: {path}")
    digest = hashlib.sha256(payload).hexdigest()
    rows = []
    for week, count in sorted(observed.items()):
        rows.append({
            "Date": f"{year}-01-01", "Year": str(year), "Month": "1", "SourceYear": str(year), "SourceWeek": str(week),
            "DiseaseCode": "__source_native__", "RawDiseaseLabel": code.replace("_", " "), "SourceDiseaseCode": code,
            "Cases": str(count), "Measure": "case_notifications" if metric == "cases" else "death_notifications", "Unit": "count",
            "Frequency": "weekly", "PeriodType": "source_week", "ReportingBasis": "unverified_archive_basis",
            "DefinitionVersion": f"th_id_th_archive_{metric}_basis_unverified_year_anchor_v1",
            "CalendarStatus": "unverified_historical_calendar", "TimeBasis": "source_year_week_unverified_calendar", "DateRole": "source_year_storage_anchor_only",
            "PeriodStart": "", "PeriodEnd": "", "SourceAnnualTotal": str(annual),
            "Geography": "Thailand", "GeographyKey": "country:TH:national",
            "DatasetStatus": "historical_archive_revisable", "IsProvisional": "true",
            "RevisionSemantics": "authoritative_revision", "AuthoritativeRevision": "true",
            "Source": DEFAULT_SOURCE_NAME, "SourceURL": REPOSITORY, "DownloadURL": raw_url(commit, path),
            "SourceRepositoryCommit": commit, "SourceRepositoryPath": path, "SourceGitBlobSHA": blob_sha,
            "RetrievedAt": retrieved_at or datetime.now(timezone.utc).isoformat(), "RawSHA256": digest,
            "License": "archive_reuse_requires_review", "PublicReleaseEnabled": "false",
            "Dimensions": json.dumps({"source_year": year, "source_week": week, "source_metric": metric}, sort_keys=True),
        })
    return rows


class ThailandIDTHCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=90, delay=0.05)
        self.session.headers.update({"Accept": "application/vnd.github+json"})

    async def crawl(self, **kwargs):
        raise IDTHContractError("ID_TH collection requires the staged artifact connector")

    def parse(self, response):
        raise IDTHContractError("ID_TH parsing requires pinned path/commit/blob provenance")
