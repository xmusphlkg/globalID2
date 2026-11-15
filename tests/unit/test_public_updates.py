from datetime import datetime, timezone
import json
from types import SimpleNamespace
import pytest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.publish_public_updates import PublicationReceipt, PublishStats, publish, reconcile, website_history
from src.domain import Base, Country, Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty
from src.services.data_release.public_updates import project_updates, read_published_markers

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def test_projection_groups_by_release_and_type_and_redacts_private_fields():
    rows = [('surveillance', 'JP', NOW, 'private-task'), ('surveillance', 'US', NOW, 'private-task'),
            ('situation', '*', NOW, 'private-task'), ('literature', '*', NOW, 'paper-task'),
            ('knowledge', 'D038', NOW, 'knowledge-task'), ('knowledge', 'private-url', NOW, 'knowledge-task')]
    files = project_updates(rows)
    assert json.loads(files['latest.json'])['months'] == ['2026-11']
    assert json.loads(files['latest.json'])['counts'] == {'surveillance': 1, 'situation': 1, 'literature': 1, 'knowledge': 1}
    events = json.loads(files['2026-11.json'])['events']
    assert {item['channel'] for item in events} == {'surveillance', 'situation', 'literature', 'knowledge'}
    assert next(item for item in events if item['channel'] == 'surveillance')['scopes'] == ['JP', 'US']
    assert next(item for item in events if item['channel'] == 'knowledge')['scopes'] == ['D038']
    assert b'private' not in files['2026-11.json']
    assert files == project_updates(reversed(rows))


