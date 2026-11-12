from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.domain import Task, TaskStatus
from src.domain.collection import PublishDirty
from src.services import data_release_service as module
from src.services.data_release_service import DataReleaseJobConfig
from tests.unit.test_release_dirty_selection import release_db


async def test_literature_is_scheduled_every_six_hours_and_selects_only_its_global_markers(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, '_config', lambda: SimpleNamespace(timezone='UTC', literature_json_enabled=True,
        literature_publish_interval_minutes=360, auto_failure_cooldown_minutes=0))
    monkeypatch.setattr(module, 'is_automatic_json_paused', lambda _line: False)
    monkeypatch.setattr(service, 'load_jobs', AsyncMock(return_value=[DataReleaseJobConfig(job_id='site-release', name='Site')]))
    now = datetime.now(timezone.utc)
    async with maker() as db:
        db.add_all([PublishDirty(line='literature', scope_key='*', source_task_uuid='a', marked_at=now),
                    PublishDirty(line='literature', scope_key='bad', source_task_uuid='bad', marked_at=now),
                    PublishDirty(line='situation', scope_key='*', source_task_uuid='s', marked_at=now)])
        await db.commit()
    await service.reconcile_pending_literature()
    await service.reconcile_pending_literature()
    async with maker() as db:
        tasks = list((await db.execute(select(Task))).scalars())
        markers = list((await db.execute(select(PublishDirty).order_by(PublishDirty.id))).scalars())
        assert len(tasks) == 1 and tasks[0].input_data['publish_dirty_ids'] == [markers[0].id]
        assert tasks[0].input_data['data_line'] == 'literature'
        tasks[0].status, tasks[0].created_at = TaskStatus.COMPLETED, now - timedelta(hours=5)
        markers[0].published_at = now
        db.add(PublishDirty(line='literature', scope_key='*', source_task_uuid='b', marked_at=now))
        await db.commit()
    await service.reconcile_pending_literature()
    async with maker() as db:
        tasks = list((await db.execute(select(Task))).scalars())
        assert len(tasks) == 1
        tasks[0].created_at = now - timedelta(hours=7)
        await db.commit()
    await service.reconcile_pending_literature()
    async with maker() as db:
        tasks = list((await db.execute(select(Task).order_by(Task.id))).scalars())
        assert len(tasks) == 2 and tasks[-1].input_data['data_line'] == 'literature'


async def test_static_release_does_not_consume_literature_markers_after_cutover(release_db, monkeypatch):
    maker, service, _ = release_db
    monkeypatch.setattr(service, '_config', lambda: SimpleNamespace(timezone='UTC', surveillance_json_enabled=True,
        situation_json_enabled=True, literature_json_enabled=True))
    now = datetime.now(timezone.utc)
    async with maker() as db:
        db.add_all([PublishDirty(line='literature', scope_key='*', source_task_uuid='a', marked_at=now),
                    PublishDirty(line='knowledge', scope_key='*', source_task_uuid='b', marked_at=now)])
        await db.commit()
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id='site-release', name='Site'),
        manual=True, trigger='test', trigger_task_uuid=None)
    async with maker() as db:
        task = (await db.execute(select(Task).where(Task.task_uuid == result['task_uuid']))).scalar_one()
        rows = list((await db.execute(select(PublishDirty).order_by(PublishDirty.id))).scalars())
    assert task.input_data['literature_detached'] is True
    assert task.input_data['publish_dirty_ids'] == [rows[1].id]
