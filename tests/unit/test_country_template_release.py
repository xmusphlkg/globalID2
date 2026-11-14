"""The planned country path compiles one family and preserves all other HTML."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release import publication_execution as execution
from src.services.data_release.build_stage import build_release_site
from src.services.data_release.pipeline import _built_site_digest
from tests.unit.test_release_stage_integration import release  # noqa: F401


def plan_inputs(tmp_path, warnings=()):
    baseline = tmp_path / "exports/verified-pages-snapshot"
    baseline.mkdir(parents=True)
    (baseline / "release.json").write_text('{"release_id":"base"}')
    for locale in ("", "zh/", "fr/"):
        page = baseline / f"{locale}countries/jp/index.html"
        page.parent.mkdir(parents=True)
        page.write_text("old country")
    data = tmp_path / "astro-site/src/data"
    data.mkdir(parents=True)
    (data / "meta.json").write_text('{"countries":[{"code":"JP"}]}')
    baseline_digest = _built_site_digest(baseline)
    (baseline / "release.json").write_text(json.dumps({"release_id": "base", "build_digest": baseline_digest}))
    (tmp_path / "exports/publicly-verified-site-release.json").write_text(json.dumps({
        "release_id": "base", "build_digest": baseline_digest, "public_verified_at": "2026-11-13T00:00:00Z"}))
    plan = {"kind": "template", "channels": [], "changed_files": [{"path": "astro-site/src/pages/countries/[country]/index.astro", "kind": "template"}],
            "expected_generations": {"site": "base"}, "_inventory_warnings": list(warnings)}
    service = SimpleNamespace(_config=lambda: SimpleNamespace())
    return baseline, plan, service


def test_country_plan_freezes_full_baseline_and_exact_existing_routes(tmp_path):
    baseline, plan, service = plan_inputs(tmp_path)
    execution.prepare_execution(plan, root=tmp_path, data_dir=tmp_path / "data", service=service,
                                kind="template", channel=None, job_configuration={})
    assert plan["execution_mode"] == "country_template"
    assert plan["html_page_count"] == 3 and plan["content_record_count"] == 0
    assert plan["_execution"]["country_baseline_digest"] == _built_site_digest(baseline)


@pytest.mark.parametrize("warning", ["published_dirty_source_baseline_unavailable", "published_source_baseline_unavailable",
    "published_source_revision_unavailable", "source_inventory_unavailable", "source_inventory_unstable", "source_symlink_requires_review"])
def test_incomplete_inventory_never_enters_narrow_country_path(tmp_path, warning):
    _, plan, service = plan_inputs(tmp_path, [warning])
    execution.prepare_execution(plan, root=tmp_path, data_dir=tmp_path / "data", service=service,
                                kind="template", channel=None, job_configuration={})
    assert plan["execution_mode"] == "code_only" and plan["html_page_count"] is None


@pytest.mark.asyncio
async def test_isolated_country_build_skips_all_other_render_and_split_commands(tmp_path):
    baseline, plan, service = plan_inputs(tmp_path)
    (baseline / "index.html").write_text("homepage stays identical")
    (baseline / "_astro").mkdir()
    (baseline / "_astro/old.js").write_text("old asset")
    updated_digest = _built_site_digest(baseline)
    (baseline / "release.json").write_text(json.dumps({"release_id": "base", "build_digest": updated_digest}))
    (tmp_path / "exports/publicly-verified-site-release.json").write_text(json.dumps({
        "release_id": "base", "build_digest": updated_digest, "public_verified_at": "2026-11-13T00:00:00Z"}))
    execution.prepare_execution(plan, root=tmp_path, data_dir=tmp_path / "data", service=service,
                                kind="template", channel=None, job_configuration={})
    async def command(_task_uuid, **kwargs):
        assert kwargs["title"] == "Compile Country Template"
        output = Path(kwargs["cmd"][kwargs["cmd"].index("--output") + 1])
        shutil.copytree(baseline, output)
        pages = [f"{locale}countries/jp/index.html" for locale in ("", "zh/", "fr/")]
        for page in pages:
            (output / page).write_text("new country")
        (output / "country-template-receipt.json").write_text(json.dumps({"family": "country", "pages": pages,
            "content_render_count": 0, "papers_render_count": 0, "reports_render_count": 0}))
    service._run_logged_command = AsyncMock(side_effect=command)
    service._record_release_checkpoint = AsyncMock()
    task = SimpleNamespace(task_uuid="country-task", input_data={"code_only": True, "country_template_only": True,
        "country_template_codes": ["jp"], "publication_plan": {key: plan["_execution"][key]
            for key in ("country_baseline_digest", "country_baseline_release_sha256")}})
    runtime = SimpleNamespace(root_dir=tmp_path, astro_dir=tmp_path / "astro-site", astro_build_timeout_seconds=60,
                              task_manager=SimpleNamespace(add_workbook_entry=AsyncMock()))
    identity = {"release_id": "new", "source_commit": "abc", "source_branch": "main", "deployment_branch": "main", "built_at": "now"}
    result = await build_release_site(service=service, task=task, runtime=runtime,
        job=SimpleNamespace(job_id="site", include_cloudflare_deploy=True), python_path=Path("python"),
        git_env={}, release_identity=identity, r2_papers_enabled=False, r2_reports_enabled=False,
        built_site_digest=_built_site_digest)
    assert service._run_logged_command.await_count == 1
    assert (runtime.astro_dir / "dist/index.html").read_text() == "homepage stays identical"
    assert (runtime.astro_dir / "dist/_astro/old.js").is_file()
    assert result["build_digest"] == _built_site_digest(runtime.astro_dir / "dist")
    # A successful Pages command can advance the shared snapshot before public
    # verification fails. Retry must keep the frozen candidate, not recompile
    # against that newer snapshot using the old baseline digest.
    (baseline / "release.json").write_text('{"release_id":"newer-snapshot"}')
    (baseline / "countries/jp/index.html").write_text("advanced baseline")
    retried = await build_release_site(service=service, task=task, runtime=runtime,
        job=SimpleNamespace(job_id="site", include_cloudflare_deploy=True), python_path=Path("python"),
        git_env={}, release_identity=identity, r2_papers_enabled=False, r2_reports_enabled=False,
        built_site_digest=_built_site_digest)
    assert service._run_logged_command.await_count == 1
    assert retried["build_digest"] == result["build_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_planned_static_release_requires_public_verification_before_ack(release, verified):
    from src.services.data_release.pipeline import _execute_release_task
    r = release
    r.task.input_data["publication_plan"] = {"plan_id": "test"}
    r.service._verify_cloudflare_production_release = AsyncMock(return_value={"verified": verified})
    r.service._record_release_checkpoint = AsyncMock()
    if not verified:
        with pytest.raises(RuntimeError, match="public verification"):
            await _execute_release_task(r.service, r.task, runtime=r.runtime)
        r.commit.assert_not_awaited()
    else:
        result = await _execute_release_task(r.service, r.task, runtime=r.runtime)
        assert result["website_verification"] == "verified"
        assert result["cloudflare_deployment"]["public_verified"]
        r.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_planned_release_with_correct_pages_and_missing_worker_route_is_not_acknowledged(release, monkeypatch):
    from urllib.error import HTTPError
    from src.services.data_release import checks
    from src.services.data_release.pipeline import _execute_release_task

    r = release
    r.task.input_data["publication_plan"] = {"plan_id": "test"}
    r.service.integration_checks = AsyncMock(return_value={
        "overall_ready": True, "cloudflare": {"production_branch": "main", "subdomain": "site.pages.dev"},
    })
    r.service._record_release_checkpoint = AsyncMock()
    marker = r.runtime.root_dir / "exports/publicly-verified-site-release.json"
    previous = marker.read_bytes() if marker.exists() else None

    async def verify(*, release_identity, **kwargs):
        async def public_json(url):
            if "/release.json?" in url:
                return release_identity
            raise HTTPError(url, 404, "Not Found", {}, None)
        deployment = {"environment": "production", "status": "success",
                      "branch": release_identity["deployment_branch"], "commit_hash": release_identity["source_commit"]}
        return await checks.verify_cloudflare_production_release(**kwargs, release_identity=release_identity,
            latest_deployment=AsyncMock(return_value=deployment), fetch_public_json=public_json,
            public_origin="https://public.example")

    r.service._verify_cloudflare_production_release = verify
    monkeypatch.setattr(checks.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match="surveillance-data Worker"):
        await _execute_release_task(r.service, r.task, runtime=r.runtime)
    r.commit.assert_not_awaited()
    assert (marker.read_bytes() if marker.exists() else None) == previous
    assert not any(call.args[2].get("public_verified") is True
                   for call in r.service._record_release_checkpoint.await_args_list)
