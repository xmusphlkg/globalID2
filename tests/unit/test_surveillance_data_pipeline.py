import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation.release_projection_snapshot import write_projection_snapshot
from src.services.data_release import data_pipeline
from src.services.data_release.pipeline import execute_release_task


@pytest.fixture
def setup(tmp_path, monkeypatch):
    snapshot_state = {"open": False, "count": 0}
    calls = []
    commit = AsyncMock()
    monkeypatch.setattr(data_pipeline, "commit_release_projection", commit)
    stored = SimpleNamespace(output_data=None)
    lines = ["surveillance"]
    countries = [("JP", True)]
    def execute(statement):
        if "FROM publish_dirty" in str(statement):
            rows = [SimpleNamespace(id=11, line=line, scope_key="JP", published_at=None, published_task_uuid=None, change_set={"version": 1, "precise": True}) for line in lines]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
        return SimpleNamespace(all=lambda: countries)
    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), commit=AsyncMock(), get=AsyncMock(return_value=stored))

    @asynccontextmanager
    async def database():
        yield db

    @asynccontextmanager
    async def snapshot():
        snapshot_state["count"] += 1
        snapshot_state["open"] = True
        try:
            yield {"GLOBALID_RELEASE_DB_SNAPSHOT": "00000003-0000001B-1"}
        finally:
            snapshot_state["open"] = False

    class Service:
        failure = False
        _python_executable = lambda self: Path("python")
        _config = lambda self: SimpleNamespace()
        _cloudflare_account_id = lambda self: "account"
        _cloudflare_api_token = lambda self: "test-token"

        async def _run_logged_command(self, task_uuid, *, title, cmd, env, **kwargs):
            calls.append((title, cmd))
            if title == "Capture Surveillance Publication Baseline":
                assert not snapshot_state["open"]
                assert "GLOBALID_RELEASE_DB_SNAPSHOT" not in env
                assert "--capture-baseline-only" in cmd
            elif title == "Export Surveillance JSON":
                assert snapshot_state["open"]
                assert env["GLOBALID_RELEASE_DB_SNAPSHOT"] == "00000003-0000001B-1"
                public = tmp_path / "exports/surveillance-data"
                public.mkdir(parents=True, exist_ok=True)
                payload = b'{"disease_series":{}}'
                sha = hashlib.sha256(payload).hexdigest()
                (public / "objects").mkdir(exist_ok=True)
                (public / f"objects/{sha}.json").write_bytes(payload)
                (public / "current.json").write_text(json.dumps({"schema_version": 1, "release_id": f"data-{task_uuid}",
                    "files": {"countries/jp.json": {"url": f"objects/{sha}.json", "sha256": sha, "bytes": len(payload)}}}))
                with monkeypatch.context() as env_patch:
                    env_patch.setenv("GLOBALID_RELEASE_DB_SNAPSHOT", env["GLOBALID_RELEASE_DB_SNAPSHOT"])
                    write_projection_snapshot({"country_exports": [{"code": "JP", "country_data": {"disease_series": {}}}]},
                        tmp_path / "exports/surveillance-state/_release-projection", {})
            elif title == "Publish Surveillance JSON":
                assert not snapshot_state["open"]
                assert "GLOBALID_RELEASE_DB_SNAPSHOT" not in env
                if self.failure:
                    raise RuntimeError("upload failed")
            else:
                raise AssertionError(f"JSON release must never run static work: {title}")

    runtime = SimpleNamespace(root_dir=tmp_path, get_database=database, read_snapshot=snapshot, task_model=object,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()),
        generate_timeout_seconds=60, download_publish_timeout_seconds=60)
    task = SimpleNamespace(id=1, task_uuid="json-task", input_data={"data_only": True,
        "incremental_country_codes": ["JP"], "publish_dirty_ids": [11]})
    return SimpleNamespace(runtime=runtime, task=task, service=Service(), calls=calls, commit=commit,
        db=db, stored=stored, snapshot=snapshot_state, lines=lines, countries=countries)


@pytest.mark.asyncio
async def test_data_update_never_builds_html_or_publishes_other_channels(setup):
    s = setup
    result = await execute_release_task(s.service, s.task, runtime=s.runtime)
    assert [name for name, _ in s.calls] == ["Capture Surveillance Publication Baseline", "Export Surveillance JSON", "Publish Surveillance JSON"]
    assert result["data_only"] and not result["pages_deployed"]
    assert s.snapshot == {"open": False, "count": 1}
    s.commit.assert_awaited_once()
    assert s.commit.await_args.kwargs["dirty_ids"] == [11]
    assert s.commit.await_args.kwargs["require_prepared"]
    assert s.stored.output_data == result


@pytest.mark.asyncio
async def test_failed_upload_keeps_dirty_changes_and_retry_reuses_the_frozen_snapshot(setup):
    s = setup
    s.service.failure = True
    with pytest.raises(RuntimeError, match="upload"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    s.commit.assert_not_awaited()
    s.service.failure = False
    s.calls.clear()
    await execute_release_task(s.service, s.task, runtime=s.runtime)
    assert [name for name, _ in s.calls] == ["Publish Surveillance JSON"]
    assert s.snapshot["count"] == 1
    s.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_change_line_cannot_start_an_export_or_upload(setup):
    s = setup
    s.lines[:] = ["literature"]
    with pytest.raises(ValueError, match="surveillance change"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    assert s.calls == []
    s.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_cannot_ack_a_different_selection_or_damaged_projection(setup):
    s = setup
    s.service.failure = True
    with pytest.raises(RuntimeError):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    s.task.input_data["publish_dirty_ids"] = [12]
    with pytest.raises((ValueError, RuntimeError), match="missing|selected"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    s.task.input_data["publish_dirty_ids"] = [11]
    (s.runtime.root_dir / "exports/surveillance-runs/json-task/_release-projection/jp.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    s.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_full_initialization_is_rejected_before_publication(setup):
    s = setup
    s.countries.append(("US", True))
    with pytest.raises(ValueError, match="all available"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    assert [name for name, _ in s.calls] == ["Capture Surveillance Publication Baseline", "Export Surveillance JSON"]
    s.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_candidate_receipt_never_reexports_or_uploads(setup):
    s = setup
    s.service.failure = True
    with pytest.raises(RuntimeError):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    (s.runtime.root_dir / "exports/surveillance-runs/json-task/ready.json").write_text("broken")
    s.calls.clear()
    with pytest.raises(RuntimeError, match="receipt is damaged"):
        await execute_release_task(s.service, s.task, runtime=s.runtime)
    assert s.calls == []
