import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.data_release.build_reuse import BuildReuse, stamp_homepages
from src.services.data_release.build_stage import build_release_site


HOME = '<html data-release-id="development"><head><meta name="gids-source-commit" content=""><meta content="" name="gids-deployment-branch"></head><body>Public content</body></html>'
IDENTITY = dict(release_id="release-1", source_commit="abc", source_branch="main", deployment_branch="main", built_at="2026-11-06")


def put(path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def artifacts(root):
    for page in ("index.html", "fr/index.html", "zh/index.html"):
        put(root / "astro-site/dist" / page, HOME)
    for name in ("papers-site", "reports-site"):
        put(root / "exports" / name / "index.html", "validated partition")


@pytest.fixture
def tree(tmp_path):
    put(tmp_path / "astro-site/src/data/meta.json", '{"version":1}')
    put(tmp_path / "astro-site/src/pages/index.astro", "template")
    put(tmp_path / "astro-site/postcss.config.mjs", "config")
    artifacts(tmp_path)
    return tmp_path


def reuse(root, **kwargs):
    return BuildReuse(root, split=True, configuration=kwargs.pop("configuration", {"target": "main"}), node_version="test-node", **kwargs)


def validated(root):
    cache = reuse(root)
    inputs = cache.inputs()
    assert cache.save(inputs, stamped=stamp_homepages(root / "astro-site/dist", IDENTITY))
    return inputs


def test_receipt_survives_new_task_identity_but_never_proves_remote_publication(tree):
    inputs = validated(tree)
    put(tree / "astro-site/dist/release.json", '{"release_id":"old"}')
    put(tree / "astro-site/src/data/_release-projection/manifest.json", '{"change_ids":[999]}')
    put(tree / "exports/papers-site/.git/config", "new publisher metadata")
    cache = reuse(tree)
    assert cache.inputs() == inputs
    assert cache.matches(inputs)
    newer = {**IDENTITY, "release_id": 'release-2"&', "source_commit": "def"}
    assert stamp_homepages(tree / "astro-site/dist", newer)
    assert cache.save(inputs, stamped=True)
    assert 'data-release-id="release-2&quot;&amp;"' in (tree / "astro-site/dist/index.html").read_text()
    assert reuse(tree).matches(inputs)
    assert not (tree / "exports/successful-site-release.json").exists()


@pytest.mark.parametrize("path", ["astro-site/src/pages/index.astro", "astro-site/src/data/meta.json",
    "astro-site/postcss.config.mjs", "astro-site/public/new.json", "scripts/validate-new.py", "shared/schema.json"])
def test_changed_rendering_or_validation_input_requires_build(tree, path):
    previous = validated(tree)
    put(tree / path, "changed input")
    cache = reuse(tree)
    assert cache.inputs() != previous
    assert not cache.matches(cache.inputs())


def test_version_notes_do_not_invalidate_validated_templates(tree):
    put(tree / "astro-site/public/logo.svg", "<svg/>")
    previous = validated(tree)
    put(tree / "astro-site/src/data/changelog.ts", "export const changelogReleases = ['new version'];")
    put(tree / "astro-site/public/site-data/updates/changelog.json", '{"schema_version":1,"releases":[]}')
    put(tree / "CHANGELOG.md", "## [99.0.0] - 2026-11-14")
    cache = reuse(tree)
    assert cache.inputs() == previous
    assert cache.matches(previous)


@pytest.mark.parametrize("mutation", ["changed", "deleted", "extra", "extra-directory", "bad-receipt"])
def test_all_post_split_outputs_are_verified(tree, mutation):
    inputs = validated(tree)
    if mutation == "changed":
        put(tree / "exports/papers-site/index.html", "damaged")
    elif mutation == "deleted":
        (tree / "exports/reports-site/index.html").unlink()
    elif mutation == "extra":
        put(tree / "astro-site/dist/extra.html", "unexpected")
    elif mutation == "extra-directory":
        put(tree / "astro-site/dist/_release-projection/unexpected.json")
    else:
        put(tree / "data/cache/validated-site-build.json", "broken JSON")
    assert not reuse(tree).matches(inputs)


def test_unstamped_or_changed_inputs_cannot_promote_a_receipt(tree, monkeypatch):
    cache = reuse(tree)
    inputs = cache.inputs()
    assert not cache.save(inputs, stamped=False)
    assert not cache.path.exists()
    put(tree / "astro-site/src/data/meta.json", "changed")
    assert not cache.save(inputs, stamped=True)
    assert not cache.path.exists()
    validated(tree)
    changed = reuse(tree, configuration={"target": "different"})
    assert not changed.matches(changed.inputs())
    monkeypatch.setenv("PUBLIC_GIDS_MAINTENANCE_MESSAGE", "maintenance")
    assert not reuse(tree).matches(reuse(tree).inputs())


@pytest.mark.asyncio
async def test_real_build_entrypoint_skips_commands_and_restamps_only_after_validated_build(tree, monkeypatch):
    calls = []
    build_environments = []
    manager = SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock())
    runtime = SimpleNamespace(root_dir=tree, astro_dir=tree / "astro-site", task_manager=manager, astro_build_timeout_seconds=60)
    job = SimpleNamespace(job_id="release", include_cloudflare_deploy=True)
    task = SimpleNamespace(task_uuid="first")

    async def run(*args, title, **kwargs):
        calls.append(title)
        if title == "Build Astro Site":
            build_environments.append(kwargs['env'])
            artifacts(tree)

    service = SimpleNamespace(_config=lambda: SimpleNamespace(site="test"), _run_logged_command=run,
        _validate_situation_release_command=lambda **kw: ["validate"],
        _split_papers_bundle_command=lambda **kw: ["papers"], _split_reports_bundle_command=lambda **kw: ["reports"])
    options = dict(service=service, task=task, runtime=runtime, job=job, python_path=Path("python"),
        git_env={}, r2_papers_enabled=True, r2_reports_enabled=True, built_site_digest=lambda root: "current-digest")
    await build_release_site(**options, release_identity=IDENTITY)
    assert calls == ["Build Astro Site", "Validate Situation Release Gate", "Split Papers Bundle", "Split Reports Bundle"]
    calls.clear()
    task.task_uuid = "second"
    result = await build_release_site(**options, release_identity={**IDENTITY, "release_id": "second"})
    assert calls == []
    assert result["release_id"] == "second" and result["build_digest"] == "current-digest"
    assert 'data-release-id="second"' in (runtime.astro_dir / "dist/index.html").read_text()
    # The legacy full selector and a template plan do not turn unrelated jobs
    # into force renders. A frozen full plan does, even with identical bytes.
    for selected in [{'full': True}, {'full': True, 'publication_plan': {'execution_mode': 'code_only'}}]:
        task.input_data = selected
        await build_release_site(**options, release_identity=IDENTITY)
        assert calls == []
    task.input_data = {'full': True, 'publication_plan': {'execution_mode': 'full'}}
    with monkeypatch.context() as scoped:
        scoped.setenv('GIDS_FORCE_FULL_RENDER', '0')
        scoped.setattr(BuildReuse, 'matches', lambda *_: pytest.fail('Full publication must not consult a reusable output'))
        await build_release_site(**options, release_identity=IDENTITY)
    assert calls == ['Build Astro Site', 'Validate Situation Release Gate', 'Split Papers Bundle', 'Split Reports Bundle']
    assert build_environments[-1]['GIDS_FORCE_FULL_RENDER'] == '1'
    assert (tree / 'data/cache/validated-site-build.json').is_file(), 'Fresh validated outputs may still populate the cache'
    calls.clear()
    task.input_data = {'publication_plan': {'execution_mode': 'code_only'}}
    put(runtime.astro_dir / "src/data/meta.json", "new data")
    with monkeypatch.context() as scoped:
        scoped.setenv('GIDS_FORCE_FULL_RENDER', '1')
        await build_release_site(**options, release_identity=IDENTITY)
    assert calls[0] == "Build Astro Site"
    assert build_environments[-1]['GIDS_FORCE_FULL_RENDER'] == '0', 'Ambient flags cannot escalate a template plan to a full render'


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["preflight-cache", "save-cache", "validation"])
async def test_optional_cache_failure_falls_back_but_failed_validation_never_promotes(tree, monkeypatch, failure):
    calls = []
    async def run(*args, title, **kwargs):
        calls.append(title)
        if title == "Build Astro Site": artifacts(tree)
        if failure == "validation" and title == "Validate Situation Release Gate":
            raise RuntimeError("invalid situation output")
    def unavailable(*args, **kwargs):
        raise OSError("cache is unavailable")
    if failure == "preflight-cache": monkeypatch.setattr(BuildReuse, "inputs", unavailable)
    if failure == "save-cache": monkeypatch.setattr(BuildReuse, "save", unavailable)
    runtime = SimpleNamespace(root_dir=tree, astro_dir=tree / "astro-site", astro_build_timeout_seconds=60,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()))
    service = SimpleNamespace(_config=lambda: SimpleNamespace(site="test"), _run_logged_command=run,
        _validate_situation_release_command=lambda **kw: ["validate"],
        _split_papers_bundle_command=lambda **kw: ["papers"], _split_reports_bundle_command=lambda **kw: ["reports"])
    options = dict(service=service, runtime=runtime, task=SimpleNamespace(task_uuid="failure"),
        job=SimpleNamespace(job_id="release", include_cloudflare_deploy=True), python_path=Path("python"),
        git_env={}, r2_papers_enabled=True, r2_reports_enabled=True, built_site_digest=lambda root: "digest",
        release_identity=IDENTITY)
    if failure == "validation":
        with pytest.raises(RuntimeError, match="invalid situation output"):
            await build_release_site(**options)
        assert "Split Papers Bundle" not in calls
    else:
        assert (await build_release_site(**options))["build_digest"] == "digest"
        assert "Split Reports Bundle" in calls
    assert not (tree / "data/cache/validated-site-build.json").exists()


@pytest.mark.parametrize("attribute", ["content", 'content=""', "content=''", "content=previous"])
def test_stamping_accepts_astro_compressed_empty_attributes(tree, attribute):
    for page in ("index.html", "fr/index.html", "zh/index.html"):
        put(tree / "astro-site/dist" / page, HOME.replace('content=""', attribute))
    assert stamp_homepages(tree / "astro-site/dist", IDENTITY)
    assert 'content="abc"' in (tree / "astro-site/dist/index.html").read_text()
