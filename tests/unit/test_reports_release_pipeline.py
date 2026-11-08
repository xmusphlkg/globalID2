from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release.reports_pipeline import execute_reports_release


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", [None, "Build Reports Site", "Verify Reports Host", "empty index"])
async def test_reports_release_skips_public_verification_and_keeps_local_data(tmp_path, fail_at):
    astro = tmp_path / "astro-site"
    reports = tmp_path / "exports/reports-site"
    paths = [astro / "src/data/reports", astro / "public/site-data/reports", reports]
    for path in paths:
        path.mkdir(parents=True)
        (path / "old.json").write_text("old")
    (paths[0] / "index.json").write_text('[{"id": 1}]')
    commands = []
    stored = SimpleNamespace(output_data=None)
    class Database:
        async def get(self, *_args): return stored
        async def commit(self): pass
    @asynccontextmanager
    async def database(): yield Database()
    snapshot_open = False
    @asynccontextmanager
    async def read_snapshot():
        nonlocal snapshot_open
        snapshot_open = True
        try:
            yield {"GLOBALID_RELEASE_DB_SNAPSHOT": "00000003-0000001B-1"}
        finally:
            snapshot_open = False
    async def run(_task_uuid, *, title, **kwargs):
        commands.append((title, kwargs))
        if title == "Export Reports Data":
            assert snapshot_open
            assert kwargs["env"]["GLOBALID_RELEASE_DB_SNAPSHOT"] == "00000003-0000001B-1"
            (paths[0] / "old.json").write_text("new")
            if fail_at == "empty index":
                (paths[0] / "index.json").write_text("[]")
        else:
            assert not snapshot_open
            assert "GLOBALID_RELEASE_DB_SNAPSHOT" not in kwargs["env"]
        if title == fail_at:
            raise RuntimeError("injected failure")
    config = SimpleNamespace(r2_reports_public_base_url="https://reports.test", r2_reports_bucket="reports",
        r2_access_key_id="", r2_secret_access_key="", r2_endpoint_url="", r2_account_id="",
        main_site_origin="https://main.test")
    service = SimpleNamespace(load_jobs=AsyncMock(return_value=[SimpleNamespace(job_id="site-release")]),
        _r2_reports_enabled=lambda: True, _r2_reports_check=lambda **kw: {"blockers": []},
        _reports_site_dir=lambda: reports, _python_executable=lambda: Path("/python"), _config=lambda: config,
        _cloudflare_account_id=lambda: "account", _cloudflare_api_token=lambda: "token",
        _publish_reports_r2_command=lambda **kw: ["publish"], _verify_reports_host_command=lambda **kw: ["verify"],
        _run_logged_command=run)
    manager = SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock())
    runtime = SimpleNamespace(root_dir=tmp_path, astro_dir=astro, task_manager=manager, get_database=database,
        task_model=object, generate_timeout_seconds=60, astro_build_timeout_seconds=60, download_publish_timeout_seconds=60,
        read_snapshot=read_snapshot)
    task = SimpleNamespace(id=1, task_uuid="f49f0575-09b8-4d00-82b7-0e79b4056ae8", input_data={"release_job_id": "site-release"})
    if fail_at and fail_at != "Verify Reports Host":
        with pytest.raises(RuntimeError, match="empty|injected failure"):
            await execute_reports_release(service, task, runtime=runtime)
        assert (paths[0] / "old.json").read_text() == "new"
        if fail_at == "empty index":
            assert all(title != "Publish Reports Bundle to R2" for title, _ in commands)
    else:
        result = await execute_reports_release(service, task, runtime=runtime)
        assert result["reports_only"] and result["r2_reports_published"]
        assert not result["pages_deployed"]
        assert result["website_verification"] == "skipped"
        assert result["artifact_rollback"] is False
        assert any(call.kwargs.get("title") == "Website Verification Skipped" for call in manager.add_workbook_entry.await_args_list)
        assert stored.output_data == result
        assert [title for title, _ in commands] == ["Export Reports Data", "Build Reports Site", "Split Reports Bundle", "Publish Reports Bundle to R2"]
        assert "--reports-only" in commands[0][1]["cmd"]
        assert commands[1][1]["env"]["NODE_OPTIONS"] == "--max-old-space-size=6144"
        assert [kwargs["metadata"]["event"] for _, kwargs in commands] == [
            "reports_export", "reports_build", "reports_bundle", "r2_reports_publish",
        ]
        assert all("pages" not in title.lower() for title, _ in commands)
