"""Template publication must stay independent from active data producers."""
import json
from pathlib import Path

import pytest

from src.services.data_release import code_workspace, stage_receipts
from src.services.data_release.stage_receipts import StageReceipts


def baseline(root):
    paths = [root / "astro-site/src/data", root / "astro-site/public/site-data"]
    for path in paths:
        path.mkdir(parents=True)
    (paths[0] / "meta.json").write_text('{"published":1}')
    (paths[0] / "changelog.ts").write_text("export const version = 1")
    (paths[1] / "index.json").write_text('{"index":1}')
    identity = {"release_id": "full-release", "build_digest": "built-full"}
    receipts = StageReceipts(root, "full-task", {}, {})
    receipts.prepare_published_data(identity, paths)
    receipts.assert_prepared_inputs(paths)
    receipts.mark_published(identity, data_paths=paths)
    return receipts, identity, paths


def test_background_edits_do_not_invalidate_or_mutate_the_retained_publication(tmp_path):
    receipts, _, paths = baseline(tmp_path)
    retained = receipts.published_data_paths(paths)
    original = paths[0] / "meta.json"
    assert original.stat().st_ino != (retained[0] / "meta.json").stat().st_ino
    original.write_text('{"unpublished":2}')
    (paths[1] / "new.json").write_text('{}')
    (paths[0] / "changelog.ts").write_text("export const version = 2")
    work = code_workspace.prepare(tmp_path, "template-task")
    assert (work / "src/data/meta.json").read_text() == '{"published":1}'
    assert (work / "src/data/changelog.ts").read_text() == "export const version = 2"
    assert not (work / "public/site-data/new.json").exists()
    (work / "src/data/meta.json").write_text('{"research-baseline-composition":3}')
    assert original.read_text() == '{"unpublished":2}'
    assert (retained[0] / "meta.json").read_text() == '{"published":1}'


@pytest.mark.parametrize("tamper", ["body", "extra", "missing", "manifest", "symlink", "binding"])
def test_corrupt_retained_snapshot_never_falls_back_to_active_inputs(tmp_path, tamper):
    receipts, _, paths = baseline(tmp_path)
    retained = receipts.published_data_paths(paths)
    if tamper == "body":
        (retained[0] / "meta.json").write_text('{"published":2}')
    elif tamper == "extra":
        (retained[1] / "extra.json").write_text('{}')
    elif tamper == "missing":
        (retained[0] / "meta.json").unlink()
    elif tamper == "manifest":
        (retained[0].parent / "manifest.json").write_text('{}')
    elif tamper == "symlink":
        (retained[0] / "meta.json").unlink()
        (retained[0] / "meta.json").symlink_to(paths[0] / "meta.json")
    else:
        marker = tmp_path / "exports/successful-site-release.json"
        value = json.loads(marker.read_bytes())
        value["retained_data_snapshot"]["release_id"] = "different-release"
        marker.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="verified published data snapshot"):
        receipts.published_data_paths(paths)


def test_racy_stat_digest_aging_does_not_invalidate_unchanged_build_inputs(tmp_path, monkeypatch):
    receipts, identity, paths = baseline(tmp_path)
    # Force the initial inventory into its digest window, then advance the
    # clock without sleeping. The later inventory legitimately has sha=None.
    now = max(path.stat().st_ctime_ns for directory in paths for path in directory.iterdir())
    monkeypatch.setattr(stage_receipts.time, "time_ns", lambda: now)
    receipts.prepare_published_data(identity, paths)
    assert receipts.prepared_input_files[str(paths[0] / "meta.json")]["sha256"]
    monkeypatch.setattr(stage_receipts.time, "time_ns", lambda: now + 5_000_000_000)
    receipts.assert_prepared_inputs(paths)
    (paths[0] / "meta.json").write_text('{"published":2}')
    with pytest.raises(RuntimeError, match="changed during rendering"):
        receipts.assert_prepared_inputs(paths)


