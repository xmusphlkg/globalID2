import json
from pathlib import Path

import pytest

from src.services.data_release.artifact_checkpoint import ArtifactCheckpoint
from src.services.data_release.pipeline import _built_site_digest


@pytest.mark.parametrize('relative', ['shared/research-urls.ts', 'cloudflare/papers-json/src/index.ts'])
def test_url_policy_or_reader_changes_invalidate_frozen_release(tmp_path, relative):
    from src.services.data_release.artifact_checkpoint import source_fingerprint
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_text('old policy')
    before = source_fingerprint(tmp_path)
    source.write_text('new policy')
    assert source_fingerprint(tmp_path) != before


def test_partition_backup_falls_back_across_filesystems(tmp_path, monkeypatch):
    import errno
    from src.services.data_release import artifact_checkpoint

    source, destination = tmp_path / "partition", tmp_path / "backup"
    source.write_bytes(b"original")

    def cross_device(*args):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(artifact_checkpoint.os, "link", cross_device)
    artifact_checkpoint.link_immutable_or_copy(source, destination)
    source.write_bytes(b"changed")
    assert destination.read_bytes() == b"original"


def candidate(root):
    paths = [root / f"live-{index}" for index in range(5)]
    for path in paths:
        path.mkdir()
        (path / "data.json").write_text("frozen content")
    projection = paths[0] / "_release-projection"
    projection.mkdir()
    (projection / "manifest.json").write_text("{}")
    checkpoint = ArtifactCheckpoint(root, "release-1", paths, {"publish_dirty_ids": [9]})
    identity = {"release_id": "release-1", "build_digest": _built_site_digest(paths[2])}
    signature = checkpoint.signature({"destination": "production"})
    checkpoint.save_ready(signature, identity)
    assert checkpoint.retain()
    assert not any(path.exists() for path in paths)
    return checkpoint, identity, signature


def test_frozen_candidate_survives_new_instance_and_can_be_retained_again(tmp_path):
    first, identity, signature = candidate(tmp_path)
    resumed = ArtifactCheckpoint(tmp_path, "release-1", first.paths, {"publish_dirty_ids": [9]})
    for path in first.paths:
        path.mkdir()
        (path / "production.json").write_text("previous production")
    assert resumed.restore(signature, identity, _built_site_digest) == identity
    for path in first.paths:
        assert (path / "data.json").read_text() == "frozen content"
        assert not (path / "production.json").exists()
    assert resumed.retain()
    assert resumed.restore(signature, identity, _built_site_digest) == identity
    resumed.discard()
    assert not resumed.directory.exists()


@pytest.mark.parametrize("changed", ["scope", "source", "destination", "identity", "corrupt", "missing", "bad_map"])
def test_invalid_candidate_falls_back_without_touching_live_artifacts(tmp_path, changed):
    checkpoint, identity, signature = candidate(tmp_path)
    if changed in {"scope", "source", "destination"}:
        signature = {**signature, {"scope": "selection", "destination": "destinations"}.get(changed, changed): "changed"}
    elif changed == "identity":
        identity = {**identity, "release_id": "different"}
    elif changed == "corrupt":
        (checkpoint.directory / "2" / "data.json").write_text("modified")
    elif changed == "missing":
        (checkpoint.directory / "0" / "_release-projection" / "manifest.json").unlink()
    else:
        ready = checkpoint.directory / "ready.json"
        data = json.loads(ready.read_text())
        data["present"] = ["../../outside"]
        ready.write_text(json.dumps(data))
    for path in checkpoint.paths:
        path.mkdir()
        (path / "production.json").write_text("original")
    assert checkpoint.restore(signature, identity, _built_site_digest) is None
    assert not checkpoint.directory.exists()
    for path in checkpoint.paths:
        assert (path / "production.json").read_text() == "original"


@pytest.mark.parametrize("configuration", ["site", "environment"])
def test_rendering_configuration_change_invalidates_candidate(tmp_path, monkeypatch, configuration):
    checkpoint, identity, signature = candidate(tmp_path)
    if configuration == "site":
        settings = tmp_path / "data/system-settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"site": {"ga4_measurement_id": "G-CHANGED"}}))
    else:
        monkeypatch.setenv("PUBLIC_GIDS_MAINTENANCE_MESSAGE", "changed")
    changed = checkpoint.signature({"destination": "production"})
    assert changed["rendering"] != signature["rendering"]
    assert checkpoint.restore(changed, identity, _built_site_digest) is None


def test_candidate_source_tracks_authored_typescript_in_data_directory(tmp_path):
    from src.services.data_release.artifact_checkpoint import source_fingerprint
    path = tmp_path / "astro-site/src/data/changelog.ts"
    path.parent.mkdir(parents=True)
    path.write_text("export const releases = []")
    before = source_fingerprint(tmp_path)
    (path.parent / "generated.json").write_text('{"new":"snapshot"}')
    assert source_fingerprint(tmp_path) == before
    path.write_text("export const releases = ['new']")
    assert source_fingerprint(tmp_path) != before
