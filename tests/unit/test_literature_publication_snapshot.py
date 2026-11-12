import hashlib
import json

import pytest

from unittest.mock import AsyncMock

from src.generation import literature_publication
from src.generation.literature_publication import freeze_literature_json, select_index_artifacts
from src.services.data_release.papers_r2 import R2S3Error
from src.services.data_release.surveillance_publish import (
    capture_surveillance_baseline, publish_surveillance_data, validate_manifest,
)
from tests.unit.test_surveillance_publish import CONFIG, Store


def test_snapshot_pins_every_file_and_reuses_immutable_objects(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    (source / "shards").mkdir(parents=True)
    (source / "index.json").write_text('{"count":1}')
    (source / "shards" / "one.json").write_text('[{"id":1}]')
    first = freeze_literature_json(source, output, release_id="one", generated_at="2026-11-11T00:00:00Z")
    for name, file in first["files"].items():
        body = (output / file["url"]).read_bytes()
        assert body == (source / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == file["sha256"]
    assert validate_manifest((output / "current.json").read_bytes())[0] == first
    (source / "index.json").write_text('{"count":2}')
    second = freeze_literature_json(source, output, release_id="two", generated_at="2026-11-11T06:00:00Z")
    assert first["files"]["shards/one.json"] == second["files"]["shards/one.json"]
    assert (output / first["files"]["index.json"]["url"]).exists()


def test_explicit_selection_excludes_old_shards_but_requires_referenced_ones(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    (source / "shards").mkdir(parents=True)
    (source / "index.json").write_text('{"articles":{"file":"shards/active.json"}}')
    (source / "catalogue.json").write_text('{}')
    (source / "shards" / "active.json").write_text('{}')
    (source / "shards" / "old.json").write_text('{}')
    with pytest.raises(ValueError, match="missing 1 referenced"):
        freeze_literature_json(source, output, release_id="one", generated_at="t", logical_names=["index.json"])
    snapshot = freeze_literature_json(source, output, release_id="one", generated_at="t",
        logical_names=["index.json", "shards/active.json"])
    assert set(snapshot["files"]) == {"index.json", "shards/active.json"}
    assert select_index_artifacts(source) == ["catalogue.json", "index.json", "shards/active.json"]


def test_content_named_shards_use_writer_receipts_without_rereading(tmp_path, monkeypatch):
    source, output = tmp_path / 'source', tmp_path / 'output'
    (source / 'shards').mkdir(parents=True)
    payload = b'{"records":[1]}'
    digest = hashlib.sha256(payload).hexdigest()
    name = f'articles-{digest}.json'
    (source / 'shards' / name).write_bytes(payload)
    (source / 'index.json').write_text(json.dumps({'articles': {'file': f'shards/{name}'}}))
    (source / 'catalogue.json').write_text('{}')
    original = literature_publication.ArtifactIndex.describe
    reads = []
    def describe(self, path):
        reads.append(path.name)
        return original(self, path)
    monkeypatch.setattr(literature_publication.ArtifactIndex, 'describe', describe)
    result = freeze_literature_json(source, output, release_id='one', generated_at='t',
                                    logical_names=select_index_artifacts(source))
    assert name not in reads
    assert result['files'][f'shards/{name}']['sha256'] == digest


@pytest.mark.parametrize("failure", ["missing_index", "missing_shard", "unsafe_reference", "invalid_index", "invalid_json", "unsafe_name", "symlink"])
def test_invalid_candidate_does_not_switch_current_pointer(tmp_path, failure):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    index = source / "index.json"
    index.write_text('{}')
    initial = freeze_literature_json(source, output, release_id="one", generated_at="t")
    old_pointer = (output / "current.json").read_bytes()
    if failure == "missing_index":
        index.unlink()
    elif failure == "missing_shard":
        index.write_text('{"articles":{"file":"shards/missing.json"}}')
    elif failure == "unsafe_reference":
        index.write_text('{"articles":{"file":"shards/../index.json"}}')
    elif failure == "invalid_index":
        index.write_text('[]')
    elif failure == "invalid_json":
        index.write_text('{')
    elif failure == "unsafe_name":
        (source / "bad name.json").write_text('{}')
    elif failure == "symlink":
        (source / "link.json").symlink_to(index)
    with pytest.raises(ValueError):
        freeze_literature_json(source, output, release_id="two", generated_at="t")
    assert (output / "current.json").read_bytes() == old_pointer


async def test_export_uses_supplied_snapshot_and_private_directories(tmp_path, monkeypatch):
    collect = AsyncMock(return_value={"articles": [{"article_id": "a"}]})
    monkeypatch.setattr(literature_publication, "collect_literature_export", collect)
    calls = []
    monkeypatch.setattr(literature_publication, "attach_surveillance_evidence",
        lambda payload, situation, *, diseases_by_id, related_cache_path: {**payload, "snapshot": situation})
    monkeypatch.setattr(literature_publication, "write_literature_artifacts",
        lambda payload, build, public, *, article_digests: calls.append((payload, build, public)))
    session, private = object(), tmp_path / "candidate"
    payload = await literature_publication.export_literature_snapshot(
        session, diseases_by_id={"D1": {}}, surveillance_coverage={"D1": {"US"}},
        situation_snapshot={"snapshot_id": "s1"}, private_dir=private, limit=100,
    )
    assert payload["snapshot"] == {"snapshot_id": "s1"}
    collect.assert_awaited_once_with(session, diseases_by_id={"D1": {}},
        surveillance_coverage={"D1": {"US"}}, limit=100, title_cache_path=None)
    assert calls[0][1:] == (private / "build", private / "public")
    (private / "existing").touch()
    with pytest.raises(ValueError, match="empty private"):
        await literature_publication.export_literature_snapshot(
            session, diseases_by_id={}, surveillance_coverage={}, situation_snapshot={},
            private_dir=private, limit=100,
        )


async def test_standalone_export_requires_owned_database_snapshot(tmp_path, monkeypatch):
    from scripts import export_research_snapshot
    from src.core.database_snapshot import SNAPSHOT_ENV

    context = tmp_path / "context.json"
    context.write_text('{"diseases":{},"surveillance_coverage":{},"situation":{}}')
    monkeypatch.delenv(SNAPSHOT_ENV, raising=False)
    with pytest.raises(ValueError, match="snapshot identifier"):
        await export_research_snapshot.run(context, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_publication_fences_stale_candidate_and_keeps_immutable_index(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.json").write_text('{"version":1}')
    older, newer = tmp_path / "older", tmp_path / "newer"
    store = Store()
    prefix = "site-data/research-publication"
    capture_surveillance_baseline(older, CONFIG, prefix=prefix, client=store)
    first = freeze_literature_json(source, older, release_id="older", generated_at="t")
    (source / "index.json").write_text('{"version":2}')
    capture_surveillance_baseline(newer, CONFIG, prefix=prefix, client=store)
    freeze_literature_json(source, newer, release_id="newer", generated_at="t")
    publish_surveillance_data(newer, CONFIG, prefix=prefix, client=store, require_starting_baseline=True)
    current = store.objects[f"{prefix}/current.json"]
    with pytest.raises(R2S3Error, match="changed since"):
        publish_surveillance_data(older, CONFIG, prefix=prefix, client=store, require_starting_baseline=True)
    assert store.objects[f"{prefix}/current.json"] == current
    assert first["release_id"] == "older"
