#!/usr/bin/env python3
"""Reconcile frozen INS BES annual cumulative annex facts with their stored rows.

Epidemiological year comes from the source dimensions, never the calendar year
of the report date. A successful comparison certifies the supported annex facts
in the captured reports; it does not certify every narrative table or permit
public release.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.imports.import_surveillance_archive import load_archive_manifest
from src.core.database import dispose_database, get_db
from src.data.crawlers.co_bes import DEFAULT_SCOPE, ONTOLOGY_SOURCE_ID
from src.data.processors.co_bes import ColombiaINSBESUpdater
from src.data.storage.series.identity import _dimension_key
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries import co_bes

SAMPLE_LIMIT = 20
VALID_WEEKS = frozenset(map(str, range(1, 54)))
# Independently transcribed from the complete PDFs at the listed SHA-256s.
# These check actual column placement as well as full DB row fidelity.
ORIGINAL_CELL_CHECKS = {
    "8aa142bd19e17ed167f063e363e97ddf9c5938753aa5a0c773e83d822e0c162d": (
        ("Accidente ofídico", "Total nacional", 5397),
        ("Accidente ofídico", "Amazonas", 65),
    ),
    "c8ac2eca6791d930b316bef33488bc205bd18bcee8f3eb788016b7c9d8a6ffb1": (
        ("Accidente ofídico", "Total nacional", 4276),
        ("Accidente ofídico", "Amazonas", 44),
        ("Accidente ofídico", "Antioquia", 499),
        ("Dengue General (Dengue y Dengue Grave)", "Total nacional", 101949),
        ("Dengue Grave", "Total nacional", 1016),
    ),
}
PHYSICAL_FIELDS = (
    "raw_data", "time", "geography_key", "source_series_code",
    "definition_version", "value", "dimension_key", "observation_unit",
    "series_unit", "metric_type", "reporting_basis", "temporal_granularity",
    "suppressed",
)


def fingerprint(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _dimensions(row: dict) -> dict:
    value = row.get("Dimensions")
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError("BES source dimensions must be an object")  # noqa: TRY004 - source contract failure.
    return result


def source_year(row: dict) -> int:
    """Do not mistake 2025 W53's January 2026 end date for source year 2026."""
    value = _dimensions(row).get("source_epi_year")
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ValueError("BES row has no explicit valid source epidemiological year")  # noqa: TRY004 - missing source identity.
    try:
        year = int(value)
    except ValueError:
        raise ValueError("BES row has no explicit valid source epidemiological year") from None
    if str(year) != str(value) or not 2025 <= year <= 2100:
        raise ValueError("BES row has no explicit valid source epidemiological year")
    return year


def _utc_time(value) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def expected_record(row: dict) -> tuple:
    if row["Cases"] in ("", None) and row.get("MissingValueStatus") in {"source_missing", "source_unreadable"}:
        value = None
    else:
        value = float(row["Cases"])
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise ValueError("BES cumulative source counts must be finite nonnegative integers")
    dimensions = _dimensions(row)
    week = dimensions.get("source_epi_week")
    if isinstance(week, bool) or str(week) not in VALID_WEEKS:
        raise ValueError("BES row has no explicit valid source epidemiological week")
    if row.get("PeriodType") != "source_epi_year_to_date_at_week":
        raise ValueError("BES cumulative facts require their explicit source period type")
    unit = row.get("Unit", "count")
    return (
        fingerprint(row), _utc_time(row["Date"]), row["GeographyKey"],
        row["SourceDiseaseCode"], row["DefinitionVersion"], value,
        _dimension_key(dimensions), unit, unit, row["Measure"],
        row["ReportingBasis"], row["Frequency"], False,
    )


@dataclass
class SourceSnapshot:
    records: dict[str, tuple[int, tuple]]
    counts: dict[int, int]
    source_audit: dict
    capture_complete: bool
    all_captured_reports_have_facts: bool
    captured_pdf_count: int
    represented_pdf_count: int
    source_missing_cells: int = 0
    ambiguous_source_label_cells: int = 0
    original_cell_checks: tuple = ()


