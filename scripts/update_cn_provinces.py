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
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import get_db, init_database  # noqa: E402
from src.core.db_schema import ensure_country_scope, ensure_country_scope_schema  # noqa: E402
from src.data.crawlers.cn_provinces import (  # noqa: E402
    ProvinceDataCenterCrawler,
    ProvinceMonthlyReportCrawler,
    load_phsm_history_with_audit,
    province_configs,
)
from src.data.processors.cn_provinces import CNProvinceUpdater  # noqa: E402


async def _ensure_province_rows(db) -> None:
    await ensure_country_scope_schema(db)
    for code, item in province_configs().items():
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
                    metadata = EXCLUDED.metadata, is_active = true,
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


def _select_monthly_rows(province: str, year: int | None, month: int | None) -> tuple[list[dict[str, object]], dict[str, object]]:
    crawler = ProvinceMonthlyReportCrawler()
    links = crawler.discover(province)
    discovered_count = len(links)
    if year is not None:
        links = [item for item in links if item.report_date.year == year]
    if month is not None:
        links = [item for item in links if item.report_date.month == month]
    if year is None and month is None:
        links = links[-3:]
    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for link in links:
        try:
            rows.extend(crawler.fetch(link).rows)
        except Exception as exc:
            errors.append({"date": link.report_date.isoformat(), "url": link.url, "error": str(exc)})
    return rows, {
        "province": province,
        "discovered": discovered_count,
        "selected": len(links),
        "rows": len(rows),
        "errors": errors,
    }


def _select_all_monthly_rows(year: int | None, month: int | None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
    for province, config in sorted(province_configs().items()):
        if config.parser in {"narrative_only", "discovery_pending"}:
            statuses.append({
                "province": province,
                "status": config.parser,
                "rows": 0,
                "errors": [],
            })
            continue
        try:
            province_rows, status = _select_monthly_rows(province, year, month)
            status["status"] = "parsed" if province_rows else "no_parsed_rows"
            rows.extend(province_rows)
            statuses.append(status)
        except Exception as exc:
            statuses.append({
                "province": province,
                "status": "failed_closed",
                "rows": 0,
                "errors": [{"error": str(exc)}],
            })
    print(json.dumps({"monthly_jurisdictions": statuses}, ensure_ascii=False, indent=2))
    return rows


def _load_rows(args: argparse.Namespace) -> list[dict[str, object]]:
    if args.command == "history":
        loaded = load_phsm_history_with_audit(
            args.workbook,
            include_datacenter=not args.monthly_only,
            include_monthly_reports=not args.datacenter_only,
        )
        print(json.dumps({"history_audit": loaded.audit.as_dict()}, ensure_ascii=False, indent=2))
        return loaded.rows
    if args.command == "datacenter":
        crawler = ProvinceDataCenterCrawler(
            request_interval=args.request_interval,
            max_workers=args.workers,
        )
        year = args.year if args.year is not None else max(crawler.available_years())
        return crawler.fetch_year(year)
    if args.command == "monthly":
        if args.all_provinces:
            return _select_all_monthly_rows(args.year, args.month)
        rows, status = _select_monthly_rows(args.province, args.year, args.month)
        print(json.dumps({"monthly_jurisdictions": [status]}, ensure_ascii=False, indent=2))
        return rows
    raise ValueError(args.command)


async def _run(args: argparse.Namespace) -> int:
    rows = _load_rows(args)
    summary = {
        "command": args.command,
        "rows": len(rows),
        "sources": sorted({str(row.get("SourceID")) for row in rows}),
        "provinces": len({str(row.get("JurisdictionCode")) for row in rows}),
        "period_start": min((str(row.get("Date")) for row in rows), default=None),
        "period_end": max((str(row.get("Date")) for row in rows), default=None),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        return 0
    if not rows:
        raise RuntimeError("No province rows were fetched; refusing an empty applied import")
    await init_database()
    async with get_db() as db:
        await _ensure_province_rows(db)
        await db.commit()
    chunk_size = max(1, int(getattr(args, "chunk_size", 5000)))
    imported = skipped = 0
    latest = None
    source_ids: set[str] = set()
    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]
        # Keep full-history refreshes below managed database connection limits.
        # Every chunk is an idempotent upsert transaction, so an interrupted run
        # can be retried without deleting already committed observations.
        async with get_db() as db:
            result = await CNProvinceUpdater().import_rows(db, chunk)
            await db.commit()
        imported += result.inserted_or_updated
        skipped += result.skipped_rows
        source_ids.update(result.source_ids)
        if result.source_latest_date is not None:
            latest = max(latest, result.source_latest_date) if latest else result.source_latest_date
        print(json.dumps({
            "import_progress": {
                "processed": min(offset + len(chunk), len(rows)),
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
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    history = subparsers.add_parser("history")
    history.add_argument("--workbook", type=Path, required=True)
    source_group = history.add_mutually_exclusive_group()
    source_group.add_argument("--datacenter-only", action="store_true")
    source_group.add_argument("--monthly-only", action="store_true")
    history.add_argument("--apply", action="store_true")
    history.add_argument("--chunk-size", type=int, default=5000)

    datacenter = subparsers.add_parser("datacenter")
    datacenter.add_argument("--year", type=int)
    datacenter.add_argument("--request-interval", type=float, default=0.2)
    datacenter.add_argument("--workers", type=int, choices=range(1, 17), default=6)
    datacenter.add_argument("--apply", action="store_true")
    datacenter.add_argument("--chunk-size", type=int, default=5000)

    monthly = subparsers.add_parser("monthly")
    monthly_scope = monthly.add_mutually_exclusive_group(required=True)
    monthly_scope.add_argument("--province", choices=sorted(province_configs()))
    monthly_scope.add_argument("--all-provinces", action="store_true")
    monthly.add_argument("--year", type=int)
    monthly.add_argument("--month", type=int, choices=range(1, 13))
    monthly.add_argument("--apply", action="store_true")
    monthly.add_argument("--chunk-size", type=int, default=5000)
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
