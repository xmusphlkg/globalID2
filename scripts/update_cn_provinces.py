#!/usr/bin/env python3
"""Import Chinese province history and refresh configured official sources.

Examples:
  venv/bin/python scripts/update_cn_provinces.py history --workbook /path/to/nation_and_provinces.xlsx --apply
  venv/bin/python scripts/update_cn_provinces.py datacenter --year 2021 --apply
  venv/bin/python scripts/update_cn_provinces.py monthly --province CN-LN --apply

Run ``scripts/sync_disease_ontology.py --apply`` once after deploying a new
ontology release and before the first applied province import.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema  # noqa: E402
from src.data.crawlers.cn_provinces import (  # noqa: E402
    ProvinceDataCenterCrawler,
    ProvinceMonthlyReportCrawler,
    load_phsm_history_with_audit,
    province_configs,
)
from src.data.processors.cn_provinces import CNProvinceUpdater  # noqa: E402
from src.data.storage.series_observation_store import SeriesObservationStore  # noqa: E402
from src.domain.disease_ontology import DiseaseSurveillanceSeries  # noqa: E402
from src.services.ingestion.countries.cn_province import (  # noqa: E402
    collection_status, diagnostic_errors, merge_report_rows, select_monthly_links,
)
from src.services.ingestion.stages import mark_dirty  # noqa: E402


async def _ensure_province_rows(db, codes=None) -> None:
    await ensure_country_scope_schema(db)
    for code, item in province_configs().items():
        if codes is not None and code not in codes:
            continue
        metadata = {
            "parent_country_code": "CN",
            "location_type": "subdivision",
            "iso_subdivision_code": code,
            "flag_country_code": "CN",
            "adcode": item.adcode,
        }
        crawler = {
            "sources": ["cn_province_datacenter", "cn_province_monthly_report"],
            "cadence": "mixed_annual_monthly",
            "geography_key": f"country:{code}:national",
            "index_url": item.index_url,
            "parser": item.parser,
        }
        await db.execute(
            text(
                """
                INSERT INTO countries (
                    code, name, name_en, name_local, language, timezone,
                    data_source_url, data_source_type, crawler_config,
                    parser_config, disease_mapping_rules, report_config,
                    is_active, metadata, notes, created_at, updated_at
                ) VALUES (
                    :code, :name, :name_en, :name_local, 'zh-CN', 'Asia/Shanghai',
                    :url, 'mixed', CAST(:crawler AS json), CAST(:parser AS json),
                    '{"strategy":"source_series_registry","fallback":"quarantine_unmapped"}'::json,
                    '{"default_type":"MONTHLY","lang":"zh-CN"}'::json,
                    true, CAST(:metadata AS json), :notes,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name, name_en = EXCLUDED.name_en,
                    name_local = EXCLUDED.name_local,
                    data_source_url = EXCLUDED.data_source_url,
                    crawler_config = EXCLUDED.crawler_config,
                    parser_config = EXCLUDED.parser_config,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "code": code,
                "name": item.name_en,
                "name_en": item.name_en,
                "name_local": item.name_zh,
                "url": item.index_url or None,
                "crawler": json.dumps(crawler, ensure_ascii=False),
                "parser": json.dumps({"primary": item.parser}, ensure_ascii=False),
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "notes": "Chinese provincial notifiable-disease observations; independent from CN national facts.",
            },
        )
        await ensure_country_scope(
            db,
            scope_code=code,
            country_code=code,
            scope_type="canonical",
            language_code="zh-CN",
            display_name=item.name_zh,
            is_default=True,
            is_active=True,
            metadata={"origin": "update_cn_provinces", **metadata},
        )


