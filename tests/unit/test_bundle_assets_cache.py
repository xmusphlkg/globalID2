from pathlib import Path

import pytest

from src.services.data_release import artifact_index, bundle_assets
from src.services.data_release.artifact_index import ArtifactIndex
from src.services.data_release.bundle_assets import copy_bundle_assets


def test_cached_html_references_still_validate_assets_and_detect_page_changes(tmp_path, monkeypatch):
    dist, bundle = tmp_path / "dist", tmp_path / "bundle"
    (dist / "_astro").mkdir(parents=True)
    bundle.mkdir()
    page = bundle / "index.html"
    page.write_text('<link href="/_astro/a.css">')
    (dist / "_astro/a.css").write_text('body{color:red}')
    # Advance the receipt clock so files leave the racy-timestamp window without
    # sleeping. Filesystem metadata still changes normally on every edit.
    now = artifact_index.time.time_ns
    monkeypatch.setattr(artifact_index.time, "time_ns", lambda: now() + 2_000_000_000)
    index = ArtifactIndex(bundle, cache_dir=tmp_path / "cache")
    copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    index.save()
    index = ArtifactIndex(bundle, cache_dir=tmp_path / "cache")
    original = Path.read_bytes

    def read(path):
        if path == page:
            raise AssertionError("Unchanged HTML was read again")
        return original(path)

    with monkeypatch.context() as context:
        context.setattr(Path, "read_bytes", read)
        copy_bundle_assets(dist, bundle, [page], artifact_index=index)
        (dist / "_astro/a.css").unlink()
        with pytest.raises(FileNotFoundError, match="Bundle asset missing"):
            copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    page.write_text('<script src="/_astro/b.js"></script>')
    (dist / "_astro/b.js").write_text('import("./lazy.js")')
    (dist / "_astro/lazy.js").write_text('export default 1')
    copied = copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    assert copied == {bundle / "_astro/b.js", bundle / "_astro/lazy.js"}
    # Changes to an existing asset must refresh its transitive dependency graph.
    (dist / "_astro/b.js").write_text('import("./other.js")')
    (dist / "_astro/other.js").write_text('export default 2')
    copied = copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    assert copied == {bundle / "_astro/b.js", bundle / "_astro/other.js"}


def test_parser_version_changes_and_damaged_reference_cache_reparse(tmp_path, monkeypatch):
    dist, bundle = tmp_path / "dist", tmp_path / "bundle"
    dist.mkdir(); bundle.mkdir()
    page = bundle / "index.html"
    page.write_text('<p>No assets</p>')
    index = ArtifactIndex(bundle, cache_dir=tmp_path / "cache")
    copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    monkeypatch.setattr(bundle_assets, "REFERENCE_SCHEMA", "new-parser")
    copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    assert index.entries[str(page.resolve())]["asset_references"]["schema"] == "new-parser"
    index.entries[str(page.resolve())]["asset_references"]["tokens"] = None
    assert copy_bundle_assets(dist, bundle, [page], artifact_index=index) == set()


def test_parallel_reference_parsing_preserves_assets_and_propagates_worker_failure(tmp_path, monkeypatch):
    from src.services.data_release.bundle_asset_workers import warm_asset_references
    dist, bundle = tmp_path / "dist", tmp_path / "bundle"
    (dist / "_astro").mkdir(parents=True)
    bundle.mkdir()
    (dist / "_astro/app.js").write_text('import("./lazy.js")')
    (dist / "_astro/lazy.js").write_text("export const value = 1")
    pages = [bundle / f"{number}.html" for number in range(64)]
    for page in pages:
        page.write_text('<script src="/_astro/app.js"></script>')
    index = ArtifactIndex(bundle, cache_dir=tmp_path / "cache")
    assert warm_asset_references(pages, index, workers=2) == 64
    assert all(index.entries[str(page.resolve())]["asset_references"]["tokens"] == ["/_astro/app.js"] for page in pages)
    monkeypatch.setenv("GIDS_BUNDLE_ASSET_WORKERS", "2")
    copied = copy_bundle_assets(dist, bundle, pages, artifact_index=index)
    assert copied == {bundle / "_astro/app.js", bundle / "_astro/lazy.js"}
    assert (bundle / "_astro/lazy.js").read_bytes() == (dist / "_astro/lazy.js").read_bytes()
    pages[0].write_text('<script src="/_astro/missing.js"></script>')
    with pytest.raises(FileNotFoundError, match="Bundle asset missing"):
        copy_bundle_assets(dist, bundle, pages, artifact_index=index)
    pages[0].write_bytes(b"\xff")
    fresh = ArtifactIndex(bundle, cache_dir=tmp_path / "fresh-cache")
    with pytest.raises(UnicodeDecodeError):
        warm_asset_references(pages, fresh, workers=2)
    assert not fresh.path.exists()


def test_bundle_graph_observes_new_missing_targets_changed_imports_and_repairs_outputs(tmp_path, monkeypatch):
    dist, bundle = tmp_path / 'dist', tmp_path / 'bundle'
    (dist / '_astro').mkdir(parents=True); bundle.mkdir()
    (dist / '_astro/app.js').write_text('import("./one.js")')
    (dist / '_astro/one.js').write_text('export default 1')
    page = bundle / 'index.html'
    page.write_text('<script src="/_astro/app.js"></script><img src="/optional.png">')
    index = ArtifactIndex(bundle, cache_dir=tmp_path / 'cache')
    original = copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    with monkeypatch.context() as context:
        context.setattr(bundle_assets, '_asset_references', lambda *a, **k: pytest.fail('Unchanged bundle was parsed again'))
        assert copy_bundle_assets(dist, bundle, [page], artifact_index=index) == original
        (bundle / '_astro/one.js').unlink()
        copy_bundle_assets(dist, bundle, [page], artifact_index=index)
        assert (bundle / '_astro/one.js').is_file()
    (dist / 'optional.png').write_bytes(b'image')
    assert bundle / 'optional.png' in copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    (dist / '_astro/app.js').write_text('import("./two.js")')
    (dist / '_astro/two.js').write_text('export default 2')
    copied = copy_bundle_assets(dist, bundle, [page], artifact_index=index)
    assert bundle / '_astro/two.js' in copied and bundle / '_astro/one.js' not in copied
    (dist / '_astro/two.js').unlink()
    with pytest.raises(FileNotFoundError, match='Bundle asset missing'):
        copy_bundle_assets(dist, bundle, [page], artifact_index=index)