def _normalized_label(value):
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def check_original_cells(rows):
    """Check specific printed original cells independently of the parser's grid."""
    indexed = {}
    hashes = set()
    for row in rows:
        digest = row.get("RawSHA256")
        hashes.add(digest)
        if digest in ORIGINAL_CELL_CHECKS:
            key = (digest, _normalized_label(row.get("RawDiseaseLabel")),
                   _normalized_label(row.get("Geography")))
            indexed.setdefault(key, []).append(row)
    checks = []
    for digest in hashes & ORIGINAL_CELL_CHECKS.keys():
        for disease, geography, expected in ORIGINAL_CELL_CHECKS[digest]:
            key = (digest, _normalized_label(disease), _normalized_label(geography))
            matches = indexed.get(key, [])
            if len(matches) != 1 or float(matches[0]["Cases"]) != expected:
                raise ValueError(f"BES original PDF cell check failed: {disease}, {geography}")
            checks.append({"raw_sha256": digest, "source_label": disease,
                           "source_geography": geography, "value": expected, "matches": True})
    return tuple(checks)


def build_snapshot(artifacts, rows, *, start_year, end_year, source_audit=None):
    original_cells = check_original_cells(rows)
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "collection_plan"]
    if len(plans) != 1:
        raise ValueError("Exactly one frozen BES collection plan is required")
    plan = json.loads(read_artifact(plans[0]))
    if (int(plan["requested_start_year"]) != start_year
            or int(plan["requested_end_year"]) != end_year):
        raise ValueError("BES manifest collection years differ from verification years")
    pdfs = [a for a in artifacts if a.metadata_.get("endpoint") == "pdf"]
    urls_by_hash = {}
    listed_years = {}
    for artifact in pdfs:
        urls_by_hash.setdefault(artifact.sha256, set()).add(unquote(artifact.source_url))
        listed_year = artifact.metadata_.get("report", {}).get("listed_year")
        if listed_year is not None:
            listed_years[(artifact.sha256, unquote(artifact.source_url))] = int(listed_year)
    planned = {unquote(report["pdf_url"]) for report in plan["reports"]}
    actual = {unquote(a.metadata_.get("report", {}).get("pdf_url", a.source_url)) for a in pdfs}
    capture_complete = bool(pdfs) and not plan["failed_downloads"] and len(pdfs) == len(planned) and actual == planned
    records = {}
    counts = Counter()
    represented_files = set()
    source_missing = ambiguous_labels = 0
    for row in rows:
        record_id = row.get("SourceRecordID")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("BES source fact has no source record identity")
        if record_id in records:
            raise ValueError("Duplicate BES source record identity in frozen originals")
        year = source_year(row)
        if not start_year <= year <= end_year:
            raise ValueError("BES organized source fact belongs to another source year")
        digest = row.get("RawSHA256")
        if (digest not in urls_by_hash
                or unquote(row.get("SourceURL", "")) not in urls_by_hash[digest]):
            raise ValueError("BES source fact is not linked to a frozen official PDF")
        listed_year = listed_years.get((digest, unquote(row["SourceURL"])))
        if listed_year is not None and listed_year != year:
            raise ValueError("BES source epidemiological year differs from official directory metadata")
        represented_files.add((digest, unquote(row["SourceURL"])))
        source_missing += row.get("MissingValueStatus") == "source_missing"
        dimensions = _dimensions(row)
        ambiguous_labels += bool(dimensions.get("source_label_ambiguous")
                                 or dimensions.get("unresolved_duplicate_source_label"))
        records[record_id] = (year, expected_record(row))
        counts[year] += 1
    if not records:
        raise ValueError("BES verification requires nonempty verified source facts")
    captured_files = {(a.sha256, unquote(a.source_url)) for a in pdfs}
    return SourceSnapshot(records, dict(counts), source_audit or {}, capture_complete,
                          represented_files == captured_files, len(pdfs), len(represented_files),
                          source_missing, ambiguous_labels, original_cells)


def load_source(manifest, *, start_year, end_year, parse_workers, source_csv=None):
    artifacts = load_archive_manifest(Path(manifest), "CO")
    fetched = co_bes.organize(artifacts, updater=ColombiaINSBESUpdater(),
                             source=DEFAULT_SCOPE, start_year=start_year,
                             end_year=end_year, parse_workers=parse_workers)
    snapshot = build_snapshot(artifacts, fetched.rows, start_year=start_year,
                              end_year=end_year, source_audit=fetched.source_audit)
    if source_csv is not None:
        with Path(source_csv).open(newline="", encoding="utf-8-sig") as stream:
            csv_rows = list(csv.DictReader(stream))
        csv_records = {}
        for row in csv_rows:
            record_id = row.get("SourceRecordID")
            if not record_id or record_id in csv_records:
                raise ValueError("BES materialized CSV has missing or duplicate source identities")
            csv_records[record_id] = fingerprint(row)
        expected = {key: record[1][0] for key, record in snapshot.records.items()}
        if csv_records != expected:
            raise ValueError("BES materialized CSV differs from frozen original organization")
    return snapshot