def test_retention_is_prepared_before_success_and_ready_retry_preserves_workspace(tmp_path):
    receipts, _, paths = baseline(tmp_path)
    work = code_workspace.prepare(tmp_path, "template-task")
    build_paths = [work / "src/data", work / "public/site-data"]
    identity = {"release_id": "template-release", "build_digest": "built-template"}
    task = StageReceipts(tmp_path, "template-task", {}, {})
    task.prepare_published_data(identity, build_paths)
    task.assert_prepared_inputs(build_paths)
    signature = task.signature("ready", identity)
    assert task.save("ready", signature, build_paths, identity=identity)
    original_inode = (work / "src/data/meta.json").stat().st_ino
    (paths[0] / "meta.json").write_text('{"background":4}')
    (tmp_path / 'CHANGELOG.md').write_text('A newer independently published version note')
    assert code_workspace.prepare(tmp_path, "template-task") == work
    assert (work / "src/data/meta.json").stat().st_ino == original_inode
    loaded = StageReceipts(tmp_path, "template-task", {}, {})
    assert loaded.matches("ready", signature)
    loaded.mark_published(identity, data_paths=build_paths)
    assert loaded.published_data_paths(paths)[0].joinpath("meta.json").read_text() == '{"published":1}'


def test_prepared_candidate_is_not_successful_until_deploy_mark(tmp_path):
    _, _, paths = baseline(tmp_path)
    marker = tmp_path / "exports/successful-site-release.json"
    previous = marker.read_bytes()
    (paths[0] / "meta.json").write_text('{"next":2}')
    candidate = StageReceipts(tmp_path, "next-task", {}, {})
    candidate.prepare_published_data({"release_id": "next"}, paths)
    assert marker.read_bytes() == previous


def test_missing_legacy_snapshot_cannot_authorize_a_workspace(tmp_path):
    (tmp_path / "astro-site/src/data").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="normal data release"):
        code_workspace.prepare(tmp_path, "first-template")


def test_private_service_commands_resolve_data_and_dist_in_the_workspace(tmp_path, monkeypatch):
    from src.services.data_release_service import DataReleaseService
    from types import SimpleNamespace
    service = DataReleaseService()
    monkeypatch.setattr(service, '_config', lambda: SimpleNamespace(main_site_origin='https://site.test',
        r2_papers_public_base_url='https://papers.test', r2_reports_public_base_url='https://reports.test',
        pages_file_budget=20000, pages_file_warn=18000, literature_json_enabled=True))
    monkeypatch.setattr(service, '_papers_site_dir', lambda: tmp_path / 'exports/papers-site')
    monkeypatch.setattr(service, '_reports_site_dir', lambda: tmp_path / 'exports/reports-site')
    private = tmp_path / 'exports/code-workspaces/task/astro-site'
    (private / 'dist').mkdir(parents=True)
    for builder in (service._split_papers_bundle_command, service._split_reports_bundle_command):
        command = builder(python_path=Path('/venv/python'), astro_dir=private)
        assert command[command.index('--dist-dir') + 1] == str(private / 'dist')
    command = service._publish_papers_r2_command(python_path=Path('/venv/python'), astro_dir=private)
    for flag, relative in (('--site-dist', 'dist'), ('--articles-dir', 'src/data/research/articles'),
                           ('--research-source', 'public/site-data/research')):
        assert command[command.index(flag) + 1] == str(private / relative)
    service._write_site_release_manifest({'release_id':'private'}, astro_dir=private)
    assert json.loads((private / 'dist/release.json').read_bytes())['release_id'] == 'private'
    assert not (tmp_path / 'astro-site/dist/release.json').exists()


def test_private_cache_import_remains_independent_and_keeps_validation_manifests(tmp_path):
    baseline(tmp_path)
    cache = tmp_path / 'data/cache'
    (cache / 'site-pages').mkdir(parents=True)
    (cache / 'site-page-manifest.json').write_text('{"schema":1}')
    (cache / 'site-pages/index.html').write_text('validated page')
    work = code_workspace.prepare(tmp_path, 'cached-template')
    imported = work.parent / 'data/cache'
    assert (imported / 'site-page-manifest.json').read_text() == '{"schema":1}'
    page = imported / 'site-pages/index.html'
    assert page.stat().st_ino != (cache / 'site-pages/index.html').stat().st_ino
    page.write_text('new private page')
    assert (cache / 'site-pages/index.html').read_text() == 'validated page'


