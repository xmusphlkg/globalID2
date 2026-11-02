import copy
import hashlib
import json
import os
import time

import pytest

from src.generation import research_shards as shards


def restore(value, root):
    if not isinstance(value, dict) or not value.get("sharded"):
        return value
    if value["kind"] == "list":
        return [record for chunk in value["chunks"] for record in json.loads((root / chunk["file"]).read_text())["records"]]
    if value["kind"] == "value":
        return json.loads((root / value["file"]).read_text())["value"]
    return {key: restore(child, root) for key, child in value["parts"].items()}


@pytest.mark.parametrize("operation", ["insert", "remove", "edit"])
def test_small_change_reuses_most_shards_and_preserves_exact_order(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(shards, "SHARD_TARGET_BYTES", 2048)
    articles = [{"article_id": str(i), "title": "x" * 250} for i in range(400)]
    document = {"schema_version": 1, "articles": articles}
    first = shards.publish_browser_research_index(document, tmp_path, max_bytes=16384)
    original = copy.deepcopy(document)
    before = shards._referenced_shards(first)
    changed = copy.deepcopy(document)
    if operation == "insert":
        changed["articles"].insert(120, {"article_id": "added", "title": "y" * 250})
    elif operation == "remove":
        changed["articles"].pop(120)
    else:
        changed["articles"][120]["title"] = "y" * 250
    second = shards.publish_browser_research_index(changed, tmp_path, max_bytes=16384)
    after = shards._referenced_shards(second)
    assert len(before & after) >= len(before) - 4
    assert len(after - before) <= 4
    assert restore(second["articles"], tmp_path) == changed["articles"]
    assert restore(first["articles"], tmp_path) == articles
    assert document == original
    for name in after:
        body = (tmp_path / "shards" / name).read_bytes()
        assert len(body) <= 16384
        assert name.endswith(hashlib.sha256(body).hexdigest() + ".json")


def test_retirement_starts_at_last_reference_even_for_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(shards, "SHARD_TARGET_BYTES", 2048)
    now = time.time()
    document = {"articles": [{"article_id": str(i), "title": "x" * 250} for i in range(400)]}
    first = shards.publish_browser_research_index(document, tmp_path, max_bytes=16384, now=now)
    old = shards._referenced_shards(first)
    for name in old:
        os.utime(tmp_path / "shards" / name, (0, 0))
    shards.publish_browser_research_index({"articles": []}, tmp_path, max_bytes=16384, now=now + 1)
    assert all((tmp_path / "shards" / name).exists() for name in old)
    shards.publish_browser_research_index({"articles": []}, tmp_path, max_bytes=16384, now=now + shards.SHARD_RETENTION_SECONDS)
    assert all((tmp_path / "shards" / name).exists() for name in old)
    shards.publish_browser_research_index({"articles": []}, tmp_path, max_bytes=16384, now=now + shards.SHARD_RETENTION_SECONDS + 2)
    assert not list((tmp_path / "shards").glob("*.json"))


def test_oversized_new_export_preserves_previous_manifest(tmp_path):
    shards.publish_browser_research_index({"articles": []}, tmp_path)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(RuntimeError, match="exceed"):
        shards.publish_browser_research_index({"articles": [{"title": "x" * 4000}]}, tmp_path, max_bytes=1024)
    assert (tmp_path / "index.json").read_bytes() == before
