#!/usr/bin/env python3
"""Refresh missing rates in a local site snapshot using reviewed populations.

This repairs snapshots exported before official subdivision populations were
imported. It retains observations and publication dates and never publishes a
release. Normal database exports already calculate these rates themselves.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.site_subdivision_maps import reported_series, parent_code, normalise_population


def fill_rates(entry: dict, country: str, populations: dict, *, as_of: date | None = None) -> int:
    """Fill only reviewed whole-population counts with an exact reporting year."""
    series = reported_series(entry, parent_code({'code': country}), as_of=as_of, location_code=country)
    if not series:
        return 0
    indices = {when: index for index, when in enumerate(entry.get('dates') or [])}
    additions = []
    for (when, count, _), period in zip(series['points'], series['periods']):
        index = indices[when]
        rates = entry.get('incidence_rates') or []
        if index < len(rates) and rates[index] is not None:
            continue
        population = populations.get(str(period['reporting_year']))
        if count is not None and population:
            additions.append((index, count / population['value'] * 100000, population))
    if not additions:
        return 0
    for key in ('incidence_rates', 'incidence_sources', 'population_values', 'population_sources'):
        values = list(entry.get(key) or [])
        entry[key] = values + [None] * (len(indices) - len(values))
    for index, rate, population in additions:
        entry['incidence_rates'][index] = rate
        entry['incidence_sources'][index] = population['source'].lower() + '_computed'
        entry['population_values'][index] = population['value']
        entry['population_sources'][index] = population['source']
    entry['population_rate_provenance'] = populations
    return len(additions)


def refresh_country(data: dict, populations: dict) -> int:
    # Observation snapshots have no corresponding historical population ledger.
    if data.get('recorded_at_as_of'):
        return 0
    country = data['country_code']
    try:
        as_of = date.fromisoformat(str(data.get('generated_at') or '')[:10])
    except ValueError:
        as_of = None
    count = 0
    for entry in (data.get('disease_series') or {}).values():
        if entry.get('recorded_at_as_of'):
            continue
        count += fill_rates(entry, country, populations, as_of=as_of)
        for source in entry.get('source_series') or []:
            if source.get('recorded_at_as_of'):
                continue
            candidate = {
                **source, 'cases': source.get('values'),
                'period_granularity': source.get('temporal_granularity'),
                'selected_series_codes': [source.get('series_code')], 'source_series': [source],
                'quality_statuses': source.get('point_quality_statuses'),
            }
            added = fill_rates(candidate, country, populations, as_of=as_of)
            if added:
                for key in ('incidence_rates', 'incidence_sources', 'population_values', 'population_sources', 'population_rate_provenance'):
                    source[key] = candidate[key]
                source['has_incidence_rates'] = True
                count += added
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'astro-site/src/data')
    parser.add_argument('--public', type=Path, default=ROOT / 'astro-site/public/site-data')
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    if any((args.public / name).exists() for name in ('current.json', 'bootstrap.json')):
        parser.error('Use a mutable local site snapshot; versioned publications must be regenerated through data release')
    from src.core.database import get_session_maker, dispose_database
    from src.generation.site_subdivision_maps import fetch_map_populations
    from src.generation.site_data_views import build_country_site_data, build_country_source_series_data, build_disease_site_data
    from src.generation.site_data_writer import write_pretty_json, write_compact_json
    from src.generation.surveillance_data_export import country_summary
    async with get_session_maker()() as session:
        rows = await fetch_map_populations(session)
    await dispose_database()
    populations = {}
    for row in rows:
        normalized = normalise_population(row)
        if normalized:
            populations.setdefault(row['country_code'], {})[str(row['year'])] = normalized
    def save(path, value, compact=False):
        relative = path.relative_to(args.state if path.is_relative_to(args.state) else args.public)
        backup = args.backup / ('state' if path.is_relative_to(args.state) else 'public') / relative
        if path.exists() and not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        (write_compact_json if compact else write_pretty_json)(path, value)
    changed = {}; summary = Counter()
    def rate_count(data):
        entries = (data.get('disease_series') or {}).values()
        return sum(sum(value is not None for value in record.get('incidence_rates') or [])
                   for entry in entries for record in [entry, *(entry.get('source_series') or [])])
    for country, population in sorted(populations.items()):
        path = args.state / 'countries' / f'{country.lower()}.json'
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        backup = args.backup / 'state/countries' / path.name
        previous_count = rate_count(json.loads(backup.read_text())) if backup.exists() else rate_count(data)
        refresh_country(data, population)
        additions = rate_count(data) - previous_count
        if additions:
            changed[country] = data['disease_series']
            # Force later scoped projection to rebuild its input-summary receipt.
            data.pop('_incremental_projection_inputs', None)
            basis = data.setdefault('incidence_rate_basis', {})
            sources = Counter(source for entry in data['disease_series'].values() for source in entry.get('incidence_sources') or [] if source)
            basis['source_counts'] = dict(sources)
            basis['population_source'] = ' / '.join(sorted({source.removesuffix('_computed').upper() for source in sources if source.endswith('_computed')})) or None
            save(path, data)
            save(args.public / 'countries' / path.name, build_country_site_data(data), True)
            save(args.public / 'countries' / f'{country.lower()}-source-series.json', build_country_source_series_data(data), True)
            summary_path = args.public / 'countries' / f'{country.lower()}-summary.json'
            if summary_path.exists():
                previous = json.loads(summary_path.read_text())
                save(summary_path, country_summary(data, generated_at=previous.get('generated_at'), download=previous.get('download')), True)
            summary[country] = additions
    for path in sorted((args.state / 'diseases').glob('*.json')):
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            continue
        did = data.get('disease_id'); updated = False
        for country, series in (data.get('country_series') or {}).items():
            entry = changed.get(country, {}).get(did)
            if not entry or series.get('dates') != entry.get('dates') or series.get('cases') != entry.get('cases'):
                continue
            for key in ('incidence_rates', 'incidence_sources', 'population_values', 'population_sources', 'population_rate_provenance', 'source_series'):
                if key in entry:
                    series[key] = entry[key]
            updated = True
        if updated:
            save(path, data)
            existing = json.loads((args.public / 'diseases' / path.name).read_text())
            names = {s['cc']: s.get('n') for s in existing.get('series', [])}
            names_zh = {s['cc']: s.get('n_zh') for s in existing.get('series', [])}
            save(args.public / 'diseases' / path.name, build_disease_site_data(data, names, names_zh), True)
    report = {'locations': len(summary), 'added_rate_points_including_sources': sum(summary.values()), 'by_location': dict(summary)}
    args.backup.mkdir(parents=True, exist_ok=True)
    (args.backup / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
