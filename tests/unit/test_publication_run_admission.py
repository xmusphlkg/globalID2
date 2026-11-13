"""Planned work uses the existing database task reservation and exact dirty IDs."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.domain import Task, TaskStatus
from src.domain import DataReleaseJob
from src.domain.collection import PublishDirty
from src.services.data_release_service import DataReleaseJobConfig
from src.services.data_release.publication_execution import task_identity
from src.services.data_release.publication_execution import digest, PublicationPlanError
from tests.unit.test_release_dirty_selection import release_db  # noqa: F401


def frozen(key="publication-key", digest="a" * 64):
    return {"plan_id": "11111111-1111-4111-8111-111111111111", "plan_digest": digest,
            "task_uuid": task_identity(key), "expected_generations": {}}


async def test_theme_reservation_is_durable_idempotent_and_never_selects_dirty(release_db):
    maker, service, _ = release_db
    async with maker() as db:
        db.add(PublishDirty(line="literature", scope_key="*", source_task_uuid="source", marked_at=datetime.now(timezone.utc)))
        await db.commit()
    job = DataReleaseJobConfig(job_id="site-release", name="Site")
    args = dict(manual=True, trigger="publication_plan", trigger_task_uuid=None, theme_only=True, publication_plan=frozen())
    first = await service._enqueue_release_task(job, **args)
    replay = await service._enqueue_release_task(job, **args)
    assert first["task_uuid"] == replay["task_uuid"] == task_identity("publication-key")
    assert replay["reason"] == "idempotent_replay"
    async with maker() as db:
        tasks = list((await db.execute(select(Task))).scalars())
        assert len(tasks) == 1
        task = tasks[0]
        assert task.input_data["theme_only"] and task.input_data["publish_dirty_ids"] == []
        assert not task.input_data["include_cloudflare_deploy"] and not task.input_data["include_git_push"]
        assert not task.input_data["raw_archive_enabled"]
        assert (await db.execute(select(PublishDirty))).scalar_one().published_at is None
        task.status = TaskStatus.COMPLETED
        await db.commit()
    assert (await service._enqueue_release_task(job, **args))["reason"] == "idempotent_replay"
    assert not await service._release_up_to_date("site-release", datetime.now(timezone.utc))
    assert await service._latest_terminal_release_task("site-release") is None


async def test_different_plan_cannot_reuse_idempotency_reservation(release_db):
    _, service, _ = release_db
    job = DataReleaseJobConfig(job_id="site-release", name="Site")
    args = dict(manual=True, trigger="publication_plan", trigger_task_uuid=None, theme_only=True)
    await service._enqueue_release_task(job, **args, publication_plan=frozen())
    with pytest.raises(ValueError, match="idempotency_conflict"):
        await service._enqueue_release_task(job, **args, publication_plan=frozen(digest="b" * 64))


async def test_maintenance_plan_with_empty_selection_does_not_consume_new_dirty(release_db):
    maker, service, _ = release_db
    async with maker() as db:
        db.add(PublishDirty(line="literature", scope_key="*", source_task_uuid="new", marked_at=datetime.now(timezone.utc)))
        await db.commit()
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="publication_plan", trigger_task_uuid=None, full=True,
        pending_ids=[], publication_plan=frozen())
    assert result["status"] == "queued"
    async with maker() as db:
        task = (await db.execute(select(Task))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == []


async def test_content_plan_keeps_remote_cas_baseline_and_dirty_selection(release_db):
    maker, service, _ = release_db
    async with maker() as db:
        selected = PublishDirty(line="situation", scope_key="*", source_task_uuid="old", marked_at=datetime.now(timezone.utc))
        later = PublishDirty(line="situation", scope_key="*", source_task_uuid="new", marked_at=datetime.now(timezone.utc))
        db.add_all([selected, later])
        await db.commit()
        selected_id, later_id = selected.id, later.id
    metadata = {**frozen(), "expected_generations": {"situation": "c" * 64}}
    result = await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
        manual=True, trigger="publication_plan", trigger_task_uuid=None, data_only=True, data_line="situation",
        pending_ids=[selected_id], publication_plan=metadata)
    assert result["status"] == "queued"
    async with maker() as db:
        task = (await db.execute(select(Task))).scalar_one()
        assert task.input_data["publish_dirty_ids"] == [selected_id]
        assert later_id not in task.input_data["publish_dirty_ids"]
        assert task.input_data["publication_expected_generations"] == {"situation": "c" * 64}


async def test_plan_cannot_silently_shrink_after_one_dirty_id_was_published(release_db):
    maker, service, _ = release_db
    async with maker() as db:
        selected = PublishDirty(line="situation", scope_key="*", source_task_uuid="old", marked_at=datetime.now(timezone.utc))
        consumed = PublishDirty(line="situation", scope_key="*", source_task_uuid="other", marked_at=datetime.now(timezone.utc),
                                published_at=datetime.now(timezone.utc))
        db.add_all([selected, consumed])
        await db.commit()
        ids = [selected.id, consumed.id]
    with pytest.raises(PublicationPlanError, match="selection_changed"):
        await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
            manual=True, trigger="publication_plan", trigger_task_uuid=None, data_only=True, data_line="situation",
            pending_ids=ids, publication_plan=frozen())
    async with maker() as db:
        assert not list((await db.execute(select(Task))).scalars())


async def test_plan_job_configuration_is_rechecked_inside_queue_reservation(release_db):
    maker, service, _ = release_db
    async with maker() as db:
        row = (await db.execute(select(DataReleaseJob))).scalar_one()
        expected = digest({column.name: getattr(row, column.name) for column in DataReleaseJob.__table__.columns})
        row.cloudflare_project_name = "changed-after-plan-validation"
        await db.commit()
    with pytest.raises(PublicationPlanError, match="job_configuration_changed"):
        await service._enqueue_release_task(DataReleaseJobConfig(job_id="site-release", name="Site"),
            manual=True, trigger="publication_plan", trigger_task_uuid=None, theme_only=True,
            publication_plan={**frozen(), "job_configuration_digest": expected})
    async with maker() as db:
        assert not list((await db.execute(select(Task))).scalars())