async def compare_database(snapshot, *, start_year, end_year, report_urls=None):
    expected = dict(snapshot.records)
    seen = set()
    stats = {str(y): {"source_rows": snapshot.counts.get(y, 0), "database_rows": 0,
                     "missing_database_rows": 0, "unexpected_database_rows": 0,
                     "duplicate_database_record_ids": 0, "mismatched_rows": 0}
             for y in range(start_year, end_year + 1)}
    unexpected = duplicate = mismatch = outside = 0
    mismatch_examples, unexpected_examples, duplicate_examples = [], [], []
    statement = """
        SELECT obs.raw_data, obs.value, obs.time, obs.geography_key,
               obs.dimension_key, series.source_series_code, series.definition_version,
               obs.unit, series.unit, series.metric_type, series.reporting_basis,
               series.temporal_granularity, obs.suppressed
        FROM disease_series_observations obs
        JOIN disease_surveillance_series series USING (series_code)
        WHERE series.country_code='CO' AND series.source_system=:source_id
    """
    parameters = {"source_id": ONTOLOGY_SOURCE_ID}
    if report_urls is not None:
        if not report_urls:
            raise ValueError("Report-scoped reconciliation requires original report URLs")
        statement += " AND obs.raw_data ->> 'SourceURL' IN :report_urls"
        parameters["report_urls"] = sorted(set(report_urls))
        query = text(statement).bindparams(bindparam("report_urls", expanding=True))
    else:
        query = text(statement)
    try:
        async with get_db() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            stream = await db.stream(query, parameters)
            async for raw, value, when, geography, dimensions, code, version, unit, series_unit, metric, basis, frequency, suppressed in stream:
                record_id = raw.get("SourceRecordID") if isinstance(raw, dict) else None
                source = snapshot.records.get(record_id)
                try:
                    year = source[0] if source else source_year(raw)
                except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                    year = None
                if year is not None and not start_year <= year <= end_year:
                    outside += 1
                    continue
                year_stats = stats.get(str(year))
                if year_stats is not None:
                    year_stats["database_rows"] += 1
                if record_id is not None and record_id in seen:
                    duplicate += 1
                    if year_stats is not None:
                        year_stats["duplicate_database_record_ids"] += 1
                    if len(duplicate_examples) < SAMPLE_LIMIT:
                        duplicate_examples.append(record_id)
                    continue
                if record_id is not None:
                    seen.add(record_id)
                if source is None:
                    unexpected += 1
                    if year_stats is not None:
                        year_stats["unexpected_database_rows"] += 1
                    if len(unexpected_examples) < SAMPLE_LIMIT:
                        unexpected_examples.append(record_id)
                    continue
                expected.pop(record_id, None)
                actual = (fingerprint(raw), _utc_time(when), geography, code, version,
                          value, dimensions, unit, series_unit, metric, basis, frequency, suppressed)
                fields = [name for name, left, right in zip(PHYSICAL_FIELDS, source[1], actual) if left != right]
                if fields:
                    mismatch += 1
                    year_stats["mismatched_rows"] += 1
                    if len(mismatch_examples) < SAMPLE_LIMIT:
                        mismatch_examples.append({"source_record_id": record_id, "fields": fields})
    finally:
        await dispose_database()
    for year, _record in expected.values():
        stats[str(year)]["missing_database_rows"] += 1
    for detail in stats.values():
        detail["facts_match"] = detail["source_rows"] == detail["database_rows"] and not any(
            detail[key] for key in ("missing_database_rows", "unexpected_database_rows", "duplicate_database_record_ids", "mismatched_rows"))
    facts_match = not (expected or unexpected or duplicate or mismatch) and all(x["facts_match"] for x in stats.values())
    audit = snapshot.source_audit
    # Older in-flight imports may have marked label ambiguity as broad coverage
    # uncertainty even though both original numeric columns were retained.
    # The fixed-cell contract measures extraction, independently of label review.
    reported_fixed_complete = audit.get("fixed_annex_cells_complete")
    if reported_fixed_complete is None:
        parsed_reports = audit.get("parsed_reports")
        discovered_reports = audit.get("discovered_reports")
        reported_fixed_complete = (
            isinstance(parsed_reports, int) and not isinstance(parsed_reports, bool)
            and parsed_reports == discovered_reports == snapshot.captured_pdf_count
            and parsed_reports > 0
        )
    parser_coverage_complete = (
        reported_fixed_complete is True
        and not audit.get("failed_reports")
        and not snapshot.source_audit.get("parser_gaps")
        and not snapshot.source_audit.get("unparsed_groups")
        and not snapshot.source_audit.get("unparsed_cells")
    )
    fixed_annex_cells_complete = (snapshot.capture_complete
                                 and snapshot.all_captured_reports_have_facts
                                 and parser_coverage_complete)
    complete = facts_match and fixed_annex_cells_complete
    return {
        "source": DEFAULT_SCOPE, "source_id": ONTOLOGY_SOURCE_ID,
        "comparison_scope": "snapshot_reports" if report_urls is not None else "full_source",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "verification": "frozen_artifact_full_source_row_and_physical_semantics",
        "coverage_scope": "source_native_cumulative_dashboard_cells",
        "source_scope": "fixed_annex_source_year_to_date_cumulative_counts",
        "all_narrative_tables_imported": False, "public_release_enabled": False,
        "start_year": start_year, "end_year": end_year, "years": stats,
        "source_rows": len(snapshot.records), "missing_database_rows": len(expected),
        "unexpected_database_rows": unexpected, "duplicate_database_record_ids": duplicate,
        "mismatched_rows": mismatch, "outside_requested_source_year_rows": outside,
        "captured_pdf_count": snapshot.captured_pdf_count,
        "represented_pdf_count": snapshot.represented_pdf_count,
        "source_missing_cells": snapshot.source_missing_cells,
        "ambiguous_source_label_cells": snapshot.ambiguous_source_label_cells,
        "source_definitions_fully_resolved": snapshot.ambiguous_source_label_cells == 0,
        "independent_original_cell_checks": snapshot.original_cell_checks,
        "capture_complete": snapshot.capture_complete,
        "parser_coverage_complete": parser_coverage_complete,
        "fixed_annex_cells_complete": fixed_annex_cells_complete,
        "all_captured_reports_have_facts": snapshot.all_captured_reports_have_facts,
        "missing_source_record_id_examples": list(expected)[:SAMPLE_LIMIT],
        "unexpected_database_record_id_examples": unexpected_examples,
        "duplicate_database_record_id_examples": duplicate_examples,
        "mismatched_row_examples": mismatch_examples,
        "facts_match": facts_match, "coverage_verified": complete,
        "complete": complete, "source_audit": snapshot.source_audit,
    }


