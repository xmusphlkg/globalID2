"""Publication fencing after external success and a later DB finalization failure."""
import hashlib
import json

import pytest

from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Error
from src.services.data_release.surveillance_publish import publish_surveillance_data


class MemoryObjects:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def get_object(self, key):
        return self.objects.get(key)

    def put_object_conditional(self, key, body, *, previous, **_metadata):
        if self.objects.get(key) != previous:
            raise R2S3Error("Precondition failed", status=412)
        self.objects[key] = body
        self.writes.append(key)


def candidate(directory, release, cases):
    directory.mkdir()
    (directory / "objects").mkdir()
    body = json.dumps({"cases": cases}).encode()
    sha = hashlib.sha256(body).hexdigest()
    (directory / f"objects/{sha}.json").write_bytes(body)
    (directory / "current.json").write_text(json.dumps({
        "schema_version": 1, "release_id": release,
        "files": {"countries/us.json": {"url": f"objects/{sha}.json", "sha256": sha, "bytes": len(body)}},
    }))


def test_successful_pointer_with_failed_db_ack_cannot_replace_a_later_release(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    candidate(first, "published-before-db-ack-failed", 10)
    candidate(second, "later-successful-release", 20)
    store = MemoryObjects()
    config = R2PapersConfig("test-account", "test-bucket", "test-key", "test-secret")
    publish_surveillance_data(first, config, client=store)
    # The pointer upload succeeded but its caller's DB transaction failed.
    # A later task then became live before an operator retried the old task.
    publish_surveillance_data(second, config, client=store)
    pointer = "site-data/surveillance/current.json"
    latest = store.objects[pointer]
    completed_writes = list(store.writes)
    with pytest.raises(R2S3Error, match="original baseline"):
        publish_surveillance_data(first, config, client=store)
    assert store.objects[pointer] == latest
    assert store.writes == completed_writes
