import json

from src.services.data_release.publication_execution import literature_receipt


def test_research_commit_evidence_does_not_claim_public_verification(tmp_path):
    run = tmp_path / 'exports/research-runs/task'
    run.mkdir(parents=True)
    assert literature_receipt(tmp_path, 'task') == {}
    (run / 'authority-committed.json').write_text(json.dumps({
        'release_id': 'literature-task', 'authoritative_committed': True,
        'generation': 'a' * 64, 'committed_at': '2026-11-13T00:00:00Z'}))
    result = literature_receipt(tmp_path, 'task')
    assert result['authoritative_committed'] is True
    assert 'public_verified' not in result
    (run / 'published.json').write_text(json.dumps({'release_id': 'literature-task',
        'json': {'release_id': 'literature-task'}, 'public_verification': {'status': 'verified'}}))
    assert literature_receipt(tmp_path, 'task')['public_verified'] is True


def test_other_research_task_receipt_cannot_attest_this_plan(tmp_path):
    run = tmp_path / 'exports/research-runs/task'
    run.mkdir(parents=True)
    (run / 'authority-committed.json').write_text(json.dumps({
        'release_id': 'literature-other', 'authoritative_committed': True}))
    assert literature_receipt(tmp_path, 'task') == {}
    assert literature_receipt(tmp_path, '../other') == {}


def test_v3_commit_reports_pending_until_real_public_verification(tmp_path):
    run = tmp_path / 'exports/research-runs/task'
    run.mkdir(parents=True)
    (run / 'authority-committed.json').write_text(json.dumps({
        'release_id': 'literature-task', 'authoritative_committed': True, 'public_verification_required': True}))
    assert literature_receipt(tmp_path, 'task')['public_verified'] is False
    (run / 'published.json').write_text(json.dumps({'release_id': 'literature-task',
        'json': {'release_id': 'literature-task'}, 'public_verification': {'status': 'verified'}}))
    assert literature_receipt(tmp_path, 'task')['public_verified'] is True
