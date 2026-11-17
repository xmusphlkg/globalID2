"""Actual SPF record contracts and complete offline paginated replay."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers.spf_overseas import DATASETS, count_value, parse_records, metadata_url, records_url
from src.data.processors.spf_overseas import SPFOverseasUpdater
from src.services.ingestion.countries.spf_overseas import organize

FIXTURE = Path(__file__).parents[1] / 'fixtures/ingestion/SPF'


def metadata():
    return {d: json.loads((FIXTURE / (d + '-metadata.json')).read_text()) for d in DATASETS}


def records():
    return [(d, r, {}) for d in DATASETS for r in json.loads((FIXTURE / (d + '-sample.json')).read_text())['results']]


def parse(raw=None, meta=None):
    return parse_records(records() if raw is None else raw, country_code='GP', metadata=metadata() if meta is None else meta, as_of=date(2026, 11, 16))


def test_real_records_keep_territories_counts_strata_and_rates():
    rows = parse()
    assert len(rows) == 10
    assert {r['SourceDataset'] for r in rows} == set(DATASETS)
    assert all(r['GeographyKey'] == 'country:GP:national' for r in rows)
    assert any(r['Cases'] == '0' for r in rows)
    assert all(r['Measure'] == 'case_notifications' and r['Unit'] == 'count' for r in rows)
    assert all('SourceIncidenceRatePer100000' in r for r in rows)
    measles = [r for r in rows if 'rougeole' in r['SourceDataset']]
    assert len({r['SourceDiseaseCode'] for r in measles}) == 2
    assert all(r['AggregationPolicy'] == 'non_additive' for r in rows)


@pytest.mark.parametrize('raw,expected', [(0, ('0', 'reported')), (None, ('', 'source_missing')), ('<5', ('<5', 'source_suppressed')), ('*', ('*', 'source_suppressed')), (51.0, ('51', 'reported'))])
def test_counts_distinguish_missing_zero_suppression(raw, expected):
    assert count_value(raw) == expected


@pytest.mark.parametrize('raw', [True, -1, 1.2, float('nan'), 'unknown'])
def test_invalid_counts_are_rejected(raw):
    with pytest.raises(ValueError):
        count_value(raw)


@pytest.mark.parametrize('change', [{'dep': '972'}, {'reglib': 'Martinique'}, {'annee': '2026'}, {'nbcas': 0.5}])
def test_country_calendar_and_metric_drift_fails_closed(change):
    d = 'hepatite-a-donnees-declaration-obligatoire'
    source = next(deepcopy(r) for r in records() if r[0] == d)
    source[1].update(change)
    with pytest.raises(ValueError):
        parse([source])


def test_metadata_and_duplicate_conflicts_rejected():
    meta = metadata()
    meta[next(iter(meta))]['metas']['default']['title'] = 'Other disease'
    with pytest.raises(ValueError, match='title'):
        parse(meta=meta)
    source = records()[0]
    with pytest.raises(ValueError, match='Duplicate'):
        parse([source, source])


def test_tuberculosis_change_in_department_basis_creates_definition_boundary():
    d = 'tuberculose-donnees-declaration-obligatoire_dep'
    _, raw, _ = next(deepcopy(r) for r in records() if r[0] == d)
    before, after = deepcopy(raw), deepcopy(raw)
    before['annee'], after['annee'] = '2018', '2019'
    rows = parse([(d, before, {}), (d, after, {})])
    assert rows[0]['ReportingBasis'] == 'notification_department'
    assert rows[1]['ReportingBasis'] == 'residence_department'
    assert rows[0]['DefinitionVersion'] != rows[1]['DefinitionVersion']


def artifact(tmp_path, payload, *, dataset, endpoint, offset=None):
    body = json.dumps(payload).encode()
    path = tmp_path / 'data/raw/spf' / f'{dataset}-{endpoint}-{offset}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    url = records_url(dataset, 'GP', offset=offset or 0) if endpoint == 'spf_records' else metadata_url(dataset)
    return SimpleNamespace(relative_path=str(path), byte_size=len(body), sha256=hashlib.sha256(body).hexdigest(), source_url=url,
        metadata_={'dataset':dataset, 'endpoint':endpoint, 'country_code':'GP', 'offset':offset,
                   'requested_url':url, 'retrieved_at':'2026-11-16T00:00:00Z'})


def captured(tmp_path):
    artifacts = []
    for d in DATASETS:
        m = metadata()[d]
        artifacts.extend(artifact(tmp_path, m, dataset=d, endpoint=e) for e in ['spf_metadata', 'spf_metadata_final'])
        sample = json.loads((FIXTURE / (d + '-sample.json')).read_text())
        artifacts.append(artifact(tmp_path, sample, dataset=d, endpoint='spf_records', offset=0))
    return artifacts


def test_offline_replay_and_all_history_refresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **k: pytest.fail('offline organizer used network'))
    artifacts = captured(tmp_path)
    updater = SPFOverseasUpdater('GP')
    result = organize(artifacts, updater=updater, months=[(2026, 11)])
    assert len(result.rows) == 10 and all(r['RawSHA256'] for r in result.rows)
    assert result.source_audit['coverage_complete'] is True
    assert updater.country_code == 'GP' and updater.ontology_source_id == 'SRC_GP_SPF_ODISSE'
    assert updater.public_release_enabled is False
    assert updater.prepare_rows(result.rows, months=[(2026, 11)]).rows == result.rows


def test_incomplete_duplicate_and_tampered_pages_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifacts = captured(tmp_path)
    with pytest.raises(ValueError, match='Missing'):
        organize(artifacts[1:], updater=SPFOverseasUpdater('GP'))
    with pytest.raises(ValueError, match='Discontinuous|duplicate'):
        organize(artifacts + [artifacts[-1]], updater=SPFOverseasUpdater('GP'))
    Path(artifacts[-1].relative_path).write_bytes(b'{}')
    with pytest.raises(ValueError, match='integrity'):
        organize(artifacts, updater=SPFOverseasUpdater('GP'))
