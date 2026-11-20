import copy
import json

import pytest

from scripts.migrate_download_cells import migrate, activate
from src.generation.direct_download_files import build_direct_download_files
from tests.unit.test_direct_download_files import _context


BASE = 'https://raw.githubusercontent.com/owner/repo/main'


def test_migration_preserves_rows_resumes_and_supports_incremental_update(tmp_path):
    source, target = tmp_path / 'old', tmp_path / 'new'
    context = _context()
    old = build_direct_download_files(context, source, download_url_base=BASE, layout='legacy', workers=1)
    original = [row for part in old['countries'][0]['parts']
                for row in json.loads((source / part['files']['json']['relative_path']).read_text())['records']]
    migrated = migrate(source, target, workers=1)
    records = [row for part in migrated['countries'][0]['parts']
               for row in json.loads((target / part['files']['json']['relative_path']).read_text())['records']]
    assert records == original
    assert migrated['countries'][0]['parts'][0]['files'] == migrated['diseases'][0]['parts'][0]['files']
    paths = list((target / 'cells').rglob('*.*'))
    mtimes = {p: p.stat().st_mtime_ns for p in paths}
    assert migrate(source, target, workers=1) == migrated
    assert {p: p.stat().st_mtime_ns for p in paths} == mtimes
    backup = activate(source, target)
    assert json.loads((backup / 'manifest.json').read_text())['schema_version'] == 4
    assert (backup / 'countries').is_dir()
    assert not (source / 'countries').exists()
    changed = copy.deepcopy(context)
    changed['country_exports'][0]['country_data']['disease_series']['D007']['cases'][-1] = 99
    result = build_direct_download_files(changed, source, download_url_base=BASE, workers=1,
        changed_country_codes={'CN'}, changed_disease_ids={'D007'})
    assert result['schema_version'] == 5
    assert result['generation']['changed_files'] == 3


def test_activation_refuses_changed_baseline(tmp_path):
    source, target = tmp_path / 'old', tmp_path / 'new'
    build_direct_download_files(_context(), source, download_url_base=BASE, layout='legacy', workers=1)
    migrate(source, target, workers=1)
    with (source / 'manifest.json').open('a') as handle:
        handle.write(' ')
    with pytest.raises(ValueError, match='baseline changed'):
        activate(source, target)
    assert (source / 'countries').is_dir()


def test_corrupt_source_never_replaces_original_tree(tmp_path):
    source, target = tmp_path / 'old', tmp_path / 'new'
    old = build_direct_download_files(_context(), source, download_url_base=BASE, layout='legacy', workers=1)
    path = source / old['countries'][0]['parts'][0]['files']['json']['relative_path']
    path.write_bytes(b'x' * path.stat().st_size)
    with pytest.raises(ValueError, match='checksum mismatch'):
        migrate(source, target, workers=1)
    assert json.loads((source / 'manifest.json').read_text())['schema_version'] == 4


def test_streaming_upload_preloads_cells_then_publishes_final_tree(tmp_path, monkeypatch):
    import subprocess
    from scripts.stream_download_cells import stream
    from scripts.publish_download_repo import run_git, commit_and_push

    source, candidate, remote = tmp_path / 'source', tmp_path / 'candidate', tmp_path / 'remote.git'
    build_direct_download_files(_context(dates=['2026-01-01']), source,
        download_url_base=BASE, layout='legacy', workers=1)
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    for command in [['init', '-b', 'main'], ['config', 'user.name', 'Fixture'],
                    ['config', 'user.email', 'fixture@example.invalid'], ['remote', 'add', 'origin', str(remote)],
                    ['add', '.'], ['commit', '-m', 'Baseline'], ['push', 'origin', 'main']]:
        run_git(command, source)
    baseline = run_git(['rev-parse', 'HEAD'], source)
    migrate(source, candidate, workers=1)
    activate(source, candidate)
    stream(source, candidate, batch_bytes=10_000)
    assert run_git(['rev-parse', 'main'], remote) == baseline
    preload = run_git(['for-each-ref', '--format=%(refname:short)', 'refs/heads/globalid-upload'], remote)
    assert 'cells/' in run_git(['ls-tree', '-r', '--name-only', preload], remote)
    monkeypatch.setenv('GIDS_DOWNLOAD_PRELOAD_BRANCH', preload)
    commit_and_push(source, 'main', 'Publish cells', push=True)
    assert json.loads(run_git(['show', 'main:manifest.json'], remote))['schema_version'] == 5
    assert run_git(['ls-tree', '-r', '--name-only', 'main', '--', 'countries', 'diseases'], remote) == ''
    assert run_git(['for-each-ref', '--format=%(refname)', 'refs/heads/globalid-upload'], remote) == ''
