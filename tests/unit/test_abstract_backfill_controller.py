from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts import run_abstract_backfill as controller


async def test_review_waits_for_preexisting_stream(monkeypatch):
    now = datetime.now(timezone.utc)
    tasks = iter([
        SimpleNamespace(task_uuid='old', created_at=now - timedelta(minutes=2)),
        SimpleNamespace(task_uuid='new', created_at=now + timedelta(seconds=1)),
    ])
    waited = []
    async def enqueue(*args, **kwargs):
        return next(tasks)
    async def wait(uuid):
        waited.append(uuid)
        return {}
    monkeypatch.setattr(controller, 'enqueue_model_operation', enqueue)
    monkeypatch.setattr(controller, 'wait_task_output', wait)
    await controller.fresh_review(now)
    assert waited == ['old', 'new']


def test_state_checkpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, 'STATE', tmp_path / 'state.json')
    controller.save_state({'pending_review': True})
    assert controller.read_state() == {'pending_review': True}
