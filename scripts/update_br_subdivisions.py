#!/usr/bin/env python3
"""Aggregate official SINAN microdata by residence state and notification month.

Defaults to historical coverage. --apply imports source + legacy records and
--export queues a site release. --recent-years 2 is suitable for weekly revisions.
Never substitutes notifying UF for residence. National cases are untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.crawlers.br import (
    BrazilSINANCrawler,
    _deduplicate_sinan_file_aliases,
)
from src.data.crawlers.br_cache import SINANContentCache
from src.data.processors.br import BRMonthlyUpdater, BRUpdateImportResult
from src.data.processors.br_subdivisions import (
    aggregate_residence_file,
    coalesce_source_rows,
    projected_dbf_records,
    record_month,
    residence_state,
)

__all__ = ["coalesce_source_rows", "projected_dbf_records", "record_month", "residence_state"]

CACHE_VERSION = "residence-notification-month-v3"


def aggregate_file(item, *, force=False):
    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=ROOT / "data/raw/br")
    crawler.force_refresh = force
    try:
        return _aggregate_file(crawler, item, force=force)
    finally:
        crawler.session.close()


def _aggregate_file(crawler, item, *, force=False):
    signature = crawler._file_cache_signature(item)
    dbc = crawler._download_file(item, crawler.download_cache_dir(item))
    with dbc.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    cache = SINANContentCache(
        crawler.cache_dir / "content", item, digest, version=CACHE_VERSION,
    )
    payload = cache.read() if not force else None
    if payload is not None:
        return payload
    with tempfile.TemporaryDirectory(prefix="globalid_br_states_") as directory:
        dbf = Path(directory) / (item.filename + ".dbf")
        crawler._decompress_to_dbf(dbc, dbf)
        try:
            aggregated = aggregate_residence_file(item, dbf)
        except LookupError:
            payload = {
                "file": item.filename,
                "source_url": item.url,
                "sha256": digest,
                "excluded": "no residence geography fields",
                "rows": [],
            }
            cache.write(payload)
            return payload
    payload = {
        "version": CACHE_VERSION,
        "signature": signature,
        "file": item.filename,
        "source_url": item.url,
        "sha256": digest,
        "record_count": aggregated["dated_record_count"] + aggregated["undated_count"],
        **aggregated,
    }
    if not aggregated["rows"]:
        payload["excluded"] = "no valid residence state observations"
    cache.write(payload)
    return payload


class StateBatchUpdater(BRMonthlyUpdater):
    series_registered_rows_only = False
    series_geography_from_rows = True

    def __init__(self):
        super().__init__()
        self.series_geography_key = None

    async def import_rows(
        self, db, rows, *, db_latest_date, source_latest_date, force=False
    ):
        by_state = defaultdict(list)
        for row in rows:
            by_state[row["CountryCode"]].append(row)
        inserted, unmapped = 0, 0
        for code, records in sorted(by_state.items()):
            result = await BRMonthlyUpdater(country_code=code).import_rows(
                db,
                records,
                db_latest_date=None,
                source_latest_date=source_latest_date,
                force=force,
            )
            inserted += result.inserted_or_updated
            unmapped += result.skipped_unmapped
        return BRUpdateImportResult(
            inserted, unmapped, db_latest_date, source_latest_date, bool(inserted)
        )


async def apply(rows, *, reviewed_revisions=None):
    from sqlalchemy import text

    from src.core.database import dispose_database, get_db
    from src.services.crawl_service import CrawlService
    from src.services.ingestion.manual_runs import record_manual_store

    try:
        async with get_db() as db:
            if reviewed_revisions is not None:
                from src.core.disease_mutation_lock import (
                    acquire_country_data_mutation_lock,
                )
                from src.services.ingestion.br_reviewed_revisions import (
                    reviewed_residence_rows,
                )

                await acquire_country_data_mutation_lock(db, "BR")
                rows = await reviewed_residence_rows(db, rows, reviewed_revisions, root=ROOT)
            result = await CrawlService._import_rows_with_series(
                db,
                StateBatchUpdater(),
                rows,
                db_latest_date=None,
                source_latest_date=date.fromisoformat(max(r["Date"] for r in rows)),
                force=True,
            )
            codes = sorted({r["CountryCode"] for r in rows})
            await db.execute(
                text(
                    "UPDATE countries SET is_active=true, "
                    "metadata=(metadata::jsonb || jsonb_build_object("
                    "'population_only', false, 'disease_geography_basis', 'residence'))::json "
                    "WHERE code=:code"
                ),
                [{"code": code} for code in codes],
            )
            audit = await record_manual_store(
                db, line="surveillance", scope_keys=codes,
                operation="update_br_subdivisions",
                metrics={"upserted": result.inserted_or_updated,
                         "unmapped": result.skipped_unmapped},
            )
            return {
                "task_uuid": audit.task_uuid,
                "upserted": result.inserted_or_updated,
                "unmapped": result.skipped_unmapped,
            }
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--recent-years", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prefixes", nargs="+")
    parser.add_argument("--reviewed-revisions", type=Path, help="Exact source-proven correction receipt; requires --apply")
    parser.add_argument("--output-dir", type=Path, help="Write candidate CSV and audit reports to an isolated directory")
    parser.add_argument("--force", action="store_true", help="Refresh directory listings, redownload and reparse selected files")
    args = parser.parse_args()
    if args.reviewed_revisions and not args.apply:
        parser.error("--reviewed-revisions requires --apply")
    if args.export and not args.apply:
        parser.error("--export requires --apply")
    if args.recent_years is not None and args.recent_years < 1:
        parser.error("--recent-years must be positive")
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    start = today.year - args.recent_years + 1 if args.recent_years else args.start_year
    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=ROOT / "data/raw/br")
    crawler.force_refresh = args.force
    try:
        index = _deduplicate_sinan_file_aliases(crawler.fetch_file_index())
    finally:
        crawler.session.close()
    # NTRA is a survey aggregate, SDTA counts outbreak rows, not individual cases.
    requested_prefixes = {prefix.upper() for prefix in (args.prefixes or [])}
    if "PFA" in requested_prefixes:
        requested_prefixes.add("PFAN")
    items = [
        i
        for i in index
        if start <= i.year <= today.year
        and i.prefix not in {"NTRA", "SDTA"}
        and (not requested_prefixes or i.prefix in requested_prefixes)
    ]
    if not items:
        raise ValueError("No official files match the requested period")
    output = args.output_dir or ROOT / "data/processed/br/subdivisions"
    output.mkdir(parents=True, exist_ok=True)
    reports, failures, rows = [], [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(aggregate_file, item, force=args.force): item for item in items}
        for n, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            try:
                payload = future.result()
                rows.extend(payload.pop("rows"))
                reports.append(payload)
            except Exception as exc:  # noqa: BLE001 -- preserve every source failure for audit
                failures.append({"file": item.filename, "error": str(exc)})
                print(json.dumps(failures[-1]), flush=True)
            if n % 10 == 0 or n == len(items):
                print(
                    json.dumps(
                        {
                            "files_completed": n,
                            "files_total": len(items),
                            "rows": len(rows),
                            "failures": len(failures),
                        }
                    ),
                    flush=True,
                )
    # Only closed reporting months within the requested years; do not invent future observations.
    rows = [
        r
        for r in rows
        if start <= int(r["Year"])
        and date.fromisoformat(r["Date"]) < today.replace(day=1)
    ]
    rows = coalesce_source_rows(rows)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "applied": False,
        "rows": len(rows),
        "files": reports,
        "failures": failures,
        "excluded_prefixes": ["NTRA", "SDTA"],
        "policy": "residence, never notifying UF; unknown residence retained in audit; no population interpolation",
    }
    (output / "fetch_report.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures or not rows:
        raise RuntimeError(
            "Source fetch is incomplete; no database import performed; inspect fetch_report.json"
        )
    rows.sort(key=lambda r: (r["CountryCode"], r["Date"], r["DiseaseCode"]))
    with (output / "state_monthly.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if args.apply:
        from src.data.storage.series.quality import SeriesObservationQualityError

        try:
            if args.reviewed_revisions:
                reviewed = json.loads(args.reviewed_revisions.read_text())
                report["reviewed_revision_receipt"] = str(args.reviewed_revisions)
                report["reviewed_revision_receipt_sha256"] = hashlib.sha256(
                    json.dumps(reviewed, sort_keys=True).encode()
                ).hexdigest()
                report["import"] = asyncio.run(apply(rows, reviewed_revisions=reviewed))
            else:
                report["import"] = asyncio.run(apply(rows))
        except (SeriesObservationQualityError, ValueError, TypeError) as exc:
            # Retain the rejection alongside the exact candidate/source hashes.
            # The import transaction has rolled back; no observations are approved
            # and no publication is queued after this failure.
            if isinstance(exc, SeriesObservationQualityError):
                report["quality_report"] = exc.report.to_dict()
            else:
                report["review_error"] = str(exc)
            (output / "import_report.json").write_text(json.dumps(report, indent=2) + "\n")
            raise
        report["applied"] = True
    report["coverage"] = {
        code: {
            "rows": len(selected),
            "first_date": min(r["Date"] for r in selected),
            "last_date": max(r["Date"] for r in selected),
        }
        for code in sorted({r["CountryCode"] for r in rows})
        if (selected := [r for r in rows if r["CountryCode"] == code])
    }
    (output / "import_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in report.items() if k not in {"files"}}), flush=True
    )
    if args.export:
        from src.services.data_release_service import data_release_service
        print(json.dumps(asyncio.run(data_release_service.trigger_job(
            "site-release", manual=True, trigger="update-br-subdivisions-cli"
        )), default=str))


if __name__ == "__main__":
    main()
