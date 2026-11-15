from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import data_release_service as module
from src.services.data_release.stage_receipts import StageReceipts
from src.services.data_release_service import DataReleaseJobConfig, DataReleaseService


@pytest.mark.parametrize("snapshot", ["valid", "missing", "changed", "retained"])
async def test_code_only_requires_published_snapshot_and_skips_data_transports(tmp_path, monkeypatch, snapshot):
    service = DataReleaseService()
    astro = tmp_path / "astro"
    data_roots = [astro / "src/data", astro / "public/site-data"]
    for root in data_roots:
        root.mkdir(parents=True)
    data_file = data_roots[0] / "countries.json"
    data_file.write_text('{"US":1}')
    if snapshot != "missing":
        receipts = StageReceipts(tmp_path, "published", {}, {})
        identity = {"release_id": "successful", "build_digest": "abc"}
        if snapshot == "retained":
            receipts.prepare_published_data(identity, data_roots)
            receipts.assert_prepared_inputs(data_roots)
        receipts.mark_published(identity, data_paths=data_roots)
    if snapshot in {"changed", "retained"}:
        data_file.write_text('{"US":1234}')
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(module, "ASTRO_DIR", astro)
    monkeypatch.setattr(module, "missing_public_changelog_versions", lambda **_kwargs: [])
    monkeypatch.setattr(service, "ensure_storage", AsyncMock())
    monkeypatch.setattr(service, "load_jobs", AsyncMock(return_value=[DataReleaseJobConfig(
        job_id="site", name="Site", include_git_push=False, include_cloudflare_deploy=True,
    )]))
    monkeypatch.setattr(service, "_download_repo_branch", lambda _job: "main")
    monkeypatch.setattr(service, "_download_repo_url", lambda: "")
    monkeypatch.setattr(service, "_download_repo_raw_base", lambda _job: "")
    monkeypatch.setattr(service, "_download_repo_check", AsyncMock(side_effect=AssertionError("unexpected download probe")))
    monkeypatch.setattr(service, "_raw_archive_check", AsyncMock(side_effect=AssertionError("unexpected raw archive probe")))
    monkeypatch.setattr(service, "_raw_archive_runtime", lambda: SimpleNamespace(enabled=True, branch="main", repo_url="raw.git"))
    monkeypatch.setattr(service, "_git_status_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_tracked_generated_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_python_executable", lambda: python)
    monkeypatch.setattr(service, "_run_capture", AsyncMock(return_value={"returncode": 0, "stdout": "version"}))
    monkeypatch.setattr(service, "_cloudflare_check", AsyncMock(return_value={
        "payload": {"project_access_ok": True, "production_branch": "master"}, "blockers": [],
    }))
    for name in ("papers", "reports"):
        monkeypatch.setattr(service, f"_r2_{name}_enabled", lambda _job: True)
        monkeypatch.setattr(service, f"_r2_{name}_check", lambda **_kwargs: {"payload": {"bucket_access_ok": True}, "blockers": []})
    monkeypatch.setattr(service, "_pages_budget_snapshot", lambda: {"file_count": 10, "budget": 20000})

    checks = await service.integration_checks("site", code_only=True)

    assert checks["overall_ready"] is (snapshot in {"valid", "retained"})
    assert checks["raw_archive"]["independent"] is True
    assert checks["channels"]["github_downloads"]["enabled"] is False
    assert not any("requires direct-download" in message for message in checks["blockers"])
    if snapshot not in {"valid", "retained"}:
        assert any("data snapshot" in message for message in checks["blockers"])
    service._download_repo_check.assert_not_called()
    service._raw_archive_check.assert_not_called()
    # Code-only needs Wrangler, but never invokes the Situation refresh helper.
    assert service._run_capture.call_count == 1
