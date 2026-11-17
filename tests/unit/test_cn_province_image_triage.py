"""Operator triage stays read-only and keeps every vote pending until quorum."""
import json
from unittest.mock import AsyncMock

from scripts import triage_cn_province_images as triage


async def test_triage_reports_model_capacity_and_abstention_without_raw_response(tmp_path, monkeypatch):
    audit_path = tmp_path / 'pending.json'
    audit_path.write_text(json.dumps({'status': 'needs_review', 'task_uuid': 'task-1', 'rows': [], 'audit': {
        'protocol': 'cn-province-native-image-expert-vote.v1', 'province_code': 'CN-QH',
        'report_month': '2026-10', 'status': 'needs_review', 'panel_size': 3, 'quorum': 2,
        'documents': [{'sha256': 'a' * 64, 'source_url': 'https://official.example/image.jpg',
                       'media_type': 'image/jpeg', 'page_count': 1}],
        'readings': [{'slot': 1, 'status': 'voted', 'model_identity': 'model-a',
                      'raw_response': 'sensitive extraction'},
                     {'slot': 2, 'status': 'abstained', 'reason': 'invalid disease'},
                     {'slot': 3, 'status': 'absent', 'reason': 'no_untried_native_model_available'}],
        'valid_votes': 1, 'winner_votes': 0,
    }}))
    capacity = {'panel_size': 3, 'quorum': 2, 'distinct_native_models': 2,
                'quorum_available': True, 'full_panel_available': False, 'missing_for_full_panel': 1}
    route_check = AsyncMock(return_value=capacity)
    monkeypatch.setattr(triage, 'native_model_capacity', route_check)
    result = await triage.run([audit_path])
    item = result['reviews'][0]
    assert result['status'] == 'needs_review'
    assert item['current_model_capacity'] == capacity
    assert item['next_action'] == 'inspect_abstentions_and_restore_missing_distinct_model_or_manual_review'
    assert 'sensitive extraction' not in json.dumps(result)
    assert route_check.await_count == 1


async def test_offline_triage_never_queries_models(tmp_path, monkeypatch):
    path = tmp_path / 'pending.json'
    path.write_text(json.dumps({'status': 'needs_review', 'audit': {
        'protocol': 'cn-province-native-image-expert-vote.v1', 'panel_size': 3,
        'status': 'needs_review', 'quorum': 2, 'documents': [{'sha256': 'a' * 64}],
        'readings': [{'status': 'absent'}], 'available_native_models_at_start': 1,
    }}))
    check = AsyncMock(side_effect=AssertionError('must stay offline'))
    monkeypatch.setattr(triage, 'native_model_capacity', check)
    result = await triage.run([path], offline=True)
    assert result['reviews'][0]['next_action'] == 'enable_distinct_native_file_models_or_review_originals_manually'
    check.assert_not_awaited()
