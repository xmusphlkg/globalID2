"""DDS recovery budgets follow native calendars and per-category persisted facts."""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from src.data.crawlers.th_dds import parse_calendar_workbook
from src.data.crawlers.th_tableau import source_code
from src.data.processors.th_dds import ThailandDDSWeeklyUpdater
from src.services.crawl_service import CrawlService
from src.services.ingestion.countries import th_dds, th_tableau, th_refresh
from src.domain import DiseaseSeriesObservation, DiseaseSurveillanceSeries

CALENDAR = parse_calendar_workbook((Path(__file__).parents[1] / 'fixtures/ingestion/TH/dds/calendar_2026.xlsx').read_bytes(), year=2026)
# Explicit annual boundary fixture, used as a captured source response by the
# fake calendar endpoint; production never derives calendar weeks from dates.
NEXT = {1: (date(2027, 1, 1), date(2027, 1, 2)), 2: (date(2027, 1, 3), date(2027, 1, 9)),
    3: (date(2027, 1, 10), date(2027, 1, 16))}
NOW = datetime(2027, 1, 10, 12, tzinfo=timezone.utc)


def state(codes=('15', '45')):
    rows = [(code, end.isoformat(), NOW.isoformat()) for code in codes for week, (_, end) in CALENDAR.items()
        if date(2026, 10, 10) <= end and week <= 51]
    rows.extend((code, NEXT[1][1].isoformat(), NOW.isoformat()) for code in codes)
    return {'coverage': rows, 'revision_window_months': 3, 'max_weeks_per_category': 4}


def test_cross_year_catchup_preserves_short_native_week_and_inventory_partial_failure():
    current = state()
    # One disease was captured at year end; MAX(time) is identical for both.
    current['coverage'].append(('15', CALENDAR[52][1].isoformat(), NOW.isoformat()))
    plan = th_refresh.RefreshPlan({2026: CALENDAR, 2027: NEXT}, current, as_of=NOW.date(), now=NOW)
    eligible = [(year, week) for year, calendar in plan.calendars.items() for week in calendar]
    assert plan.select('15', eligible) == [(2026, 53), (2027, 2)]
    assert plan.select('45', eligible) == [(2026, 52), (2026, 53), (2027, 2)]
    assert (2027, 3) not in plan.select('45', eligible)  # open/future week
    assert CALENDAR[53] == (date(2026, 12, 27), date(2026, 12, 31))


def test_long_outage_outside_revision_window_drains_without_repeating_whole_history():
    current = {'coverage': [('15', CALENDAR[week][1].isoformat(), NOW.isoformat()) for week in (1, 2, 3, 4)],
        'revision_window_months': 3, 'max_weeks_per_category': 4}
    plan = th_refresh.RefreshPlan({2026: CALENDAR}, current, as_of=date(2026, 11, 15), now=NOW)
    first = plan.select('15', [(2026, week) for week in CALENDAR])
    assert len(first) == 4 and first[0] == (2026, 5) and first[-1] == (2026, 46)
    # Keep the earliest failed period missing while successful rotated work
    # advances to other missing weeks without waiting for that failure.
    current['coverage'].extend(('15', CALENDAR[week][1].isoformat(), NOW.isoformat())
        for _, week in first if week != 5)
    plan = th_refresh.RefreshPlan({2026: CALENDAR}, current, as_of=date(2026, 11, 15), now=NOW + timedelta(minutes=30))
    second = plan.select('15', [(2026, week) for week in CALENDAR])
    assert second[0] == (2026, 5) and second[-1] == (2026, 46)
    assert len(second) == 4 and not (set(first[1:-1]) & set(second[1:-1]))
    assert len(plan.select('new-source-category', [(2026, week) for week in CALENDAR])) == 4


