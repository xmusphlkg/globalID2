"""Image review CLI preserves originals and never performs local recognition."""
import json
import hashlib
from unittest.mock import AsyncMock

import pytest

from scripts import review_cn_province_images as cli


def arguments(tmp_path, *, live=False):
    image = tmp_path / 'original.png'
    image.write_bytes(b'original source bytes')
    values = ['--province', 'CN-QH', '--month', '2020-10', '--document', str(image),
              '--source-url', 'https://official.example/original.png', '--output', str(tmp_path / 'audit.json')]
    if live:
        values.append('--live')
    return cli.build_parser().parse_args(values)


async def test_manifest_preview_preserves_bytes_without_models_or_ocr(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    model = AsyncMock(side_effect=AssertionError('preview cannot invoke models'))
    monkeypatch.setattr(cli, '_live_review', model)
    assert await cli.run(args) == 2
    audit = json.loads(args.output.read_text())
    assert audit['audit']['panel_size'] == 3
    assert audit['audit']['quorum'] == 2
    assert audit['rows'] == []
    model.assert_not_awaited()


async def test_live_review_receives_every_original_unchanged(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    second = tmp_path / 'second.pdf'
    second.write_bytes(b'original second page')
    args.document.append(second)
    args.source_url.append('https://official.example/second.pdf')
    review = AsyncMock(return_value={'status': 'validated', 'rows': [{'label': '甲型肝炎', 'cases': 1}],
                                     'audit': {'panel_size': 3, 'quorum': 2, 'winner_votes': 2}})
    monkeypatch.setattr(cli, '_live_review', review)
    assert await cli.run(args) == 0
    documents = review.await_args.args[0]
    assert [doc['data'] for doc in documents] == [b'original source bytes', b'original second page']
    assert [doc['media_type'] for doc in documents] == ['image/png', 'application/pdf']
    assert all('ocr' not in doc for doc in documents)


async def test_timeout_never_claims_a_winning_vote(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    monkeypatch.setattr(cli, '_live_review', AsyncMock(side_effect=TimeoutError))
    assert await cli.run(args) == 2
    result = json.loads(args.output.read_text())
    assert result['status'] == 'needs_review'
    assert result['audit']['reason'] == 'model_center_review_timed_out'
    assert result['rows'] == []
    retry = arguments(tmp_path, live=True)
    retry.retry_from = args.output
    retry.output = tmp_path / 'retry.json'
    model = AsyncMock(return_value={'status': 'needs_review', 'rows': [], 'audit': {'quorum': 2}})
    monkeypatch.setattr(cli, '_live_review', model)
    assert await cli.run(retry) == 2
    assert json.loads(retry.output.read_text())['retry_of_audit_sha256'] == hashlib.sha256(args.output.read_bytes()).hexdigest()


async def test_timed_out_live_review_keeps_parent_task_identity(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    monkeypatch.setattr(cli, '_live_review', AsyncMock(side_effect=cli.ImageReviewTimeout('parent-task-uuid')))
    assert await cli.run(args) == 2
    result = json.loads(args.output.read_text())
    assert result['task_uuid'] == 'parent-task-uuid'
    assert result['status'] == 'needs_review'
    assert result['rows'] == []


def prior_audit(args):
    data = args.document[0].read_bytes()
    return {'status': 'needs_review', 'task_uuid': 'old-task', 'rows': [], 'audit': {
        'protocol': 'cn-province-native-image-expert-vote.v1', 'province_code': args.province,
        'report_month': args.month, 'panel_size': args.panel_size,
        'documents': [{'sha256': hashlib.sha256(data).hexdigest(), 'media_type': 'image/png',
                       'source_url': args.source_url[0]}],
    }}


async def test_retry_preserves_prior_audit_and_requires_same_originals(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    old_path = tmp_path / 'previous.json'
    old_bytes = json.dumps(prior_audit(args)).encode()
    old_path.write_bytes(old_bytes)
    args.retry_from = old_path
    review = AsyncMock(return_value={'status': 'needs_review', 'rows': [], 'audit': {'quorum': 2}})
    monkeypatch.setattr(cli, '_live_review', review)
    assert await cli.run(args) == 2
    assert old_path.read_bytes() == old_bytes
    result = json.loads(args.output.read_text())
    assert result['retry_of_audit_sha256'] == hashlib.sha256(old_bytes).hexdigest()
    assert result['retry_of_task_uuid'] == 'old-task'
    assert review.await_count == 1


async def test_retry_rejects_changed_original_and_existing_output(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    old_path = tmp_path / 'previous.json'
    old_path.write_text(json.dumps(prior_audit(args)))
    args.retry_from = old_path
    review = AsyncMock()
    monkeypatch.setattr(cli, '_live_review', review)
    args.document[0].write_bytes(b'changed image')
    with pytest.raises(ValueError, match='scope, panel or original documents'):
        await cli.run(args)
    review.assert_not_awaited()
    args.document[0].write_bytes(b'original source bytes')
    args.output.write_text('{}')
    with pytest.raises(ValueError, match='already exists'):
        await cli.run(args)
    review.assert_not_awaited()


async def test_retry_rejects_changed_article_or_previously_validated_audit(tmp_path, monkeypatch):
    args = arguments(tmp_path, live=True)
    args.article_file = tmp_path / 'article.txt'
    args.article_file.write_text('official period and province', encoding='utf-8')
    old = prior_audit(args)
    old['audit']['article_sha256'] = hashlib.sha256(args.article_file.read_text().encode('utf-8')).hexdigest()
    previous = tmp_path / 'previous.json'
    previous.write_text(json.dumps(old), encoding='utf-8')
    args.retry_from = previous
    model = AsyncMock()
    monkeypatch.setattr(cli, '_live_review', model)
    args.article_file.write_text('changed article', encoding='utf-8')
    with pytest.raises(ValueError, match='article text differs'):
        await cli.run(args)
    old['status'] = 'validated'
    previous.write_text(json.dumps(old), encoding='utf-8')
    with pytest.raises(ValueError, match='scope, panel or original documents'):
        await cli.run(args)
    model.assert_not_awaited()
