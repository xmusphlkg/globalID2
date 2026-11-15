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
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.crawlers.br import (
    DEFAULT_SOURCE_NAME,
    BrazilSINANCrawler,
    _deduplicate_sinan_file_aliases,
)
from src.data.crawlers.br_cache import SINANContentCache
from src.data.processors.br import BRMonthlyUpdater, BRUpdateImportResult
from src.data.processors.br_subdivisions import (
    STATE_CODES,
    coalesce_source_rows,
    projected_dbf_records,
    record_month,
    residence_state,
)

CACHE_VERSION = "residence-notification-month-v2"


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
    counts, months, unknown, date_fields = Counter(), Counter(), Counter(), Counter()
    undated = 0
    with tempfile.TemporaryDirectory(prefix="globalid_br_states_") as directory:
        dbf = Path(directory) / (item.filename + ".dbf")
        crawler._decompress_to_dbf(dbc, dbf)
        try:
            for record in projected_dbf_records(dbf):
                year, month, basis = record_month(record, item.year)
                if basis == "year_only_fallback":
                    undated += 1
                    continue  # An annual-only observation has no defensible monthly bucket.
                months[(year, month)] += 1
                date_fields[basis] += 1
                state = residence_state(record)
                if state is None:
                    unknown[(year, month)] += 1
                else:
                    counts[(state, year, month)] += 1
        except LookupError:
            payload = {
                "file": item.filename,
                "excluded": "no residence geography fields",
                "rows": [],
            }
            cache.write(payload)
            return payload
    if not counts:
        payload = {
            "file": item.filename,
            "excluded": "no valid residence state observations",
            "rows": [],
        }
        cache.write(payload)
        return payload
    rows = []
    for year, month in sorted(months):
        for state in sorted("BR-" + s for s in STATE_CODES.values()):
            rows.append(
                {
                    "CountryCode": state,
                    "GeographyKey": f"country:{state}:national",
                    "Date": f"{year:04d}-{month:02d}-01",
                    "Year": str(year),
                    "Month": str(month),
                    "DiseaseCode": item.prefix,
                    "RawDiseaseLabel": item.disease_name,
                    "Cases": str(counts[(state, year, month)]),
                    "DatasetStatus": item.dataset_status,
                    "SourceFiles": item.filename,
                    "SourceURLs": item.url,
                    "Source": DEFAULT_SOURCE_NAME,
                    "GeographyBasis": "residence",
                    "DateBasis": "notification_then_symptom_diagnosis_accident",
                    "SourceUpdateCadence": "closed_revisable",
                    "SourceAuthoritative": True,
                }
            )
    if sum(counts.values()) + sum(unknown.values()) != sum(months.values()):
        raise ValueError("State + unknown residence counts do not reconcile")
    payload = {
        "version": CACHE_VERSION,
        "signature": signature,
        "file": item.filename,
        "source_url": item.url,
        "sha256": digest,
        "record_count": sum(months.values()) + undated,
        "dated_record_count": sum(months.values()),
        "assigned_count": sum(counts.values()),
        "unknown_residence_count": sum(unknown.values()),
        "undated_count": undated,
        "date_fields": dict(date_fields),
        "rows": rows,
    }
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


async def apply(rows):
    from sqlalchemy import text

    from src.core.database import dispose_database, get_db
    from src.services.crawl_service import CrawlService
    from src.services.ingestion.manual_runs import record_manual_store

    try:
        async with get_db() as db:
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
    parser.add_argument("--force", action="store_true", help="Refresh directory listings, redownload and reparse selected files")
    args = parser.parse_args()
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
    items = [
        i
        for i in index
        if start <= i.year <= today.year
        and i.prefix not in {"NTRA", "SDTA"}
        and (not args.prefixes or i.prefix in args.prefixes)
    ]
    if not items:
        raise ValueError("No official files match the requested period")
    output = ROOT / "data/processed/br/subdivisions"
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
        report["import"] = asyncio.run(apply(rows))
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