def test_recent_revision_rotation_does_not_starve_behind_fresh_latest_week():
    old = NOW - timedelta(days=3)
    current = {'coverage': [('15', end.isoformat(), old.isoformat()) for _, end in CALENDAR.values()],
        'revision_window_months': 3, 'max_weeks_per_category': 4}
    eligible = [(2026, week) for week in CALENDAR]
    plan = th_refresh.RefreshPlan({2026: CALENDAR}, current, as_of=date(2026, 11, 15), now=NOW)
    first = plan.select('15', eligible)
    assert len(first) == 4 and first[-1] == (2026, 46)
    assert all(CALENDAR[week][1] >= date(2026, 8, 15) for _, week in first)
    updated = {CALENDAR[week][1].isoformat() for _, week in first}
    current['coverage'] = [(code, end, NOW.isoformat() if end in updated else stamp) for code, end, stamp in current['coverage']]
    second = th_refresh.RefreshPlan({2026: CALENDAR}, current, as_of=date(2026, 11, 15), now=NOW).select('15', eligible)
    assert set(first) & set(second) == {(2026, 46)}


class Writer:
    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata):
        self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata))


async def fake_calendar(writer, crawler, *, year):
    return {2026: CALENDAR, 2027: NEXT}[year]


async def test_pdf_collect_uses_global_budget_and_retries_failed_category_week(monkeypatch):
    posted = []

    class Crawler:
        session = SimpleNamespace(close=lambda: None)

        def get(self, url):
            if url == th_dds.FORM_URL:
                payload = '<input name="_token" value="session-secret">'
            elif url == th_dds.NATION_URL:
                payload = '<select name="f_dz_gr"><option value="1">Group</option></select>'
            elif '/dzg/' in url:
                payload = '<option value="15">Flu</option><option value="45">Anthrax</option>'
            elif '/year/' in url:
                year = int(url.split('/year/')[1].split('/')[0])
                payload = ''.join(f'<option value="{week}-{week}">Week</option>' for week in {2026: CALENDAR, 2027: NEXT}[year])
            else:
                payload = '<option value="2026">2569</option><option value="2027">2570</option>'
            return SimpleNamespace(content=payload.encode())

        def post(self, url, *, data):
            posted.append((data['f_dz'], int(data['f_year']), int(data['f_wk_start_end'].split('-')[0])))
            return SimpleNamespace(content=b'%PDF captured report')

    monkeypatch.setattr(th_dds, 'ThailandDDSCrawler', Crawler)
    monkeypatch.setattr(th_dds, 'capture_calendar', fake_calendar)
    writer = Writer()
    await th_dds.collect(writer, as_of=NOW.date(), refresh_state=state())
    for code in ('15', '45'):
        assert {(year, week) for disease, year, week in posted if disease == code} == {(2026, 52), (2026, 53), (2027, 2)}
    assert len(posted) == 6  # one shared budget, never four reports per year
    plans = [json.loads(a.payload) for a in writer.artifacts if a.metadata_['endpoint'] == 'collection_plan']
    assert {plan['year'] for plan in plans} == {2026, 2027}
    assert all(not plan['collection_errors'] for plan in plans)
    assert all(b'session-secret' not in a.payload for a in writer.artifacts)


async def test_tableau_collect_uses_sparse_per_category_plan_across_years(monkeypatch):
    posted = []
    labels = ['Flu', 'Anthrax']

    class Crawler:
        entries = []

        def close(self):
            pass

        def load(self):
            return {'labels': labels, 'years': [2026, 2027]}

        def select(self, field, value, **kwargs):
            if kwargs.get('report'):
                posted.append((kwargs['label'], kwargs['year'], kwargs['week']))

    current = state([source_code(label) for label in labels])
    current['coverage'].append((source_code('Flu'), CALENDAR[52][1].isoformat(), NOW.isoformat()))
    monkeypatch.setattr(th_tableau, 'ThailandDDSTableauCrawler', Crawler)
    monkeypatch.setattr(th_tableau, 'capture_calendar', fake_calendar)
    writer = Writer()
    await th_tableau.collect(writer, as_of=NOW.date(), refresh_state=current)
    assert set(posted) == {('Flu', 2026, 53), ('Anthrax', 2026, 52), ('Anthrax', 2026, 53),
        ('Flu', 2027, 2), ('Anthrax', 2027, 2)}
    bundles = [a for a in writer.artifacts if a.metadata_['endpoint'] == 'summary_bundle']
    assert len(bundles) == 2
    assert sum(len(th_tableau.unpack_bundle(a.payload)[1]['requested_reports']) for a in bundles) == 5