@pytest.mark.parametrize('independent_research', [False, True])
@pytest.mark.parametrize('previous_workspace', [False, True])
def test_workspace_imports_only_the_caches_used_by_its_research_mode(tmp_path, monkeypatch, independent_research, previous_workspace):
    baseline(tmp_path)
    cache_root = tmp_path
    if previous_workspace:
        cache_root = tmp_path / 'exports/code-workspaces/full-task'
        cache_root.mkdir(parents=True)
        (cache_root / 'workspace.json').write_text('{}')
    cache = cache_root / 'data/cache'
    entries = ['site-page-manifest.json', 'site-pages/index.html', 'astro-assets/client.js',
               'research-build-manifest.json', 'research-pages/article.html']
    for relative in entries:
        path = cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    original_files = code_workspace.files

    def scan(source):
        # Skipping the tree must avoid walking it, not just discard its copies.
        assert not (independent_research and source == cache / 'research-pages')
        return original_files(source)

    monkeypatch.setattr(code_workspace, 'files', scan)
    work = code_workspace.prepare(tmp_path, 'mode-cache', independent_research=independent_research)
    imported = work.parent / 'data/cache'
    for relative in entries:
        target = imported / relative
        if independent_research and relative.startswith('research-'):
            assert not target.exists()
        else:
            assert target.read_text() == relative
            assert target.stat().st_ino != (cache / relative).stat().st_ino
        assert (cache / relative).read_text() == relative
    assert (work / 'src/data/meta.json').read_text() == '{"published":1}'


def test_research_mode_change_rebuilds_nonfrozen_workspace_but_recovery_preserves_it(tmp_path):
    baseline(tmp_path)
    legacy = tmp_path / 'data/cache/research-pages/article.html'
    legacy.parent.mkdir(parents=True)
    legacy.write_text('legacy cache')
    work = code_workspace.prepare(tmp_path, 'mode-change', independent_research=True)
    task = StageReceipts(tmp_path, 'mode-change', {}, {})
    identity = {'release_id': 'mode-change-release'}
    assert task.save('ready', task.signature('ready', identity), [work / 'src/data'], identity=identity)
    original_inode = (work / 'src/data/meta.json').stat().st_ino
    assert code_workspace.prepare(tmp_path, 'mode-change', independent_research=True) == work
    assert (work / 'src/data/meta.json').stat().st_ino == original_inode
    rebuilt = code_workspace.prepare(tmp_path, 'mode-change', independent_research=False)
    assert (rebuilt.parent / 'data/cache/research-pages/article.html').read_text() == 'legacy cache'
    frozen_before = (rebuilt.parent / 'workspace.json').read_bytes()
    recovered = code_workspace.prepare(tmp_path, 'mode-change', recovery=True, independent_research=True)
    assert recovered == rebuilt
    assert (recovered.parent / 'workspace.json').read_bytes() == frozen_before
    assert (recovered.parent / 'data/cache/research-pages/article.html').read_text() == 'legacy cache'


def test_nonfrozen_retry_reloads_changed_rendering_configuration(tmp_path):
    baseline(tmp_path)
    settings = tmp_path / 'data/system-settings.json'
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"site":{"public_ga4_measurement_id":"G-OLD123"}}')
    work = code_workspace.prepare(tmp_path, 'settings-retry')
    task = StageReceipts(tmp_path, 'settings-retry', {}, {})
    identity = {'release_id':'settings-release'}
    assert task.save('ready', task.signature('ready', identity), [work / 'src/data'], identity=identity)
    settings.write_text('{"site":{"public_ga4_measurement_id":"G-NEW123"}}')
    rebuilt = code_workspace.prepare(tmp_path, 'settings-retry')
    assert json.loads((rebuilt.parent / 'data/system-settings.json').read_bytes())['site']['public_ga4_measurement_id'] == 'G-NEW123'
