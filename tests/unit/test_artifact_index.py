import hashlib
import os
import time
from pathlib import Path

import pytest

from src.services.data_release.artifact_index import ArtifactIndex


def test_write_receipt_survives_restart_without_reading_body(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    path = root / "page.html"
    index = ArtifactIndex(root, cache_dir=tmp_path / "cache")
    index.write(path, b"page")
    time.sleep(1.01)  # leave the racy-stat window, then capture a stable receipt
    index.describe(path)
    index.save()

    def no_read(_path):
        pytest.fail("Unchanged artifact was read again")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    restarted = ArtifactIndex(root, cache_dir=tmp_path / "cache")
    assert restarted.describe(path)["sha256"] == hashlib.sha256(b"page").hexdigest()
    assert restarted.read_files == 0


def test_same_size_edit_with_restored_mtime_invalidates_receipt(tmp_path):
    path = tmp_path / "page.html"
    index = ArtifactIndex(tmp_path, cache_dir=tmp_path / "cache")
    index.write(path, b"page")
    before = path.stat()
    path.write_bytes(b"EDIT")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert index.cached(path) is None
    assert index.describe(path)["sha256"] == hashlib.sha256(b"EDIT").hexdigest()
    assert index.read_files == 1


@pytest.mark.parametrize("payload", ["broken", "[]", '{"schema":1,"files":[]}'])
def test_damaged_cache_falls_back_to_content(tmp_path, payload):
    path = tmp_path / "page.html"
    path.write_bytes(b"page")
    index = ArtifactIndex(tmp_path, cache_dir=tmp_path / "cache")
    index.path.parent.mkdir()
    index.path.write_text(payload)
    recovered = ArtifactIndex(tmp_path, cache_dir=tmp_path / "cache")
    assert recovered.describe(path)["bytes"] == 4
    assert recovered.read_files == 1


def test_wire_receipt_is_bound_to_source_and_codec_across_restarts(tmp_path):
    path = tmp_path / "page.html"
    path.write_bytes(b"page")
    time.sleep(1.01)
    index = ArtifactIndex(tmp_path, cache_dir=tmp_path / "cache")
    _, source = index.read(path)
    index.record_representation(path, "gzip-v1", source, b"encoded", encoding="gzip")
    index.save()
    restarted = ArtifactIndex(tmp_path, cache_dir=tmp_path / "cache")
    assert restarted.cached_representation(path, "gzip-v1")["md5"] == hashlib.md5(b"encoded").hexdigest()
    assert restarted.cached_representation(path, "gzip-v2") is None
    before = path.stat()
    path.write_bytes(b"EDIT")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert restarted.cached_representation(path, "gzip-v1") is None
    # Recording an old compression result after a concurrent edit cannot make
    # the receipt usable for the newly observed source bytes.
    restarted.read(path)
    restarted.record_representation(path, "gzip-v1", source, b"encoded", encoding="gzip")
    assert "representation" not in restarted.entries[restarted.key(path)]
