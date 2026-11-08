import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from src.services.data_release.artifact_index import ArtifactIndex, _signature
from src.services.data_release.build_fingerprints import BuildFingerprints
from src.services.data_release.bundle_html_workers import rewrite_changed_pages
from src.services.data_release.papers_bundle import rewrite_papers_html, split_papers_bundle
from src.services.data_release.reports_bundle import split_reports_bundle


def test_node_receipt_reuses_only_matching_strong_identity(tmp_path):
    page = tmp_path / 'page.html'
    page.write_bytes(b'page')
    time.sleep(1.01)  # A real Node receipt becomes stable only after this window.
    cache = tmp_path / 'node.json'
    digest = hashlib.sha256(b'page').hexdigest()
    cache.write_text(json.dumps({'schema': 1, 'files': {str(page): {
        'stat': ':'.join(map(str, _signature(page))), 'sha256': digest, 'stable': True,
    }}}))
    index = ArtifactIndex(tmp_path, cache_dir=tmp_path / 'cache')
    reader = BuildFingerprints(index, cache)
    assert reader.sha256(page) == digest and index.read_files == 0
    before = page.stat()
    page.write_bytes(b'EDIT')
    os.utime(page, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert reader.sha256(page) == hashlib.sha256(b'EDIT').hexdigest()
    assert index.read_files == 1
    with pytest.raises(ValueError, match='changed during partitioning'):
        reader.read(page, digest)
    cache.write_text('broken')
    assert BuildFingerprints(index, cache).sha256(page) == hashlib.sha256(b'EDIT').hexdigest()


@pytest.mark.parametrize('kind,relative', [('papers', 'research/cards/test.png'), ('reports', 'site-data/reports/test.json')])
def test_partitions_preserve_identical_non_html_files(tmp_path, kind, relative, monkeypatch):
    if kind == 'papers':
        monkeypatch.setattr('src.services.data_release.papers_bundle.research_social_cards_enabled', lambda: True)
    source, target = tmp_path / 'dist', tmp_path / 'bundle'
    path = source / relative
    path.parent.mkdir(parents=True)
    def split():
        if kind == 'papers': split_papers_bundle(source, papers_dir=target)
        else: split_reports_bundle(source, reports_dir=target)
    path.write_bytes(b'original')
    split()
    old = (target / relative).stat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'original')
    split()
    current = (target / relative).stat()
    assert (current.st_ino, current.st_ctime_ns) == (old.st_ino, old.st_ctime_ns)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'changed!')
    split()
    assert (target / relative).read_bytes() == b'changed!'


def test_parallel_html_rewrite_matches_serial_and_propagates_failure(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir(); target.mkdir()
    body = b'<a href="/countries/ca/">Canada</a>'
    pending = []
    for i in range(32):
        path = source / f'{i}.html'
        path.write_bytes(body)
        pending.append((path, target / path.name, hashlib.sha256(body).hexdigest()))
    index = ArtifactIndex(target, cache_dir=tmp_path / 'cache')
    rewrite_changed_pages(pending, index, kind='papers', origins={}, workers=2)
    assert all(not path.exists() and output.read_text() == rewrite_papers_html(body.decode()) for path, output, _ in pending)
    for path, _output, _digest in pending: path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed during partitioning'):
        rewrite_changed_pages(pending, index, kind='papers', origins={}, workers=2)
    assert not index.path.exists()
