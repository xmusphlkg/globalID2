from datetime import datetime, timezone
import json
import pytest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.publish_public_updates import publish
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
