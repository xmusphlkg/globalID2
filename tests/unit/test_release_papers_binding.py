import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release.release_binding import papers_binding_matches
from src.services.data_release.stage_receipts import StageReceipts
from src.services.data_release.shared_assets import verified_snapshot
from src.services.data_release.pipeline import _built_site_digest
from src.services.data_release.cloudflare_deploy import deploy_cloudflare_site
from src.services.data_release.artifact_checkpoint import ArtifactCheckpoint
from src.services.data_release import checks


def identity(generation="a", digest="b"):
    generation *= 64
    return {"release_id": "r1", "build_digest": "c" * 64, "source_commit": "abc",
            "deployment_branch": "main", "commit_dirty": False,
            "papers_origin": "https://papers.example", "papers_generation_sha256": generation,
            "papers_manifest_sha256": digest * 64,
            "papers_manifest_url": f"https://papers.example/releases/sha256/{generation}/papers-manifest.json"}


def test_binding_comparison_rejects_missing_partial_and_changed():
    bound = identity()
    assert papers_binding_matches(bound, bound)
    assert papers_binding_matches({}, {})
    assert not papers_binding_matches(bound, {})
    assert not papers_binding_matches({"papers_origin": bound["papers_origin"]}, {"papers_origin": bound["papers_origin"]})
    assert not papers_binding_matches(bound, identity(digest="d"))
    assert not papers_binding_matches(bound, identity(generation="d"))


def test_published_marker_and_snapshot_include_binding(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("site")
    bound = {**identity(), "build_digest": _built_site_digest(dist)}
    (dist / "release.json").write_text(json.dumps(bound))
    receipts = StageReceipts(tmp_path, "task", {}, {})
    receipts.mark_published(bound)
    assert receipts.is_current_publication(bound)
    assert not receipts.is_current_publication(identity(digest="d") | {"build_digest": bound["build_digest"]})
    assert verified_snapshot(dist, bound)
    assert not verified_snapshot(dist, {**bound, "papers_manifest_sha256": "d" * 64})
    assert not verified_snapshot(dist, {key: value for key, value in bound.items() if not key.startswith("papers_")})


def test_retained_candidate_rejects_tampered_release_manifest(tmp_path):
    paths = [tmp_path / f"tree-{i}" for i in range(3)]
    for path in paths:
        path.mkdir()
        (path / "data").write_text("frozen")
    projection = paths[0] / "_release-projection"
    projection.mkdir()
    (projection / "manifest.json").write_text("{}")
    bound = {**identity(), "build_digest": _built_site_digest(paths[2])}
    (paths[2] / "release.json").write_text(json.dumps(bound))
    checkpoint = ArtifactCheckpoint(tmp_path, "task", paths, {})
    signature = checkpoint.signature({})
    checkpoint.save_ready(signature, bound)
    assert checkpoint.retain()
    (checkpoint.directory / "2" / "release.json").write_text(json.dumps({**bound, "papers_manifest_sha256": "d" * 64}))
    assert checkpoint.restore(signature, bound, _built_site_digest) is None


@pytest.mark.asyncio
async def test_public_release_verification_rejects_binding_change(monkeypatch):
    bound = identity()
    async def latest(*args, **kwargs):
        return {"environment": "production", "status": "success", "branch": "main", "commit_hash": "abc"}
    async def public(*args):
        return {**bound, "papers_manifest_sha256": "d" * 64}
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(checks.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="could not be verified"):
        await checks.verify_cloudflare_production_release(project_name="site", subdomain="site.example",
                                                         release_identity=bound, latest_deployment=latest,
                                                         fetch_public_json=public)


@pytest.mark.asyncio
async def test_deploy_checkpoint_with_changed_binding_cannot_be_reused(tmp_path):
    bound = identity()
    checkpoint = {**identity(digest="d"), "artifact_digest": bound["build_digest"],
                  "project_name": "site", "account_id": "account"}
    service = SimpleNamespace(_cloudflare_account_id=lambda: "account", _cloudflare_api_token=lambda: "token",
                              _cloudflare_deploy_command=lambda **kwargs: ["deploy"],
                              _run_logged_command=AsyncMock(), _record_release_checkpoint=AsyncMock())
    receipts = SimpleNamespace(is_current_publication=lambda _: True, mark_published=lambda *a, **k: None)
    runtime = SimpleNamespace(astro_dir=tmp_path, root_dir=tmp_path, cloudflare_deploy_timeout_seconds=10,
                              task_manager=SimpleNamespace(add_workbook_entry=AsyncMock()))
    await deploy_cloudflare_site(service=service, task=SimpleNamespace(task_uuid="task"), runtime=runtime,
                                 job=SimpleNamespace(job_id="job"), stage_receipts=receipts,
                                 release_identity=bound, release_checkpoints={"cloudflare_deploy": checkpoint},
                                 project_name="site", deployment_branch="main", publish_commit_message="test")
    service._run_logged_command.assert_awaited_once()
    saved = service._record_release_checkpoint.await_args.args[2]
    assert papers_binding_matches(saved, bound)


@pytest.mark.asyncio
async def test_deploy_refuses_partial_binding_before_external_effect(tmp_path):
    service = SimpleNamespace(_run_logged_command=AsyncMock())
    with pytest.raises(RuntimeError, match="binding"):
        await deploy_cloudflare_site(service=service, task=None, runtime=None, job=None,
                                     stage_receipts=None, release_identity={"papers_origin": "https://papers.example"},
                                     release_checkpoints={}, project_name="site", deployment_branch="main",
                                     publish_commit_message="test")
    service._run_logged_command.assert_not_awaited()