def _select_monthly_rows(province: str, year: int | None, month: int | None,
                         *, force=False, start_year=None, recent_months=3,
                         timeout=45) -> tuple[list[dict[str, object]], dict[str, object]]:
    crawler = ProvinceMonthlyReportCrawler(timeout=timeout)
    links = crawler.discover(province)
    discovered_count = len(links)
    discovery_diagnostics = list(getattr(crawler, "discovery_diagnostics", []))
    links = select_monthly_links(links, year=year, month=month, force=force,
                                 start_year=start_year, recent_months=recent_months)
    batches = []
    errors = diagnostic_errors(discovery_diagnostics, "discover")
    if (force or start_year is not None) and any(item.get("status") == "page_budget_reached" for item in discovery_diagnostics):
        errors.append({"stage": "discover", "error": "Historical discovery reached the page budget; coverage may be incomplete"})
    reports = []
    for link in links:
        report = {"date": link.report_date.isoformat(), "url": link.url, "title": link.title}
        try:
            fetched = crawler.fetch(link)
            images = getattr(fetched, "image_documents", [])
            if not fetched.rows and not images:
                raise ValueError("No explicit disease values found")
            batches.append((report, fetched.rows))
            diagnostics = list(getattr(crawler, "report_diagnostics", []))
            errors.extend(diagnostic_errors(diagnostics, "fetch"))
            reports.append({**report, "rows": len(fetched.rows),
                            "status": "needs_review" if images else collection_status(fetched.rows, diagnostic_errors(diagnostics, "fetch")),
                            "image_documents": [{key: doc[key] for key in ("source_url", "sha256", "media_type")} for doc in images],
                            "diagnostics": diagnostics,
                            "artifact_url": fetched.source_url,
                            "artifact_sha256": fetched.artifact_sha256})
        except Exception as exc:
            errors.append({**report, "stage": "fetch", "error": str(exc)})
    rows, conflicts = merge_report_rows(batches)
    errors.extend(conflicts)
    return rows, {
        "province": province,
        "discovered": discovered_count,
        "selected": len(links),
        "selected_months": sorted({link.report_date.isoformat()[:7] for link in links}),
        "rows": len(rows),
        "status": "needs_review" if any(item["status"] == "needs_review" for item in reports) else collection_status(rows, errors),
        "reports": reports,
        "discovery_diagnostics": discovery_diagnostics,
        "errors": errors,
    }


def _select_all_monthly_rows(year: int | None, month: int | None, *, audit=None,
                             provinces=None, workers=1, **options) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
    def collect_one(province):
        config = province_configs()[province]
        if config.parser == "discovery_pending":
            return [], {
                "province": province,
                "status": config.parser,
                "rows": 0,
                "errors": [],
            }
        try:
            return _select_monthly_rows(province, year, month, **options)
        except Exception as exc:
            return [], {
                "province": province,
                "status": "failed_closed",
                "rows": 0,
                "errors": [{"error": str(exc)}],
            }
    codes = sorted(provinces or province_configs())
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for province_rows, status in executor.map(collect_one, codes):
            rows.extend(province_rows)
            statuses.append(status)
    if audit is not None:
        audit["monthly_jurisdictions"] = statuses
    print(json.dumps({"monthly_jurisdictions": statuses}, ensure_ascii=False, indent=2))
    return rows