async def test_only_completed_export_tasks_with_acknowledged_markers_are_public(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'feed.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Country.__table__, Task.__table__, PublishDirty.__table__]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        for name, status in [('good', TaskStatus.COMPLETED), ('failed', TaskStatus.FAILED), ('running', TaskStatus.RUNNING)]:
            db.add(Task(task_uuid=name, task_type=TaskType.EXPORT_DATA, task_name=name, status=status, input_data={}, created_at=NOW))
            db.add(PublishDirty(line='literature', scope_key='*', source_task_uuid='source', marked_at=NOW, published_at=NOW, published_task_uuid=name))
        db.add(PublishDirty(line='knowledge', scope_key='D038', source_task_uuid='source', marked_at=NOW))
        db.add(PublishDirty(line='literature', scope_key='*', source_task_uuid='source-2', marked_at=NOW, published_at=NOW, published_task_uuid='good'))
        await db.commit()
        rows = await read_published_markers(db)
        assert len(rows) == 1 and rows[0].published_task_uuid == 'good'
    await engine.dispose()


def test_upload_month_before_index_is_idempotent_and_fails_closed():
    class Client:
        def __init__(self): self.objects, self.writes = {}, []
        def get_object(self, key): return self.objects.get(key)
        def put_object_conditional(self, key, body, *, previous, **kwargs):
            assert self.objects.get(key) == previous
            self.writes.append(key)
            self.objects[key] = body
    client = Client()
    files = project_updates([('literature', '*', NOW, 'task')])
    assert publish(files, client) == 2
    assert client.writes == ['site-data/updates/2026-11.json', 'site-data/updates/latest.json']
    assert publish(files, client) == 0


def test_utc_month_boundaries_and_fractional_timestamps_sort_correctly():
    files = project_updates([
        ('literature', '*', datetime.fromisoformat('2026-11-01T00:30:00+08:00'), 'october'),
        ('literature', '*', NOW, 'whole-second'),
        ('situation', '*', NOW.replace(microsecond=1), 'later'),
    ])
    assert json.loads(files['2026-10.json'])['events'][0]['published_at'].startswith('2026-10-31T16:30:00')
    assert [item['channel'] for item in json.loads(files['2026-11.json'])['events']] == ['situation', 'literature']


def test_failed_month_upload_never_advances_index_and_can_be_retried():
    class Client:
        def __init__(self):
            self.objects = {'site-data/updates/latest.json': b'previous-index'}
            self.fail = True
        def get_object(self, key): return self.objects.get(key)
        def put_object_conditional(self, key, body, *, previous, **kwargs):
            if self.fail: raise RuntimeError('upload unavailable')
            assert self.objects.get(key) == previous
            self.objects[key] = body
    client = Client()
    files = project_updates([('literature', '*', NOW, 'task')])
    with pytest.raises(RuntimeError, match='upload unavailable'):
        publish(files, client)
    assert client.objects == {'site-data/updates/latest.json': b'previous-index'}
    client.fail = False
    assert publish(files, client) == 2
    assert publish(files, client) == 0


def test_website_history_export_uses_only_node_and_never_builds_templates(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import scripts.publish_public_updates as updates
    calls = []
    body = b'{"schema_version":1,"releases":[]}'
    def run(command, **options):
        calls.append(command)
        return SimpleNamespace(stdout=body)
    monkeypatch.setattr(updates.subprocess, 'run', run)
    assert website_history(tmp_path) == {'changelog.json': body}
    assert len(calls) == 1
    assert calls[0][-1] == str(tmp_path / 'astro-site/scripts/export-changelog.mjs')
    assert 'build' not in calls[0]


@pytest.mark.parametrize('failed_feed', ['website', 'data'])
def test_independent_record_feeds_do_not_block_each_other(failed_feed):
    class Client:
        def __init__(self): self.objects = {}
        def get_object(self, key): return self.objects.get(key)
        def put_object_conditional(self, key, body, **kwargs): self.objects[key] = body
    def unavailable(): raise RuntimeError('input unavailable')
    client = Client()
    with pytest.raises(RuntimeError, match='input unavailable'):
        reconcile(client,
            history=unavailable if failed_feed == 'website' else lambda: {'changelog.json': b'versions'},
            data=unavailable if failed_feed == 'data' else lambda: project_updates([]))
    assert ('site-data/updates/latest.json' if failed_feed == 'website' else 'site-data/updates/changelog.json') in client.objects


class ReceiptClient:
    def __init__(self):
        self.config = SimpleNamespace(s3_endpoint='https://account.r2.example.test', account_id='account',
                                      bucket='test-bucket', access_key_id='private-key', secret_access_key='private-secret')
        self.objects, self.reads, self.writes = {}, [], []
        self.before_get = self.before_put = self.after_put = lambda key: None

    def get_object(self, key):
        self.reads.append(key)
        self.before_get(key)
        return self.objects.get(key)

    def put_object_conditional(self, key, body, *, previous, **kwargs):
        self.before_put(key)
        assert self.objects.get(key) == previous
        self.writes.append(key)
        self.objects[key] = body
        self.after_put(key)

    def clear_calls(self):
        self.reads.clear()
        self.writes.clear()


def receipt_run(client, path, *, now=1000, data_files=None, **options):
    receipt = PublicationReceipt(path, client.config, now=now, **options)
    stats = PublishStats()
    result = reconcile(client, history=lambda: {'changelog.json': b'website-history'},
                       data=lambda: data_files if data_files is not None else project_updates([('literature', '*', NOW, 'task')]),
                       receipt=receipt, stats=stats)
    return result, stats


def test_confirmed_receipts_skip_all_remote_io_and_do_not_extend_verification_age(tmp_path):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    result, stats = receipt_run(client, path)
    assert result == (1, 3)
    assert stats == PublishStats(remote_reads=3, remote_writes=3)
    assert client.writes == ['site-data/updates/changelog.json', 'site-data/updates/2026-11.json', 'site-data/updates/latest.json']
    expected = {f'site-data/updates/{name}': body for name, body in project_updates([('literature', '*', NOW, 'task')]).items()}
    assert client.objects == {**expected, 'site-data/updates/changelog.json': b'website-history'}
    saved = path.read_bytes()
    assert b'private' not in saved and b'test-bucket' not in saved
    assert path.stat().st_mode & 0o777 == 0o600
    client.clear_calls()
    result, stats = receipt_run(client, path, now=1010)
    assert result == (1, 0)
    assert stats == PublishStats(receipt_skips=3)
    assert client.reads == client.writes == []
    assert path.read_bytes() == saved


def test_changed_feed_only_compares_changed_files_and_month_precedes_index(tmp_path):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    client.clear_calls()
    changed = project_updates([('literature', '*', NOW, 'task'), ('surveillance', 'JP', NOW, 'new-task')])
    result, stats = receipt_run(client, path, now=1010, data_files=changed)
    assert result == (1, 2)
    assert stats == PublishStats(remote_reads=2, remote_writes=2, receipt_skips=1)
    assert client.reads == client.writes == ['site-data/updates/2026-11.json', 'site-data/updates/latest.json']
    assert all(client.objects[f'site-data/updates/{name}'] == body for name, body in changed.items())


@pytest.mark.parametrize('verify_options', [{'now': 1001, 'verify_remote': True}, {'now': 1000 + 86400},
                                          {'now': 1001, 'verify_interval_seconds': 0}, {'now': 999}])
def test_forced_periodic_and_clock_rollback_checks_repair_remote_drift(tmp_path, verify_options):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    expected = dict(client.objects)
    del client.objects['site-data/updates/2026-11.json']
    client.objects['site-data/updates/latest.json'] = b'externally-modified'
    client.clear_calls()
    _, stats = receipt_run(client, path, now=1001)
    assert stats == PublishStats(receipt_skips=3)
    result, stats = receipt_run(client, path, **verify_options)
    assert result == (1, 2)
    assert stats == PublishStats(remote_reads=3, remote_writes=2)
    assert client.objects == expected
    assert client.writes == ['site-data/updates/2026-11.json', 'site-data/updates/latest.json']


@pytest.mark.parametrize('damage', ['missing', 'invalid-json', 'wrong-schema', 'bad-hash', 'negative-time', 'huge-time'])
def test_missing_or_corrupt_receipts_require_full_remote_comparison(tmp_path, damage):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    saved = json.loads(path.read_bytes())
    if damage == 'missing':
        path.unlink()
    elif damage == 'invalid-json':
        path.write_text('{')
    else:
        if damage == 'wrong-schema': saved['schema_version'] = True
        if damage == 'bad-hash': saved['files']['latest.json']['sha256'] = 'g' * 64
        if damage == 'negative-time': saved['files']['latest.json']['verified_at'] = -1
        if damage == 'huge-time': saved['files']['latest.json']['verified_at'] = 10 ** 1000
        path.write_text(json.dumps(saved))
    client.clear_calls()
    result, stats = receipt_run(client, path, now=1001)
    assert result == (1, 0)
    assert stats == PublishStats(remote_reads=3)
    assert len(client.reads) == 3 and client.writes == []


@pytest.mark.parametrize('attribute', ['s3_endpoint', 'account_id', 'bucket'])
def test_receipts_moved_to_another_target_cannot_suppress_uploads(tmp_path, attribute):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    target = ReceiptClient()
    setattr(target.config, attribute, 'other-target')
    result, stats = receipt_run(target, path, now=1001)
    assert result == (1, 3)
    assert stats == PublishStats(remote_reads=3, remote_writes=3)


def test_receipts_are_bound_to_prefix_as_well_as_bucket(tmp_path):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    other = PublicationReceipt(path, client.config, prefix='other/updates', now=1001)
    client.clear_calls()
    files = project_updates([])
    assert publish(files, client, receipt=other, prefix='other/updates') == 1
    assert client.reads == client.writes == ['other/updates/latest.json']
    with pytest.raises(ValueError, match='another destination'):
        publish(files, client, receipt=other)


@pytest.mark.parametrize('failed_name', ['2026-11.json', 'latest.json'])
@pytest.mark.parametrize('fail_stage', ['before_get', 'before_put', 'after_put'])
def test_failed_or_ambiguous_repairs_never_leave_fresh_receipts(tmp_path, failed_name, fail_stage):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    expected = dict(client.objects)
    failed_key = f'site-data/updates/{failed_name}'
    del client.objects[failed_key]

    def fail(key):
        if key == failed_key:
            # Durable invalidation precedes the request, so a process crash at
            # this point also causes a comparison on the next ordinary run.
            assert failed_name not in json.loads(path.read_bytes())['files']
            raise TimeoutError('request outcome unknown')
    setattr(client, fail_stage, fail)
    with pytest.raises(TimeoutError, match='outcome unknown'):
        receipt_run(client, path, now=1001, verify_remote=True)
    assert failed_name not in json.loads(path.read_bytes())['files']
    if failed_name != 'latest.json':
        assert client.objects['site-data/updates/latest.json'] == expected['site-data/updates/latest.json']
    setattr(client, fail_stage, lambda key: None)
    client.clear_calls()
    receipt_run(client, path, now=1002)
    assert failed_key in client.reads
    assert client.objects == expected
    assert client.writes == ([] if fail_stage == 'after_put' else [failed_key])


@pytest.mark.parametrize('failed_feed', ['website', 'data'])
def test_successful_feed_receipts_persist_when_the_other_input_fails(tmp_path, failed_feed):
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt = PublicationReceipt(path, client.config, now=1000)
    def unavailable(): raise RuntimeError('input unavailable')
    with pytest.raises(RuntimeError, match='input unavailable'):
        reconcile(client, receipt=receipt,
                  history=unavailable if failed_feed == 'website' else lambda: {'changelog.json': b'website-history'},
                  data=unavailable if failed_feed == 'data' else lambda: project_updates([]))
    saved = json.loads(path.read_bytes())['files']
    assert set(saved) == ({'latest.json'} if failed_feed == 'website' else {'changelog.json'})
    client.clear_calls()
    receipt_run(client, path, now=1001, data_files=project_updates([]))
    assert client.reads == ['site-data/updates/changelog.json' if failed_feed == 'website' else 'site-data/updates/latest.json']


def test_receipt_save_failure_keeps_previous_file_intact_and_recovery_compares_remote(tmp_path, monkeypatch):
    import scripts.publish_public_updates as updates
    client, path = ReceiptClient(), tmp_path / 'receipt.json'
    receipt_run(client, path)
    saved = path.read_bytes()
    replace = updates.os.replace
    monkeypatch.setattr(updates.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError, match='disk unavailable'):
        receipt_run(client, path, now=1001, verify_remote=True)
    assert path.read_bytes() == saved
    assert list(tmp_path.iterdir()) == [path]
    monkeypatch.setattr(updates.os, 'replace', replace)
    client.clear_calls()
    _, stats = receipt_run(client, path, now=1002, verify_remote=True)
    assert stats == PublishStats(remote_reads=3)


@pytest.mark.parametrize('value', ['-1', 'nan', 'inf'])
def test_cli_rejects_invalid_verification_intervals_before_creating_a_client(value):
    from scripts.publish_public_updates import main
    with pytest.raises(SystemExit) as error:
        main(['--verify-interval-seconds', value])
    assert error.value.code == 2
