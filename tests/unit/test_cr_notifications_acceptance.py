"""Independent CR acceptance: stable revisions and capture-dated replay."""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pdfplumber
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from src.data.crawlers import cr_notifications as source
from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater
from src.data.storage import series_observation_store as storage
from src.domain import DiseaseSeriesObservation, DiseaseSeriesObservationRevision, DiseaseSurveillanceSeries, SourceDiseaseCategory
from src.services.ingestion.countries.cr_notifications import organize
from tests.unit.test_nl_my_missing_revisions import pg_json

FIXTURES = Path(__file__).parents[1] / 'fixtures/ingestion/CR'
REPORT = {'url': source.INDIVIDUAL_URL + '/1673-morbilidad-2015/file',
          'kind': 'annual_individual', 'year': 2015}


@pytest.fixture(scope='module')
def annual_text():
    with pdfplumber.open(FIXTURES / 'morbidity_2015.pdf') as pdf:
        for page, original in enumerate(pdf.pages[:15], 1):
            text = original.extract_text() or ''
            if 'VIH* 875 18,11' in text:
                return page, text
    pytest.fail('Reviewed annual fixture no longer contains its national VIH row')


@pytest.mark.parametrize('mapped', [False, True], ids=['holding', 'mapped'])
async def test_footnote_removal_and_count_revision_update_one_persisted_identity(annual_text, mapped, monkeypatch):
    page, text = annual_text
    before = [r for r in source.parse_annual_text(text, REPORT, page)[0] if r['RawDiseaseLabel'] == 'VIH*']
    after = [r for r in source.parse_annual_text(text.replace('VIH* 875 18,11', 'VIH 876 18,13'), REPORT, page)[0]
             if r['RawDiseaseLabel'] == 'VIH']
    assert len(before) == len(after) == 1
    assert before[0]['SourceDiseaseCode'] == after[0]['SourceDiseaseCode']
    assert before[0]['Dimensions'] == after[0]['Dimensions']
    engine = create_async_engine('sqlite+aiosqlite://', json_serializer=lambda value: json.dumps(pg_json(value)))
    store = storage.SeriesObservationStore()
    monkeypatch.setattr(storage, 'acquire_country_data_mutation_lock', AsyncMock())
    first = before[0]
    registry = {'status': 'active', 'id': 'CR_REVIEWED_VIH', 'source_id': source.ONTOLOGY_SOURCE_ID,
                'measure': first['Measure'], 'frequency': 'annual', 'rollup_policy': 'no_auto_rollup', 'unit': 'count'}
    monkeypatch.setattr(store, '_resolve_series', lambda **kwargs: [registry] if mapped else [])
    try:
        async with engine.begin() as db:
            for model in (DiseaseSurveillanceSeries, SourceDiseaseCategory, DiseaseSeriesObservation, DiseaseSeriesObservationRevision):
                await db.execute(CreateTable(model.__table__))
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            now = datetime.now(timezone.utc)
            if mapped:
                db.add(DiseaseSurveillanceSeries(series_code=registry['id'], country_code='CR',
                    source_system=source.ONTOLOGY_SOURCE_ID, source_series_code=first['SourceDiseaseCode'],
                    source_label=first['RawDiseaseLabel'], target_group_code='unmapped', mapping_relation='exact'))
            else:
                db.add(SourceDiseaseCategory(category_key='CR_TEST_CATEGORY', country_code='CR',
                    source_id=source.ONTOLOGY_SOURCE_ID, source_code=first['SourceDiseaseCode'],
                    canonical_source_label='VIH', normalized_label='vih', definition_version=first['DefinitionVersion'],
                    first_seen_at=now, last_seen_at=now))
            await db.flush()
            policy = storage.SeriesObservationQualityPolicy(mode='fail_closed')
            initial = await store.save_rows(db, before, 'CR', source_id=source.ONTOLOGY_SOURCE_ID, quality_policy=policy)
            await db.commit()
            row = (await db.execute(select(DiseaseSeriesObservation))).scalar_one()
            identity = (row.time, row.series_code, row.dimension_key)
            assert initial.upserted == 1 and row.value == 875 and row.raw_data['IsProvisional'] == 'true'
            db.expire_all()
            revised = await store.save_rows(db, after, 'CR', source_id=source.ONTOLOGY_SOURCE_ID, quality_policy=policy)
            await db.commit()
            row = (await db.execute(select(DiseaseSeriesObservation))).scalar_one()
            assert revised.upserted == 1 and row.value == 876
            assert (row.time, row.series_code, row.dimension_key) == identity
            assert row.raw_data['RawDiseaseLabel'] == 'VIH' and row.raw_data['IsProvisional'] == 'false'
            history = (await db.execute(select(DiseaseSeriesObservationRevision))).scalars().all()
            assert {r.value for r in history} == {875, 876}
    finally:
        await engine.dispose()


def test_annual_closure_uses_explicit_as_of_not_current_clock():
    payload = (FIXTURES / 'morbidity_2015.pdf').read_bytes()
    with pytest.raises(source.CostaRicaContractError, match='closed calendar year'):
        source.parse_report_pdf(payload, REPORT, as_of=date(2015, 12, 31))
    assert len(source.parse_report_pdf(payload, REPORT, as_of=date(2016, 1, 1))[0]) == 64


@pytest.mark.parametrize('captured_at,accepted', [
    ('2016-01-01T00:00:00Z', False),  # Still 2015 in Costa Rica.
    ('2016-01-01T06:00:00Z', True),
    ('2016-01-01T00:00:00-06:00', True),
])
def test_offline_replay_uses_captured_costa_rica_calendar_year(tmp_path, monkeypatch, captured_at, accepted):
    original = (FIXTURES / 'morbidity_2015.pdf').read_bytes()
    monkeypatch.chdir(tmp_path)
    directory = Path('data/raw/cr_acceptance'); directory.mkdir(parents=True)
    listing = f'<a href="{REPORT["url"]}">pdf Morbilidad 2015</a>'.encode()
    reports = source.discover(listing, source.INDIVIDUAL_URL)[2]
    assert len(reports) == 1

    def artifact(name, data, url, metadata):
        path = directory / name; path.write_bytes(data)
        return SimpleNamespace(relative_path=str(path), source_url=url, metadata_=metadata,
                               sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))

    plan = {'reports': reports, 'failures': [], 'start_year': 2014, 'end_year': 2015,
            'directories': [source.INDIVIDUAL_URL], 'captured_at': captured_at}
    artifacts = [artifact('index.html', listing, source.INDIVIDUAL_URL, {'endpoint': 'directory'}),
                 artifact('annual.pdf', original, REPORT['url'], {'endpoint': 'report', 'report': reports[0]}),
                 artifact('plan.json', json.dumps(plan).encode(), source.BULLETINS_URL, {'endpoint': 'plan'})]
    monkeypatch.setattr(source.CostaRicaNotificationsCrawler, 'get', lambda *a, **kw: pytest.fail('Replay used network'))
    if accepted:
        assert len(organize(artifacts, updater=CostaRicaNotificationsUpdater()).rows) == 64
    else:
        with pytest.raises(ValueError, match='No verified'):
            organize(artifacts, updater=CostaRicaNotificationsUpdater())


def test_future_bulletin_cannot_enter_an_older_capture():
    with pytest.raises(source.CostaRicaContractError, match='after captured'):
        source.parse_report_pdf((FIXTURES / 'bulletin_28_2026.pdf').read_bytes(),
            {'url': source.BULLETINS_URL + '/2026/file', 'kind': 'bulletin', 'year': 2026}, as_of=date(2026, 8, 6))