def _load_rows(args: argparse.Namespace) -> list[dict[str, object]]:
    args.collection_audit = {}
    if args.command == "import":
        package = load_rows_package(args.rows_file)
        args.collection_audit = package.get("collection_audit", {})
        args.input_rows_sha256 = package["rows_sha256"]
        return package["rows"]
    if args.command == "history":
        loaded = load_phsm_history_with_audit(
            args.workbook,
            include_datacenter=not args.monthly_only,
            include_monthly_reports=not args.datacenter_only,
        )
        print(json.dumps({"history_audit": loaded.audit.as_dict()}, ensure_ascii=False, indent=2))
        args.collection_audit["history_audit"] = loaded.audit.as_dict()
        return loaded.rows
    if args.command == "datacenter":
        crawler = ProvinceDataCenterCrawler(
            request_interval=args.request_interval,
            max_workers=args.workers,
        )
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        available = sorted(y for y in crawler.available_years()
                           if y < today.year or (y == today.year and today.month > 1))
        years = ([args.year] if args.year is not None else
                 [y for y in available if y >= args.start_year] if args.start_year else available[-1:])
        if not years or any(y not in available for y in years):
            raise ValueError("No published Data Center years match the requested window")
        rows, errors = [], []
        for year in years:
            jobs = [(year, disease, month) for disease in crawler.config["disease_ids"]
                    for month in range(1, today.month if year == today.year else 13)]
            def fetch_job(job):
                try:
                    return crawler._fetch_report(*job), None
                except Exception as exc:
                    return [], {"year": job[0], "disease_id": job[1], "month": job[2], "error": str(exc)}
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                for batch, error in executor.map(fetch_job, jobs):
                    rows.extend(batch)
                    if error:
                        errors.append(error)
        args.collection_audit["datacenter"] = {"years": years, "errors": errors,
            "status": collection_status(rows, errors), "rows": len(rows),
            "response_diagnostics": list(getattr(crawler, "response_diagnostics", [])),
            "scope": "all_provinces_one_download"}
        return rows
    if args.command == "monthly":
        return _select_all_monthly_rows(args.year, args.month, audit=args.collection_audit,
            provinces=None if args.all_provinces else [args.province], workers=args.workers,
            force=args.force, start_year=args.start_year, recent_months=args.recent_months,
            timeout=args.timeout)
    raise ValueError(args.command)


def _rows_digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode()).hexdigest()


def load_rows_package(path: Path) -> dict:
    """Read an immutable candidate without contacting sources or the database."""
    package = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(package, dict) or package.get("schema_version") != 1 or package.get("kind") != "cn_province_rows":
        raise ValueError("Expected a schema_version=1 cn_province_rows package")
    rows = package.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Rows package must contain a list of row objects")
    if package.get("rows_sha256") != _rows_digest(rows):
        raise ValueError("Rows package SHA-256 mismatch; regenerate the reviewed candidate")
    return package


