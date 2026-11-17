"""An official refresh cannot approve unrelated, stale or unproven corrections."""
import hashlib
import shutil
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.storage.series.identity import _allows_authoritative_revision
from src.services.ingestion.br_reviewed_revisions import reviewed_residence_rows
from tests.unit.test_br_subdivisions import write_dbf


@pytest.fixture
def correction(tmp_path, monkeypatch):
    source_url = 'ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/PRELIM/EXANBR26.dbc'
    item = SINANFile('EXAN', 'Exanthematous diseases', 2026, 'EXANBR26.dbc', source_url,
                     'preliminary', 0, None)
    fields = [('SG_UF', 'C', 2), ('DT_NOTIFIC', 'D', 8)]
    artifacts, proofs = {}, []
    for version, state in [('previous', '35'), ('current', '33')]:
        path = tmp_path / f'data/raw/br/{version}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        write_dbf(path, fields, [(False, {'SG_UF': state, 'DT_NOTIFIC': '20260101'})])
        proof = {'relative_path': path.relative_to(tmp_path).as_posix(),
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        proofs.append(proof)
        artifacts[proof['relative_path']] = {
            'source_url': source_url, 'metadata': {'file': asdict(item)},
            'created_at': datetime(2026, 1, 1 if version == 'previous' else 2, tzinfo=timezone.utc),
        }
    row = {'CountryCode': 'BR-SP', 'GeographyKey': 'country:BR-SP:national',
           'DiseaseCode': 'EXAN', 'Date': '2026-01-01', 'Cases': '0',
           'SourceFiles': item.filename, 'SourceURLs': source_url}
    receipt = {'schema_version': 1, 'entries': [{
        'country_code': 'BR-SP', 'disease_code': 'EXAN', 'date': '2026-01-01',
        'series_code': 'HOLD_BR_EXAN', 'stored_value': 1, 'incoming_value': 0,
        'previous_artifact': proofs[0], 'current_artifact': proofs[1],
    }]}
    stored = {'value': 1, 'raw_data': {**row, 'Cases': '1'}}
    class DB:
        async def execute(self, statement, params):
            record = artifacts.get(params['path']) if 'path' in params else stored
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: record))
    monkeypatch.setattr(BrazilSINANCrawler, '_decompress_to_dbf', staticmethod(shutil.copyfile))
    return SimpleNamespace(root=tmp_path, db=DB(), rows=[row], receipt=receipt,
                           artifacts=artifacts, stored=stored)


@pytest.mark.asyncio
async def test_only_proven_cell_receives_revision_semantics(correction):
    c = correction
    unrelated = {**c.rows[0], 'CountryCode': 'BR-BA', 'GeographyKey': 'country:BR-BA:national'}
    result = await reviewed_residence_rows(c.db, c.rows + [unrelated], c.receipt, root=c.root)
    assert _allows_authoritative_revision(result[0])
    assert result[0]['RevisionEvidence']['stored_value'] == 1
    assert result[0]['RevisionEvidence']['incoming_value'] == 0
    assert len(result[0]['RevisionEvidence']['receipt_sha256']) == 64
    assert not _allows_authoritative_revision(result[1])
    assert 'AuthoritativeRevision' not in c.rows[0]  # no mutation before full validation


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'stale_database', 'wrong_candidate', 'wrong_old_value', 'duplicate', 'absent',
    'damaged_artifact', 'unretained_artifact', 'not_official', 'same_source_bytes',
    'reversed_time', 'missing_old_cell', 'mixed_source_files', 'wrong_series_source',
])
async def test_review_fails_closed_for_unproven_or_changed_inputs(correction, change):
    c = correction
    review = c.receipt['entries'][0]
    if change == 'stale_database': c.stored['value'] = 2
    elif change == 'wrong_candidate': c.rows[0]['Cases'] = '5'
    elif change == 'wrong_old_value': review['stored_value'] = 2
    elif change == 'duplicate': c.receipt['entries'].append(deepcopy(review))
    elif change == 'absent': review['date'] = '2026-02-01'
    elif change == 'damaged_artifact':
        (c.root / review['current_artifact']['relative_path']).write_bytes(b'damaged')
    elif change == 'unretained_artifact': c.artifacts.clear()
    elif change == 'not_official':
        for artifact in c.artifacts.values():
            artifact['metadata']['file']['url'] = artifact['source_url'] = 'https://example.com/EXANBR26.dbc'
    elif change == 'same_source_bytes': review['current_artifact'] = deepcopy(review['previous_artifact'])
    elif change == 'reversed_time':
        c.artifacts[review['previous_artifact']['relative_path']]['created_at'] = datetime(2027, 1, 1, tzinfo=timezone.utc)
    elif change == 'missing_old_cell':
        review['country_code'] = 'BR-BA'
        c.rows[0].update(CountryCode='BR-BA', GeographyKey='country:BR-BA:national')
    elif change == 'mixed_source_files': c.rows[0]['SourceFiles'] += '|EXANBR25.dbc'
    elif change == 'wrong_series_source': c.stored['raw_data']['DiseaseCode'] = 'BOTU'
    with pytest.raises(ValueError):
        await reviewed_residence_rows(c.db, c.rows, c.receipt, root=c.root)
    assert 'AuthoritativeRevision' not in c.rows[0]


@pytest.mark.asyncio
async def test_reviewed_cell_does_not_disable_other_observation_quality_checks(correction):
    from src.data.storage.series.quality import SeriesObservationQualityPolicy
    from src.data.storage.series_observation_store import SeriesObservationStore

    c = correction
    approved = await reviewed_residence_rows(c.db, c.rows, c.receipt, root=c.root)
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def observation(geography, raw, value):
        return {'series_code': 'HOLD_BR_EXAN', 'geography_key': geography,
                'dimension_key': 'all', 'time': moment, 'value': value,
                'raw_data': raw, 'metadata': {'frequency': 'monthly'}}
    incoming = [observation('country:BR-SP:national', approved[0], 0),
                observation('country:BR-BA:national', c.rows[0], 0)]
    history = [observation('country:BR-SP:national', {}, 1),
               observation('country:BR-BA:national', {}, 1)]
    result = SeriesObservationStore.assess_quality(
        incoming, country_code='BR', existing_observations=history,
        policy=SeriesObservationQualityPolicy(),
    )
    conflicts = [i for i in result.issues if i.code == 'single_series_positive_overwritten_by_zero']
    assert len(conflicts) == 1
    assert conflicts[0].details['geography_key'] == 'country:BR-BA:national'
