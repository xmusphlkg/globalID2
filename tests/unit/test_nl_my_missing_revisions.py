"""Two real store ingests: source withdrawal, explicit zero and window retention.

SQLite executes the production PostgreSQL INSERT/ON CONFLICT statements here.
JSON booleans use PostgreSQL's text representation so ->> returns 'true' in
both engines; only the PostgreSQL advisory-lock primitive is stubbed.
"""
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from src.data.crawlers.nl_weekly import parse_virological_csv, parse_respiratory_resource, RESP_PROFILES
from src.data.crawlers import my_who_flunet as flu
from src.data.storage import series_observation_store as storage
from src.domain import DiseaseSeriesObservation, DiseaseSeriesObservationRevision, DiseaseSurveillanceSeries, SourceDiseaseCategory

FIXTURES = Path(__file__).parents[1] / 'fixtures/ingestion'


def snapshots(kind):
    if kind == 'nl_virological':
        payload = (FIXTURES / 'NL/virological_weekly.csv').read_bytes()
        parse = lambda raw: [row for row in parse_virological_csv(raw, as_of=date(2026, 11, 16))[0] if row['RawDiseaseLabel'] == 'Influenza A virus']
        before = parse(payload)
        after = parse(payload.replace(b'Influenza A virus;65', b'Influenza A virus;'))
        # Existing older source weeks outside a subsequent rolling response
        # must survive. They share the storage anchor, but differ in dimensions.
        return 'NL', 'SRC_NL_RIVM_VIROLOGICAL', before[:3], after[:2]
    if kind == 'nl_respiratory':
        name = 'tab_nps_zorg_ari_inc_website'
        resource = {'name': name, 'id': 'test-resource'}
        config = {'dataSrc': '?resource_id=test-resource', 'categoryStart': 40,
            'groupKey': ['season'], 'yAxis': {'title': 'per 100.000'}}
        parse = lambda payload: parse_respiratory_resource(payload.encode(), resource=resource, config=config,
            source_url='https://example.test/source.csv', metadata_modified='2026-11-16')[0]
        return 'NL', 'SRC_NL_RIVM_RESPIRATORY', parse('season;week;value\n2026/2027;40;65\n2026/2027;41;0\n2026/2027;42;9\n'), parse('season;week;value\n2026/2027;40;\n2026/2027;41;0\n')
    root = FIXTURES / 'MY'
    raw = json.loads((root / 'who_flunet_sample.json').read_bytes())['value'][-1]
    fields = flu.detection_fields(json.loads((root / 'who_flunet_dictionary.json').read_bytes())['value'])
    parse = lambda items: [row for row in flu.parse_records([(item, {}) for item in items], fields=fields, as_of=date(2026, 11, 16))[0] if row['SourceDiseaseCode'] == 'INF_A']
    old = copy.deepcopy(raw); old['ORIGIN_SOURCE'] = 'NONSENTINEL'
    zero = copy.deepcopy(raw); zero['ORIGIN_SOURCE'] = 'NOTDEFINED'; zero['INF_A'] = 0
    missing = copy.deepcopy(raw); missing['INF_A'] = None
    return 'MY', flu.ONTOLOGY_SOURCE_ID, parse([raw, zero, old]), parse([missing, zero])


def pg_json(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, dict):
        return {key: pg_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [pg_json(item) for item in value]
    return value


@pytest.mark.parametrize('kind', ['nl_virological', 'nl_respiratory', 'my_flunet'])
@pytest.mark.parametrize('mapped', [False, True], ids=['holding', 'mapped'])
async def test_explicit_missing_replaces_same_identity_and_retains_unoffered_history(kind, mapped, monkeypatch):
    country, source, before, after = snapshots(kind)
    engine = create_async_engine('sqlite+aiosqlite://', json_serializer=lambda value: json.dumps(pg_json(value)))
    store = storage.SeriesObservationStore()
    monkeypatch.setattr(storage, 'acquire_country_data_mutation_lock', AsyncMock())
    first = before[0]
    registry = {'status': 'active', 'id': 'TEST_REVIEWED', 'source_id': source, 'measure': first['Measure'],
        'frequency': 'weekly', 'rollup_policy': 'no_auto_rollup', 'unit': first['Unit']}
    monkeypatch.setattr(store, '_resolve_series', lambda **kwargs: [registry] if mapped else [])
    try:
        async with engine.begin() as db:
            for model in (DiseaseSurveillanceSeries, SourceDiseaseCategory, DiseaseSeriesObservation, DiseaseSeriesObservationRevision):
                await db.execute(CreateTable(model.__table__))
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            now = datetime.now(timezone.utc)
            if mapped:
                db.add(DiseaseSurveillanceSeries(series_code=registry['id'], country_code=country, source_system=source,
                    source_series_code=first['SourceDiseaseCode'], source_label=first['RawDiseaseLabel'],
                    target_group_code='unmapped', mapping_relation='exact'))
            else:
                db.add(SourceDiseaseCategory(category_key='TEST_SOURCE_CATEGORY', country_code=country,
                    source_id=source, source_code=first['SourceDiseaseCode'], canonical_source_label=first['RawDiseaseLabel'],
                    normalized_label=first['RawDiseaseLabel'].lower(), definition_version=first['DefinitionVersion'],
                    first_seen_at=now, last_seen_at=now))
            await db.flush()
            policy = storage.SeriesObservationQualityPolicy(mode='fail_closed')
            initial = await store.save_rows(db, before, country, source_id=source, quality_policy=policy)
            await db.commit()
            initial_rows = (await db.execute(select(DiseaseSeriesObservation))).scalars().all()
            identities = {(row.time, row.series_code, row.dimension_key) for row in initial_rows}
            assert initial.upserted == 3
            # Expire ORM facts so the second SELECT reads the actual UPDATE.
            db.expire_all()
            revised = await store.save_rows(db, after, country, source_id=source, quality_policy=policy)
            await db.commit()
            rows = (await db.execute(select(DiseaseSeriesObservation))).scalars().all()
            assert revised.upserted == 2
            assert {(row.time, row.series_code, row.dimension_key) for row in rows} == identities
            missing = next(row for row in rows if row.raw_data['MissingValueStatus'] == 'source_missing')
            assert missing.value is None and missing.suppressed is False
            assert missing.metadata_['missing_value_status'] == 'source_missing'
            retained = [row for row in rows if row.raw_data == before[2]]
            assert len(retained) == 1 and retained[0].value == float(before[2]['Cases'])
            if kind != 'nl_virological':
                assert any(row.value == 0 and row.raw_data['MissingValueStatus'] == 'reported' for row in rows)
            history = (await db.execute(select(DiseaseSeriesObservationRevision))).scalars().all()
            assert any(row.value is None for row in history)
            assert any(row.value == float(before[0]['Cases']) for row in history)
    finally:
        await engine.dispose()