def preflight_rows(rows):
    """Validate the whole candidate before any chunk is committed.

    Mapping, definitions, geography and cross-chunk conflicts are checked offline.
    The persistence layer retains its historical quality gates during apply.
    """
    from decimal import Decimal, InvalidOperation
    from src.data.processors.cn_provinces import expand_province_metric_rows

    provinces = province_configs()
    for position, row in enumerate(rows, 1):
        code = row.get("JurisdictionCode")
        if code not in provinces or row.get("GeographyKey") != f"country:{code}:national":
            raise ValueError(f"Row {position}: invalid or inconsistent province geography")
        if row.get("SourceID") not in CNProvinceUpdater.supported_source_ids:
            raise ValueError(f"Row {position}: unsupported province source")
        try:
            when = date.fromisoformat(str(row["Date"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Row {position}: invalid report month") from exc
        if when.day != 1 or when >= datetime.now(ZoneInfo("Asia/Shanghai")).date().replace(day=1):
            raise ValueError(f"Row {position}: monthly facts must use the first day of a completed month")
        for field in ("Cases", "Deaths"):
            value = row.get(field)
            if field == "Deaths" and value is None:
                continue
            if (field == "Cases" and value is None
                    and row.get("SourceID") == "SRC_CN_PROV_MONTHLY_REPORT"
                    and row.get("MissingValueStatus") == "source_missing"):
                continue
            try:
                count = Decimal(str(value))
                valid = not isinstance(value, bool) and count.is_finite() and count >= 0 and count == count.to_integral_value()
            except InvalidOperation:
                valid = False
            if not valid:
                raise ValueError(f"Row {position}: {field} must be an explicit nonnegative integer")
        if not row.get("SourceURL") or not row.get("DefinitionVersion"):
            raise ValueError(f"Row {position}: provenance and source definition are required")
    expanded = expand_province_metric_rows(rows)
    store = SeriesObservationStore()
    by_source = defaultdict(list)
    for row in expanded:
        by_source[row["SourceID"]].append(row)
    observations = []
    for source_id, source_rows in sorted(by_source.items()):
        built = store.build_observations(source_rows, "CN", source_id=source_id)
        failures = built.skipped_invalid + built.skipped_unmatched + built.skipped_ambiguous
        if failures:
            raise ValueError(f"Candidate mapping preflight failed for {source_id}: unmatched={built.skipped_unmatched}, "
                             f"ambiguous={built.skipped_ambiguous}, invalid={built.skipped_invalid}")
        observations.extend(built.observations)
    return {"source_rows": len(rows), "expanded_metric_rows": len(expanded),
            "unique_observations": len(observations),
            "duplicate_metric_rows": len(expanded) - len(observations),
            "required_series_codes": sorted({row["series_code"] for row in observations}),
            "provinces": sorted({row["JurisdictionCode"] for row in rows}),
            "status": "valid", "historical_quality_check": "performed_transactionally_on_apply"}


async def preflight_database(preflight):
    """Inspect Registry readiness with an explicitly read-only transaction."""
    async with get_db() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        present = set((await db.execute(select(DiseaseSurveillanceSeries.series_code).where(
            DiseaseSurveillanceSeries.series_code.in_(preflight["required_series_codes"])
        ))).scalars())
    missing = sorted(set(preflight["required_series_codes"]) - present)
    return {"missing_registry_series": missing, "status": "needs_registry_restore" if missing else "ready",
            "note": "Apply retains the store's transactional registry restoration and quality checks"}


def candidate_has_errors(audit):
    return bool(audit.get("datacenter", {}).get("errors") or any(
        item.get("errors") or item.get("status") in {"no_parsed_rows", "failed_closed", "needs_review"}
        for item in audit.get("monthly_jurisdictions", [])))


def import_chunks(rows, chunk_size):
    # Keep sparse narrative batches separate from full tables so their explicit
    # zero evidence receives the existing narrative quality policy consistently.
    groups = defaultdict(list)
    for row in rows:
        narrative = "explicit_narrative_value" in (row.get("Raw") or {})
        groups[(row["JurisdictionCode"], row["SourceID"], narrative)].append(row)
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
        for offset in range(0, len(ordered), chunk_size):
            yield ordered[offset:offset + chunk_size]


async def _run(args: argparse.Namespace) -> int:
    rows = _load_rows(args)
    preflight = preflight_rows(rows)
    summary = {
        "command": args.command,
        "rows": len(rows),
        "sources": sorted({str(row.get("SourceID")) for row in rows}),
        "provinces": len({str(row.get("JurisdictionCode")) for row in rows}),
        "period_start": min((str(row.get("Date")) for row in rows), default=None),
        "period_end": max((str(row.get("Date")) for row in rows), default=None),
        "collection_partial": candidate_has_errors(args.collection_audit),
        "preflight": preflight,
    }
    if args.preflight or args.apply:
        summary["database_preflight"] = await preflight_database(preflight)
    if args.output_rows:
        package = {"schema_version": 1, "kind": "cn_province_rows",
                   "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                   "rows_sha256": _rows_digest(rows), "rows": rows,
                   "collection_audit": args.collection_audit, "preflight": preflight}
        args.output_rows.parent.mkdir(parents=True, exist_ok=True)
        args.output_rows.write_text(json.dumps(package, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        summary["rows_file"] = str(args.output_rows)
        summary["rows_sha256"] = package["rows_sha256"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        return 2 if summary["collection_partial"] or not rows else 0
    if not rows:
        raise RuntimeError("No province rows were fetched; refusing an empty applied import")
    if summary["collection_partial"] and not args.allow_partial:
        raise RuntimeError("Candidate includes collection errors; review its audit and use --allow-partial to import only valid rows")
    async with get_db() as db:
        await _ensure_province_rows(db, preflight["provinces"])
        await db.commit()
    chunk_size = max(1, int(getattr(args, "chunk_size", 5000)))
    imported = skipped = processed = 0
    latest = None
    source_ids: set[str] = set()
    import_id = str(uuid4())
    for chunk in import_chunks(rows, chunk_size):
        # Keep full-history refreshes below managed database connection limits.
        # Every chunk is an idempotent upsert transaction, so an interrupted run
        # can be retried without deleting already committed observations.
        async with get_db() as db:
            result = await CNProvinceUpdater().import_rows(db, chunk)
            if result.skipped_rows:
                raise RuntimeError(f"Import skipped {result.skipped_rows} rows; refusing to commit this chunk")
            for code in sorted({row["JurisdictionCode"] for row in chunk}):
                await mark_dirty(db, line="surveillance", scope_key=code, task_uuid=import_id)
            await db.commit()
        imported += result.inserted_or_updated
        processed += len(chunk)
        skipped += result.skipped_rows
        source_ids.update(result.source_ids)
        if result.source_latest_date is not None:
            latest = max(latest, result.source_latest_date) if latest else result.source_latest_date
        print(json.dumps({
            "import_progress": {
                "processed": processed,
                "total": len(rows),
                "inserted_or_updated": imported,
                "skipped_rows": skipped,
            }
        }, ensure_ascii=False))
    print(json.dumps({
        "inserted_or_updated": imported,
        "source_rows": len(rows),
        "skipped_rows": skipped,
        "source_ids": sorted(source_ids),
        "source_latest_date": latest,
        "imported_new_data": imported > 0,
        "import_id": import_id,
        "collection_partial": summary["collection_partial"],
        "publication": "surveillance dirty markers committed; publication is a separate stage",
    }, ensure_ascii=False, indent=2, default=str))
    return 2 if summary["collection_partial"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    history = subparsers.add_parser("history")
    history.add_argument("--workbook", type=Path, required=True)
    source_group = history.add_mutually_exclusive_group()
    source_group.add_argument("--datacenter-only", action="store_true")
    source_group.add_argument("--monthly-only", action="store_true")

    datacenter = subparsers.add_parser("datacenter")
    datacenter.add_argument("--year", type=int)
    datacenter.add_argument("--start-year", type=int)
    datacenter.add_argument("--request-interval", type=float, default=0.2)
    datacenter.add_argument("--workers", type=int, choices=range(1, 17), default=6)

    monthly = subparsers.add_parser("monthly")
    monthly_scope = monthly.add_mutually_exclusive_group(required=True)
    monthly_scope.add_argument("--province", choices=sorted(province_configs()))
    monthly_scope.add_argument("--all-provinces", action="store_true")
    monthly.add_argument("--year", type=int)
    monthly.add_argument("--month", type=int, choices=range(1, 13))
    monthly.add_argument("--start-year", type=int)
    monthly.add_argument("--force", action="store_true", help="Read all discovered completed months, beyond the revision window")
    monthly.add_argument("--recent-months", type=int, choices=range(1, 25), default=3)
    monthly.add_argument("--workers", type=int, choices=range(1, 9), default=1)
    monthly.add_argument("--timeout", type=float, default=45)
    offline = subparsers.add_parser("import", help="Validate or import a reviewed rows package without refetching sources")
    offline.add_argument("--rows-file", type=Path, required=True)
    for command in (history, datacenter, monthly, offline):
        command.add_argument("--apply", action="store_true")
        command.add_argument("--preflight", action="store_true", help="Also inspect database Registry readiness using a read-only transaction")
        command.add_argument("--allow-partial", action="store_true", help="Explicitly allow valid rows despite recorded collection errors; exit remains 2")
        command.add_argument("--output-rows", type=Path, help="Write a checksummed candidate for review and later offline import")
        command.add_argument("--chunk-size", type=int, default=5000)
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
