#!/usr/bin/env python3
"""Read-only, reproducible mapping-to-country snapshot acceptance.

Writes private review artifacts. It neither enables publication nor creates a
publication acknowledgement; a passing snapshot still needs normal deployment.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.config import get_config
from src.core.country_library import get_country_bootstrap_config, get_country_display_name
from src.generation.public_series_store import _catalogue_context
from src.generation.site_data_about import build_country_source_info
from src.generation.site_data_dependencies import dependency_version
from src.generation.site_data_queries import fetch_disease_series_records
from src.generation.site_data_views import build_country_data
from src.generation.site_series_projection import apply_series_first_projection, validate_series_first_projection
from src.services.surveillance_onboarding_review import UNPUBLISHED_COUNTRY_CODES


def check_projected_values(records: list[dict], source_records: list[dict]) -> None:
    """Every generic point must equal an admitted, unsummed source total."""
    originals = {
        (row['series_code'], row['date'], row['disease_id']): row['cases']
        for row in source_records
    }
    for row in records:
        key = (row.get('series_code'), row['date'], row['disease_id'])
        if key not in originals or originals[key] != row['cases']:
            raise ValueError(f"Projected point does not match its original source cell: {key}")


def save_json(path: Path, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, indent=2).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


async def verify(args) -> dict:
    countries = sorted(args.countries or UNPUBLISHED_COUNTRY_CODES)
    unknown = set(countries) - UNPUBLISHED_COUNTRY_CODES
    if unknown:
        raise ValueError(f"Countries outside this acceptance scope: {sorted(unknown)}")
    catalogue = _catalogue_context()
    fingerprint = dependency_version()
    verifier_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    engine = create_async_engine(get_config().database.url)
    report = {
        'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'read_only_private_snapshot_acceptance', 'published': False,
        'projection_dependency_sha256': fingerprint, 'verifier_sha256': verifier_sha256,
        'countries': [],
    }
    try:
        async with AsyncSession(engine) as db:
            await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            await db.execute(text("SET LOCAL statement_timeout = '90s'"))
            report['mapping_release'] = dict((await db.execute(text(
                "SELECT id,release_code,checksum FROM disease_mapping_releases_v3 WHERE status='active'"
            ))).mappings().one())
            country_configs = {row['code']: dict(row) for row in (await db.execute(text(
                'SELECT code, name, data_source_url, data_source_type, crawler_config, metadata FROM countries'
            ))).mappings()}
            for code in countries:
                config = get_country_bootstrap_config(code)
                item = {
                    'code': code, 'snapshot_passed': False,
                    'public_release_enabled': config.get('public_release_enabled', True) is not False,
                    'public_source_systems': config.get('public_source_systems', []),
                }
                try:
                    async with db.begin_nested():
                        rows = await fetch_disease_series_records(db, code, False)
                        projected = apply_series_first_projection([], rows)
                        validate_series_first_projection(projected)
                        check_projected_values(projected, rows)
                        frequencies = sorted({row['temporal_granularity'] for row in projected})
                        frequency_meta = {
                            'source_frequency': frequencies[0].upper() if len(frequencies) == 1 else 'MIXED',
                            'source_frequencies': [v.upper() for v in frequencies],
                            'canonical_frequency': 'SOURCE_REPORTED_PERIODS',
                            'aggregation_rule': 'preserve_source_period_counts',
                        }
                        data = build_country_data(code, get_country_display_name(code), projected, catalogue['diseases_by_id'], frequency_meta, rows)
                        data['source_info'] = build_country_source_info(
                            code, frequency_meta, database_config=country_configs.get(code),
                        )
                        item.update({
                            'admitted_source_observations': len(rows), 'projected_points': len(projected),
                            'projected_diseases': sorted({row['disease_id'] for row in projected}),
                            'sources': dict(Counter(row['source_system'] for row in rows)),
                            'source_metrics': dict(Counter(row['metric_type'] for row in rows)),
                            'frequencies': frequencies, 'snapshot_passed': bool(projected),
                            'unpaired_deaths_remain_unknown': all(row.get('deaths') is None for row in projected),
                        })
                        if not projected:
                            item['reason'] = 'No reviewed, source-total case curve passed the source, dimension and calendar contracts.'
                        else:
                            item['snapshot_sha256'] = save_json(args.output / 'countries' / f'{code.lower()}.json', data)
                except Exception as exc:
                    item['reason'] = f'{type(exc).__name__}: {exc}'
                item['public_export_snapshot_passed'] = item['snapshot_passed'] and item['public_release_enabled']
                report['countries'].append(item)
                save_json(args.output / 'acceptance.json', report)
                print(json.dumps(item, ensure_ascii=False), flush=True)
            await db.rollback()
    finally:
        await engine.dispose()
    report['passed'] = sum(item['snapshot_passed'] for item in report['countries'])
    report['public_export_snapshots_passed'] = sum(item['public_export_snapshot_passed'] for item in report['countries'])
    report['failed'] = len(countries) - report['passed']
    report['all_snapshots_passed'] = report['failed'] == 0
    if dependency_version() != fingerprint or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != verifier_sha256:
        raise RuntimeError('Projection sources changed during acceptance; rerun against the final source versions.')
    report['source_fingerprint_verified'] = True
    save_json(args.output / 'acceptance.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country', dest='countries', action='append')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/onboarding/unpublished-publication')
    args = parser.parse_args()
    result = asyncio.run(verify(args))
    print(json.dumps({k: result[k] for k in ('passed', 'failed', 'all_snapshots_passed')}, ensure_ascii=False))
    raise SystemExit(0 if result['all_snapshots_passed'] else 1)


if __name__ == '__main__':
    main()
