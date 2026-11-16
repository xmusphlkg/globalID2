"""Compare every Malawi IDSR source row against committed series observations."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from src.core.database import dispose_database, get_db
from src.data.processors.mw import MalawiPHIMIDSRUpdater
from src.domain.collection import SourceArtifact
from src.services.ingestion.countries import mw

CHECK_FIELDS = (
    "Date", "SourceDiseaseCode", "GeographyKey", "DefinitionVersion", "Cases",
    "MissingValueStatus", "Measure", "PeriodType", "ReportingBasis",
    "SourceURL", "SourceListingURL", "RawSHA256", "SourceReportYear", "SourceReportWeek",
)


async def verify(task_uuid: str, output: Path):
    async with get_db() as db:
        artifacts = (await db.execute(select(SourceArtifact).where(
            SourceArtifact.task_uuid == task_uuid).order_by(SourceArtifact.id))).scalars().all()
    if not artifacts:
        raise ValueError("No captured Malawi artifacts for task")
    fetched = mw.organize(artifacts, updater=MalawiPHIMIDSRUpdater(), start_year=2025)
    expected = {(row["Date"], row["SourceDiseaseCode"], row["GeographyKey"],
                 row["DefinitionVersion"]): row for row in fetched.rows}
    if len(expected) != len(fetched.rows):
        raise ValueError("Duplicate identities in captured source rows")
    async with get_db() as db:
        actual_rows = (await db.execute(text("""
            SELECT obs.raw_data, obs.value, obs.time, obs.geography_key,
                   series.source_series_code, series.definition_version
            FROM disease_series_observations obs
            JOIN disease_surveillance_series series USING (series_code)
            WHERE series.country_code='MW' AND series.source_system='SRC_MW_PHIM_IDSR'
        """))).fetchall()
    actual = {}
    integrity_issues = []
    duplicate_keys = []
    for raw, value, when, geography, code, version in actual_rows:
        key = (raw.get("Date"), raw.get("SourceDiseaseCode"), raw.get("GeographyKey"),
               raw.get("DefinitionVersion"))
        if key in actual:
            duplicate_keys.append(key)
        actual[key] = raw
        if ((raw.get("Cases") in ("", None) and value is not None)
                or (raw.get("Cases") not in ("", None) and value != float(raw["Cases"]))
                or raw.get("Date") != when.date().isoformat()
                or raw.get("GeographyKey") != geography
                or raw.get("SourceDiseaseCode") != code
                or raw.get("DefinitionVersion") != version):
            integrity_issues.append(key)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatched = [key for key in sorted(set(expected) & set(actual))
                  if any(expected[key].get(field) != actual[key].get(field) for field in CHECK_FIELDS)]
    audit = fetched.source_audit
    report = {
        "task_uuid": task_uuid,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_rows": len(fetched.rows), "database_rows": len(actual_rows),
        "source_series": len({row["SourceDiseaseCode"] for row in fetched.rows}),
        "first_observation": min(row["Date"] for row in fetched.rows),
        "last_observation": max(row["Date"] for row in fetched.rows),
        "captured_artifacts": len(artifacts),
        "captured_pdfs": sum(artifact.kind == "cda_pdf" for artifact in artifacts),
        "parsed_reports": audit["parsed_reports"],
        "expected_reports": audit["expected_reports"],
        "failed_downloads": audit["failed_downloads"],
        "missing_database_keys": missing[:20], "unexpected_database_keys": extra[:20],
        "mismatched_database_keys": mismatched[:20],
        "duplicate_database_keys": duplicate_keys[:20],
        "value_or_identity_issues": integrity_issues[:20],
        "pass": len(actual_rows) == len(fetched.rows)
                and not (missing or extra or mismatched or duplicate_keys or integrity_issues),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failed_downloads"}, ensure_ascii=False), flush=True)
    if not report["pass"]:
        raise SystemExit("Malawi source and database observations differ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_uuid")
    parser.add_argument("--output", type=Path, default=Path("data/current/mw/database_acceptance.json"))
    args = parser.parse_args()

    async def execute():
        try:
            await verify(args.task_uuid, args.output)
        finally:
            await dispose_database()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