async def test_runtime_reads_real_coverage_with_source_and_basis_isolation(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite://')
    session = async_sessionmaker(engine)

    @asynccontextmanager
    async def get_db():
        async with session() as db:
            yield db

    monkeypatch.setattr(th_refresh, 'get_db', get_db)
    try:
        async with engine.begin() as db:
            for model in (DiseaseSurveillanceSeries, DiseaseSeriesObservation):
                await db.execute(CreateTable(model.__table__))
        async with session() as db:
            for code, source in [('PDF', 'SRC_TH_DDS_WEEKLY'), ('TABLEAU', 'SRC_TH_DDS_TABLEAU')]:
                db.add(DiseaseSurveillanceSeries(series_code=code, country_code='TH', source_system=source,
                    source_series_code=code, source_label=code, target_group_code='unmapped'))
            await db.flush()
            for series, disease, basis, week in [('PDF', '15', 'onset', 45), ('PDF', '45', 'onset', 46),
                    ('PDF', '15', 'gen', 46), ('TABLEAU', 'other', 'onset', 46)]:
                db.add(DiseaseSeriesObservation(time=datetime.combine(CALENDAR[week][1], datetime.min.time()),
                    series_code=series, geography_key='country:TH:national', dimension_key=disease+basis,
                    value=1, unit='count', raw_data={'SourceDiseaseCode': disease, 'TimeBasis': basis}))
            await db.commit()
        updater = ThailandDDSWeeklyUpdater()
        result = await th_refresh.runtime_options(updater, {}, default_basis='onset')
        assert {(code, end) for code, end, _ in result['refresh_state']['coverage']} == {
            ('15', CALENDAR[45][1].isoformat()), ('45', CALENDAR[46][1].isoformat())}
        # Manual selection must not touch storage or broaden the request.
        monkeypatch.setattr(th_refresh, 'get_db', lambda: pytest.fail('manual request loaded DB'))
        assert await th_refresh.runtime_options(updater, {'weeks': [45], 'basis': 'gen'}, default_basis='onset') == {'weeks': [45], 'basis': 'gen'}
    finally:
        await engine.dispose()


@pytest.mark.parametrize('scope,handler,basis', [('th_dds_weekly', '_execute_th_dds_weekly', 'onset'),
    ('th_dds_tableau', '_execute_th_tableau', 'report')])
async def test_automatic_dispatch_passes_revision_policy_and_actual_coverage(monkeypatch, scope, handler, basis):
    from src.services.ingestion import runner
    runtime = AsyncMock(return_value={'refresh_state': {'coverage': []}})
    monkeypatch.setattr(th_refresh, 'runtime_options', runtime)
    run = AsyncMock()
    monkeypatch.setattr(runner, 'run_surveillance', run)
    updater = SimpleNamespace(refresh_recent_months=5)
    await getattr(CrawlService(), handler)(task=SimpleNamespace(input_data={'revision_window_months': 5}),
        updater=updater, source=scope, force=False, process=True, save_raw=True, fill_missing=False)
    assert runtime.call_args.kwargs == {'default_basis': basis}
    assert run.call_args.kwargs['connector_options'] == runtime.return_value


def test_sparse_tableau_plan_replays_actual_raw_frames_without_fabricated_missing_reports(monkeypatch):
    import hashlib
    from src.data.processors.th_tableau import ThailandDDSTableauUpdater
    root = Path(__file__).parents[1] / 'fixtures/ingestion/TH'
    entries, plan = th_tableau.unpack_bundle((root / 'tableau/single_week_flu_report.zip').read_bytes())
    plan['weeks'] = [45, 46]
    plan['requested_reports'] = [{'label': plan['labels'][0], 'week': 46}]
    raw = th_tableau.bundle(entries, plan)
    parent = (root / 'dds/calendar_2026.rar').read_bytes()
    items = [SimpleNamespace(payload=raw, metadata_={'endpoint': 'summary_bundle', 'bundle_sha256': hashlib.sha256(raw).hexdigest()}),
        SimpleNamespace(payload=parent, metadata_={'endpoint': 'calendar_rar', 'year': 2026}),
        SimpleNamespace(payload=(root / 'dds/calendar_2026.xlsx').read_bytes(), metadata_={
            'endpoint': 'calendar_workbook', 'year': 2026, 'parent_raw_sha256': hashlib.sha256(parent).hexdigest()})]
    monkeypatch.setattr(th_tableau, 'read_artifact', lambda a: a.payload)
    monkeypatch.setattr('requests.Session.request', lambda *a, **k: pytest.fail('offline replay used network'))
    result = th_tableau.organize(items, updater=ThailandDDSTableauUpdater())
    assert result.source_audit['requested_reports'] == 1
    assert result.source_audit['status'] == 'validated'
    assert result.rows[0]['Cases'] == '32529'


@pytest.mark.parametrize('connector,endpoint', [(th_dds, 'collection_plan'), (th_tableau, 'summary_bundle')])
@pytest.mark.parametrize('first_empty', [False, True])
def test_multi_year_replay_organizes_each_plan_once_then_merges(connector, endpoint, first_empty, monkeypatch, tmp_path):
    from src.data.processors.th_dds import DDSFetchResult
    items = []
    for year in (2026, 2027):
        items.append(SimpleNamespace(payload=json.dumps({'year': year}).encode(), metadata_={'endpoint': endpoint, 'year': year}))
        items.append(SimpleNamespace(payload=b'calendar', metadata_={'endpoint': 'calendar_workbook', 'year': year}))
    if connector is th_dds:
        items.append(SimpleNamespace(payload=b'common', metadata_={'endpoint': 'disease_group'}))
    seen = []

    def organize_year(artifacts, *, updater, **kwargs):
        plans = [a for a in artifacts if a.metadata_['endpoint'] == endpoint]
        assert len(plans) == 1
        year = plans[0].metadata_['year']
        seen.append(year)
        # Tableau selects its matching calendar inside the real organizer;
        # PDF partitions selectors and reports before handing them to replay.
        if connector is th_dds:
            assert {a.metadata_['year'] for a in artifacts if 'year' in a.metadata_} == {year}
        rows = [] if first_empty and year == 2026 else [{'Date': f'{year}-01-01', 'SourceDiseaseCode': '15'}]
        return DDSFetchResult(rows, date(year, 1, 1) if rows else None, tmp_path / 'out.csv', [], {
            'year': year, 'status': 'partial' if year == 2026 else 'validated', 'requested_reports': 1,
            'failed_reports': [{'year': year, 'error': 'retry me'}] if year == 2026 else []})

    monkeypatch.setattr(connector, 'read_artifact', lambda a: a.payload)
    monkeypatch.setattr(connector, '_organize_year', organize_year)
    result = connector.organize(items, updater=SimpleNamespace(output_csv=tmp_path / 'out.csv', source_scope='dds'))
    assert seen == [2026, 2027]
    assert result.source_latest_date == date(2027, 1, 1)
    assert len(result.rows) == (1 if first_empty else 2) and result.source_audit['requested_reports'] == 2
    assert result.source_audit['status'] == 'partial'
    assert result.source_audit['failed_reports'] == [{'year': 2026, 'error': 'retry me'}]


@pytest.mark.parametrize('step', [timedelta(minutes=30), timedelta(days=1)])
def test_permanent_missing_reports_cannot_starve_other_gaps_or_revisions(step):
    old = NOW - timedelta(days=3)
    # Three permanently failing reports plus later pending work. Already stored
    # window observations have real revisions due throughout the outage.
    missing = {5, 6, 7, 18, 19, 20}
    current = {'coverage': [('15', end.isoformat(), old.isoformat()) for week, (_, end) in CALENDAR.items() if week not in missing],
        'revision_window_months': 3, 'max_weeks_per_category': 4}
    attempted, revised = set(), set()
    for cycle in range(16):
        now = NOW + step * cycle
        plan = th_refresh.RefreshPlan({2026: CALENDAR}, current, as_of=date(2026, 11, 15), now=now)
        selected = plan.select('15', [(2026, week) for week in CALENDAR])
        assert len(selected) <= 4 and (2026, 46) in selected
        attempted.update(week for _, week in selected if week in missing)
        revision_weeks = {week for _, week in selected if week not in missing and week != 46}
        due_at_start = any(date(2026, 8, 15) <= date.fromisoformat(end) < CALENDAR[46][1]
            and datetime.fromisoformat(stamp) <= now - timedelta(hours=24)
            for _, end, stamp in current['coverage'])
        if due_at_start:
            assert revision_weeks  # retries retain a lane whenever revisions are due
        revised.update(revision_weeks)
        # Assume all missing attempts fail. Successful revisions still rotate.
        refreshed = {CALENDAR[week][1].isoformat() for week in revision_weeks | {46}}
        current['coverage'] = [(code, end, now.isoformat() if end in refreshed else stamp)
            for code, end, stamp in current['coverage']]
    assert attempted == missing
    assert len(revised) > 3


@pytest.mark.parametrize('budget', [2, 3])
def test_budget_requires_room_for_latest_revisions_and_missing_rotation(budget):
    with pytest.raises(ValueError, match='4..26'):
        th_refresh.RefreshPlan({2026: CALENDAR}, {'max_weeks_per_category': budget}, as_of=NOW.date())


def test_pdf_empty_automatic_year_keeps_failure_audit_but_manual_request_fails(tmp_path, monkeypatch):
    import hashlib
    from tests.unit.test_th_dds import _artifacts
    items = _artifacts(tmp_path, monkeypatch)
    items = [a for a in items if a.metadata_['endpoint'] != 'weekly_report']
    updater = ThailandDDSWeeklyUpdater()
    with pytest.raises(th_dds.DDSContractError, match='No validated'):
        th_dds._organize_year(items, updater=updater)
    artifact = next(a for a in items if a.metadata_['endpoint'] == 'collection_plan')
    path = Path(artifact.relative_path)
    plan = json.loads(path.read_bytes()); plan['refresh_policy'] = 'bounded_per_category_recovery_and_revision'
    payload = json.dumps(plan).encode(); path.write_bytes(payload)
    artifact.sha256 = hashlib.sha256(payload).hexdigest(); artifact.byte_size = len(payload)
    result = th_dds._organize_year(items, updater=updater)
    assert result.rows == [] and result.source_latest_date is None
    assert result.source_audit['status'] == 'partial'
    assert any(error.get('week') == 46 for error in result.source_audit['failed_reports'])
    with pytest.raises(ValueError, match='No verified'):
        th_dds.organize(items, updater=updater)


def test_tableau_empty_automatic_year_keeps_failure_audit_but_manual_request_fails(monkeypatch):
    import hashlib
    from src.data.processors.th_tableau import ThailandDDSTableauUpdater
    root = Path(__file__).parents[1] / 'fixtures/ingestion/TH'
    entries, plan = th_tableau.unpack_bundle((root / 'tableau/single_week_flu_report.zip').read_bytes())
    entries = [entry for entry in entries if not entry['metadata'].get('report')]
    parent = (root / 'dds/calendar_2026.rar').read_bytes()

    def artifacts():
        raw = th_tableau.bundle(entries, plan)
        return [SimpleNamespace(payload=raw, metadata_={'endpoint': 'summary_bundle', 'bundle_sha256': hashlib.sha256(raw).hexdigest()}),
            SimpleNamespace(payload=parent, metadata_={'endpoint': 'calendar_rar', 'year': 2026}),
            SimpleNamespace(payload=(root / 'dds/calendar_2026.xlsx').read_bytes(), metadata_={
                'endpoint': 'calendar_workbook', 'year': 2026, 'parent_raw_sha256': hashlib.sha256(parent).hexdigest()})]

    monkeypatch.setattr(th_tableau, 'read_artifact', lambda a: a.payload)
    updater = ThailandDDSTableauUpdater()
    with pytest.raises(th_tableau.TableauContractError, match='No valid'):
        th_tableau._organize_year(artifacts(), updater=updater)
    plan['refresh_policy'] = 'bounded_per_category_recovery_and_revision'
    result = th_tableau._organize_year(artifacts(), updater=updater)
    assert result.rows == [] and result.source_latest_date is None
    assert result.source_audit['status'] == 'partial'
    assert result.source_audit['failed_reports'][0]['week'] == 46
    with pytest.raises(ValueError, match='No verified'):
        th_tableau.organize(artifacts(), updater=updater)
