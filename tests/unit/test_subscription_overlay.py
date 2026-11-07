import json
from pathlib import Path

import pytest

from scripts.automation import prepare_subscription_overlay as overlay
from src.services.data_release.pipeline import _built_site_digest
from src.services.data_release.shared_assets import verified_snapshot


def fixtures(tmp_path, monkeypatch):
    base, build, root = (tmp_path / name for name in ("base", "build", "root"))
    base.mkdir()
    build.mkdir()
    root.mkdir()
    (base / "index.html").write_text('unchanged baseline homepage')
    for page in overlay.PAGES:
        for directory in (base, build):
            path = directory / page
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('old' if directory == base else '<html data-release-id="release-1"><meta content="abc">pilot_mode consent<link href="/_astro/test.css"></html>')
    (build / '_astro').mkdir()
    (build / '_astro/test.css').write_text('new CSS')
    identity = {'release_id': 'release-1', 'source_commit': 'abc', 'build_digest': _built_site_digest(base)}
    (base / 'release.json').write_text(json.dumps(identity))
    monkeypatch.setattr(overlay, 'source_digest', lambda root: 'source-digest')
    monkeypatch.setattr(overlay.subprocess, 'check_output', lambda *a, **kw: 'checkout\n')
    return base, build, root, identity


def test_overlay_retains_baseline_and_identifies_changed_files(tmp_path, monkeypatch):
    base, build, root, identity = fixtures(tmp_path, monkeypatch)
    target = tmp_path / 'candidate'
    result = overlay.prepare(base, build, target, identity, root)
    assert verified_snapshot(base, identity)
    assert verified_snapshot(target, result)
    assert (target / 'index.html').read_text() == (base / 'index.html').read_text()
    assert result['release_id'] == identity['release_id']
    assert result['build_digest'] != identity['build_digest']
    assert result['overlay']['base_build_digest'] == identity['build_digest']
    assert set(result['overlay']['files_sha256']) == set(overlay.PAGES) | {'_astro/test.css'}
    assert (base / 'subscribe/index.html').read_text() == 'old'


def test_overlay_rejects_unverified_baseline_and_hashed_asset_collisions(tmp_path, monkeypatch):
    base, build, root, identity = fixtures(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='baseline_not_verified'):
        overlay.prepare(base, build, tmp_path / 'bad', {**identity, 'build_digest': 'wrong'}, root)
    (base / '_astro').mkdir()
    (base / '_astro/test.css').write_text('different existing asset')
    identity['build_digest'] = _built_site_digest(base)
    (base / 'release.json').write_text(json.dumps(identity))
    with pytest.raises(ValueError, match='asset_url_collision'):
        overlay.prepare(base, build, tmp_path / 'bad2', identity, root)
    assert verified_snapshot(base, identity)
