from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, DataReleaseJob, Task, TaskPriority, TaskStatus, TaskType
from src.services.data_release import raw_archive_jobs
from src.services.data_release.pipeline import ReleasePreflightError
from src.services.data_release.resilience import automatic_trigger_eligible, classify_release_failure


@pytest.fixture
async def archive_runtime(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'archive.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, DataReleaseJob.__table__],
        ))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with maker() as db:
            yield db

    runtime = SimpleNamespace(
        get_database=database, task_model=Task, root_dir=tmp_path,
        task_manager=SimpleNamespace(
            update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock(),
        ),
    )
    async with maker() as db:
        db.add(DataReleaseJob(job_id="daily", name="Daily"))
        await db.commit()
    yield runtime
    await engine.dispose()


@pytest.fixture
def service():
    archive = SimpleNamespace(
        enabled=True, repo_url="git@example.test:raw.git", branch="main", git_timeout_seconds=90,
    )
    return SimpleNamespace(
        _raw_archive_runtime=lambda: archive,
        _automatic_retry_settings=lambda: (3, 300, 3600),
        _raw_archive_check=AsyncMock(return_value={
            "blockers": [], "payload": {"ssh_transport": "github-ssh-over-443"},
        }),
        _build_git_env=lambda *_args, **kwargs: {"TEST_TRANSPORT": str(kwargs["use_github_ssh_over_443"])},
        _publish_raw_archive_command=lambda **_kwargs: ["python", "archive.py", "--push"],
        _python_executable=lambda: Path("python"),
        _run_logged_command=AsyncMock(),
    )


def _parent(uuid="site-1"):
    return SimpleNamespace(task_uuid=uuid, input_data={"release_job_id": "daily"}, metadata_={})


async def test_archive_enqueue_coalesces_pending_but_keeps_successor_to_running(archive_runtime, service):
    first = await raw_archive_jobs.enqueue(_parent(), service=service, runtime=archive_runtime)
    second = await raw_archive_jobs.enqueue(_parent("site-2"), service=service, runtime=archive_runtime)
    assert first["status"] == "queued"
    assert second == {"status": "coalesced", "task_uuid": first["task_uuid"]}
    async with archive_runtime.get_database() as db:
        task = (await db.execute(select(Task))).scalar_one()
        assert task.priority == TaskPriority.LOW
        assert automatic_trigger_eligible(task.input_data)
        assert task.input_data["latest_parent_release_task_uuid"] == "site-2"
        task.status = TaskStatus.RUNNING
        await db.commit()
    trailing = await raw_archive_jobs.enqueue(_parent("site-3"), service=service, runtime=archive_runtime)
    assert trailing["status"] == "queued"
    assert trailing["task_uuid"] != first["task_uuid"]


async def test_disabled_archive_never_creates_task(archive_runtime, service):
    service._raw_archive_runtime().enabled = False
    assert await raw_archive_jobs.enqueue(_parent(), service=service, runtime=archive_runtime) == {"status": "disabled"}
    async with archive_runtime.get_database() as db:
        assert (await db.execute(select(Task))).scalars().all() == []


async def _new_task(runtime, service):
    result = await raw_archive_jobs.enqueue(_parent(), service=service, runtime=runtime)
    async with runtime.get_database() as db:
        return (await db.execute(select(Task).where(Task.task_uuid == result["task_uuid"]))).scalar_one()


async def test_archive_executes_only_own_publisher_and_persists_output(archive_runtime, service):
    task = await _new_task(archive_runtime, service)
    output = await raw_archive_jobs.execute(task, service=service, runtime=archive_runtime)
    assert output["raw_archive_published"] is True
    assert output["parent_release_task_uuid"] == "site-1"
    kwargs = service._run_logged_command.call_args.kwargs
    assert kwargs["metadata"]["event"] == "raw_git_archive_publish"
    assert kwargs["env"] == {"TEST_TRANSPORT": "True", "RAW_ARCHIVE__BRANCH": "main"}
    async with archive_runtime.get_database() as db:
        assert (await db.get(Task, task.id)).output_data == output


async def test_archive_preflight_failure_retains_transient_retry_diagnostics(archive_runtime, service):
    task = await _new_task(archive_runtime, service)
    service._raw_archive_check.return_value = {
        "blockers": ["Raw archive unavailable"],
        "payload": {"read_check_output": "Connection timed out"},
    }
    with pytest.raises(ReleasePreflightError) as caught:
        await raw_archive_jobs.execute(task, service=service, runtime=archive_runtime)
    assert classify_release_failure(caught.value).retryable is True
    service._run_logged_command.assert_not_called()
    async with archive_runtime.get_database() as db:
        assert (await db.get(Task, task.id)).output_data == {}


async def test_archive_upload_failure_does_not_mutate_other_release(archive_runtime, service):
    task = await _new_task(archive_runtime, service)
    async with archive_runtime.get_database() as db:
        main = Task(task_uuid="main", task_name="Site", task_type=TaskType.EXPORT_DATA,
                    status=TaskStatus.COMPLETED, output_data={"pages_deployed": True})
        db.add(main)
        await db.commit()
        main_id = main.id
    service._run_logged_command.side_effect = RuntimeError("upload timed out")
    with pytest.raises(RuntimeError, match="timed out"):
        await raw_archive_jobs.execute(task, service=service, runtime=archive_runtime)
    async with archive_runtime.get_database() as db:
        main = await db.get(Task, main_id)
        assert main.status == TaskStatus.COMPLETED
        assert main.output_data == {"pages_deployed": True}