async def verify(manifest, *, start_year=2025, end_year=2026, parse_workers=8, source_csv=None,
                 only_manifest_reports=False):
    manifest = Path(manifest)
    frozen = manifest.read_bytes()
    snapshot = await asyncio.to_thread(load_source, manifest, start_year=start_year,
                                       end_year=end_year, parse_workers=parse_workers,
                                       source_csv=source_csv)
    report_urls = None
    if only_manifest_reports:
        report_urls = [item["source_url"] for item in snapshot.source_audit["report_audits"]]
    report = await compare_database(snapshot, start_year=start_year, end_year=end_year,
                                    report_urls=report_urls)
    if manifest.read_bytes() != frozen:
        raise ValueError("BES manifest changed during verification; rerun a frozen snapshot")
    report["source_manifest"] = str(manifest.resolve())
    report["source_manifest_sha256"] = hashlib.sha256(frozen).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-csv", type=Path)
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--parse-workers", type=int, choices=range(1, 33), default=8)
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/co/bes_database_acceptance.json")
    parser.add_argument("--require-complete", action="store_true",
                        help="Require all captured fixed-annex cells and stored facts to match; excludes narrative tables and source-label resolution")
    args = parser.parse_args()
    if not 2025 <= args.start_year <= args.end_year <= datetime.now(timezone.utc).year:
        parser.error("BES source years must be between 2025 and the current year")
    report = asyncio.run(verify(args.manifest, start_year=args.start_year,
                                end_year=args.end_year, parse_workers=args.parse_workers,
                                source_csv=args.source_csv))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({"complete": report["complete"], "source_rows": report["source_rows"],
                      "missing": report["missing_database_rows"], "extra": report["unexpected_database_rows"],
                      "duplicates": report["duplicate_database_record_ids"], "mismatches": report["mismatched_rows"],
                      "output": str(args.output)}, ensure_ascii=False))
    if args.require_complete and not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
