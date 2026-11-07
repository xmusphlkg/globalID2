import hashlib
import gzip
import json
import base64
from pathlib import Path

import pytest

from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Client, R2S3Error
from src.services.data_release.surveillance_publish import (
    capture_surveillance_baseline, publish_surveillance_data, validate_manifest,
)


CONFIG = R2PapersConfig("account", "bucket", "key", "secret")
PREFIX = "site-data/surveillance"


def generation(root, value=1, release="data-1"):
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"cases": value}).encode()
    sha = hashlib.sha256(payload).hexdigest()
    relative = f"objects/{sha}.json"
    (root / "objects").mkdir(exist_ok=True)
    (root / relative).write_bytes(payload)
    manifest = {"schema_version": 1, "release_id": release, "generated_at": "2026-11-07",
        "files": {"countries/us.json": {"url": relative, "sha256": sha, "bytes": len(payload)}}}
    (root / "current.json").write_text(json.dumps(manifest))
    return relative, payload


class Store:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.failure = None
        self.concurrent_pointer = None
        self.encodings = {}

    def get_object(self, key):
        body = self.objects.get(key)
        return gzip.decompress(body) if body is not None and self.encodings.get(key) == "gzip" else body

    def put_object_conditional(self, key, body, *, previous, **metadata):
        if key == f"{PREFIX}/current.json" and self.concurrent_pointer is not None:
            self.objects[key], self.concurrent_pointer = self.concurrent_pointer, None
        if self.objects.get(key) != previous:
            raise R2S3Error("Precondition failed", status=412)
        if self.failure and self.failure in key:
            raise R2S3Error("Network unavailable", status=503)
        self.objects[key] = body
        self.encodings[key] = metadata.get("content_encoding")
        self.writes.append((key, metadata.get("cache_control")))


def test_objects_complete_before_single_pointer_switch_and_warm_update_has_no_transfer(tmp_path):
    relative, body = generation(tmp_path)
    store = Store()
    result = publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert result["uploaded"] == 1 and result["bytes_uploaded"] == len(body)
    assert store.writes[1] == (f"{PREFIX}/{relative}", "public, max-age=31536000, immutable")
    assert store.writes[-1] == (f"{PREFIX}/current.json", "no-store")
    assert publish_surveillance_data(tmp_path, CONFIG, client=store)["uploaded"] == 0
    assert len(store.writes) == 4


def test_failure_does_not_advance_pointer_and_retry_reuses_immutable_uploads(tmp_path):
    generation(tmp_path)
    store = Store()
    publish_surveillance_data(tmp_path, CONFIG, client=store)
    previous = store.objects[f"{PREFIX}/current.json"]
    old_objects = dict(store.objects)
    generation(tmp_path, value=2, release="data-2")
    store.failure = "/current.json"
    with pytest.raises(R2S3Error):
        publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert store.objects[f"{PREFIX}/current.json"] == previous
    store.failure = None
    result = publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert result["uploaded"] == 0 and result["bytes_uploaded"] == 0
    assert json.loads(store.objects[f"{PREFIX}/current.json"])["release_id"] == "data-2"
    assert all(store.objects[key] == value for key, value in old_objects.items() if key != f"{PREFIX}/current.json")


def test_competing_publication_cannot_be_overwritten(tmp_path):
    generation(tmp_path)
    store = Store()
    publish_surveillance_data(tmp_path, CONFIG, client=store)
    other = json.loads(store.objects[f"{PREFIX}/current.json"])
    other["release_id"] = "newer-other-writer"
    newer = json.dumps(other).encode()
    generation(tmp_path, value=3, release="losing-writer")
    store.concurrent_pointer = newer
    with pytest.raises(R2S3Error, match="Precondition"):
        publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert store.objects[f"{PREFIX}/current.json"] == newer


def test_old_interrupted_candidate_cannot_adopt_a_later_baseline_on_retry(tmp_path):
    old, newer = tmp_path / "old", tmp_path / "newer"
    generation(old, release="old")
    store = Store()
    store.failure = "/current.json"
    with pytest.raises(R2S3Error):
        publish_surveillance_data(old, CONFIG, client=store)
    store.failure = None
    generation(newer, value=2, release="newer")
    publish_surveillance_data(newer, CONFIG, client=store)
    current = store.objects[f"{PREFIX}/current.json"]
    with pytest.raises(R2S3Error, match="original baseline"):
        publish_surveillance_data(old, CONFIG, client=store)
    assert store.objects[f"{PREFIX}/current.json"] == current
    # Losing a local cache must not remove the fence: the remote intent remains.
    for path in (old / ".publication").glob("*.json"):
        path.unlink()
    with pytest.raises(R2S3Error, match="remote publication baseline"):
        publish_surveillance_data(old, CONFIG, client=store)
    assert store.objects[f"{PREFIX}/current.json"] == current


def test_corrupt_or_unsafe_inputs_fail_before_upload(tmp_path):
    relative, _ = generation(tmp_path)
    store = Store()
    (tmp_path / relative).write_text("broken")
    with pytest.raises(ValueError, match="checksum"):
        publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert store.writes == []
    value = json.loads((tmp_path / "current.json").read_text())
    value["files"]["countries/us.json"]["url"] = "../private.json"
    with pytest.raises(ValueError, match="immutable"):
        validate_manifest(json.dumps(value).encode())


def test_existing_immutable_key_with_other_bytes_is_never_overwritten(tmp_path):
    relative, _ = generation(tmp_path)
    store = Store()
    store.objects[f"{PREFIX}/{relative}"] = b"corrupted"
    with pytest.raises(R2S3Error):
        publish_surveillance_data(tmp_path, CONFIG, client=store)
    assert f"{PREFIX}/current.json" not in store.objects


def test_pointer_preconditions_are_included_in_s3_signature():
    client = R2S3Client(CONFIG, opener=lambda *_args: None)
    request = client._signed_request("PUT", f"{PREFIX}/current.json", body=b"{}",
        conditional_headers={"if-match": '"previous-etag"'})
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["if-match"] == '"previous-etag"'
    assert "if-match" in headers["authorization"].split("SignedHeaders=")[1].split(",")[0]
    with pytest.raises(ValueError, match="conditional"):
        client._signed_request("PUT", "key", conditional_headers={"authorization": "override"})


def test_large_json_uses_compressed_http_bytes_without_changing_content_hash(tmp_path):
    payload = json.dumps({"cases": [123] * 10000}).encode()
    sha = hashlib.sha256(payload).hexdigest()
    (tmp_path / "objects").mkdir()
    (tmp_path / f"objects/{sha}.json").write_bytes(payload)
    (tmp_path / "current.json").write_text(json.dumps({"schema_version": 1, "release_id": "large",
        "files": {"countries/us.json": {"url": f"objects/{sha}.json", "sha256": sha, "bytes": len(payload)}}}))
    store = Store()
    result = publish_surveillance_data(tmp_path, CONFIG, client=store)
    key = f"{PREFIX}/objects/{sha}.json"
    assert store.encodings[key] == "gzip"
    assert result["bytes_uploaded"] < len(payload) // 10
    assert store.get_object(key) == payload
    assert store.encodings[f"{PREFIX}/current.json"] is None


def test_baseline_capture_requires_no_generated_files_and_reuses_original_without_get(tmp_path):
    directory, store = tmp_path / "not-yet-generated", Store()
    first = capture_surveillance_baseline(directory, CONFIG, client=store)
    assert first == {"baseline_captured": True, "reused": False, "prefix": PREFIX,
        "pointer_present": False, "baseline_sha256": None}
    path = directory / ".publication/starting-baseline.json"
    assert path.is_file() and not (directory / "current.json").exists()
    original = path.read_bytes()
    def no_get(_key):
        raise AssertionError("A captured baseline must never be refreshed")
    store.get_object = no_get
    assert capture_surveillance_baseline(directory, CONFIG, client=store)["reused"]
    assert path.read_bytes() == original and store.writes == []


def test_capture_preserves_complete_remote_pointer_bytes_and_hash(tmp_path):
    source, run, store = tmp_path / "initial", tmp_path / "run", Store()
    generation(source)
    publish_surveillance_data(source, CONFIG, client=store)
    previous = store.objects[f"{PREFIX}/current.json"]
    writes = list(store.writes)
    result = capture_surveillance_baseline(run, CONFIG, client=store)
    receipt = json.loads((run / ".publication/starting-baseline.json").read_bytes())
    assert base64.b64decode(receipt["previous_b64"]) == previous
    assert receipt["baseline_sha256"] == result["baseline_sha256"] == hashlib.sha256(previous).hexdigest()
    assert store.writes == writes


def _two_country_generation(directory, *, us, fr, release):
    generation(directory, us, release)
    payload = json.dumps({"cases": fr}).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"objects/{digest}.json"
    (directory / relative).write_bytes(payload)
    manifest = json.loads((directory / "current.json").read_bytes())
    manifest["files"]["countries/fr.json"] = {"url": relative, "sha256": digest, "bytes": len(payload)}
    (directory / "current.json").write_text(json.dumps(manifest))


def test_first_publish_of_frozen_a_cannot_undo_newer_disjoint_country_b(tmp_path):
    initial, a, b, store = tmp_path / "initial", tmp_path / "a", tmp_path / "b", Store()
    _two_country_generation(initial, us=1, fr=1, release="initial")
    publish_surveillance_data(initial, CONFIG, client=store)
    # Both read the same initial published snapshot; A changes only the US,
    # B only France. There is no overlapping dirty-ID ownership to catch A.
    capture_surveillance_baseline(a, CONFIG, client=store)
    _two_country_generation(a, us=2, fr=1, release="a-frozen-before-b")
    capture_surveillance_baseline(b, CONFIG, client=store)
    _two_country_generation(b, us=1, fr=2, release="b-first-published")
    publish_surveillance_data(b, CONFIG, client=store, require_starting_baseline=True)
    newer, writes = store.objects[f"{PREFIX}/current.json"], list(store.writes)
    with pytest.raises(R2S3Error, match="starting baseline") as error:
        publish_surveillance_data(a, CONFIG, client=store, require_starting_baseline=True)
    assert error.value.status == 412
    assert store.objects[f"{PREFIX}/current.json"] == newer
    assert store.writes == writes
    assert list((a / ".publication").glob("*.json")) == [a / ".publication/starting-baseline.json"]


def test_empty_captured_baseline_also_fences_later_first_publication(tmp_path):
    a, b, store = tmp_path / "a", tmp_path / "b", Store()
    capture_surveillance_baseline(a, CONFIG, client=store)
    generation(a, release="old-empty-snapshot")
    generation(b, 2, release="first-published")
    publish_surveillance_data(b, CONFIG, client=store)
    with pytest.raises(R2S3Error, match="starting baseline"):
        publish_surveillance_data(a, CONFIG, client=store)
    assert json.loads(store.objects[f"{PREFIX}/current.json"])["release_id"] == "first-published"


def test_captured_publication_can_retry_after_ambiguous_success_without_recapturing(tmp_path):
    store = Store()
    capture_surveillance_baseline(tmp_path, CONFIG, client=store)
    generation(tmp_path, release="candidate")
    publish_surveillance_data(tmp_path, CONFIG, client=store, require_starting_baseline=True)
    writes = list(store.writes)
    result = publish_surveillance_data(tmp_path, CONFIG, client=store, require_starting_baseline=True)
    assert result["pointer_changed"] is False
    assert result["uploaded"] == 0 and store.writes == writes
    assert json.loads((tmp_path / ".publication/starting-baseline.json").read_text())["previous_b64"] is None


@pytest.mark.parametrize("problem", ["invalid_json", "schema", "checksum", "base64", "destination", "missing_field"])
def test_damaged_starting_baseline_fails_closed_before_even_idempotent_publish(tmp_path, problem):
    store = Store()
    capture_surveillance_baseline(tmp_path, CONFIG, client=store)
    generation(tmp_path)
    store.objects[f"{PREFIX}/current.json"] = (tmp_path / "current.json").read_bytes()
    path = tmp_path / ".publication/starting-baseline.json"
    receipt = json.loads(path.read_text())
    if problem == "invalid_json": path.write_text("broken")
    else:
        if problem == "schema": receipt["schema"] = True
        if problem == "checksum": receipt["baseline_sha256"] = "a" * 64
        if problem == "base64": receipt["previous_b64"] = "***"
        if problem == "destination": receipt["prefix"] = "site-data/other"
        if problem == "missing_field": del receipt["previous_b64"]
        path.write_text(json.dumps(receipt))
    for operation in (capture_surveillance_baseline, publish_surveillance_data):
        with pytest.raises(ValueError, match="starting baseline"):
            operation(tmp_path, CONFIG, client=store)
    assert store.writes == []


def test_baseline_cannot_be_reused_for_another_prefix_or_bucket(tmp_path):
    store = Store()
    capture_surveillance_baseline(tmp_path, CONFIG, client=store)
    generation(tmp_path)
    for config, prefix in ((CONFIG, "site-data/situation"),
            (R2PapersConfig("account", "different-bucket", "key", "secret"), PREFIX)):
        with pytest.raises(ValueError, match="another destination"):
            capture_surveillance_baseline(tmp_path, config, prefix=prefix, client=store)
        with pytest.raises(ValueError, match="another destination"):
            publish_surveillance_data(tmp_path, config, prefix=prefix, client=store)
    assert store.writes == []


def test_managed_candidate_without_pre_generation_baseline_is_rejected(tmp_path):
    generation(tmp_path)
    store = Store()
    with pytest.raises(ValueError, match="captured before generation"):
        publish_surveillance_data(tmp_path, CONFIG, client=store, require_starting_baseline=True)
    assert store.writes == []


@pytest.mark.parametrize("status", [403, 503])
def test_remote_get_error_cannot_be_captured_as_a_missing_pointer(tmp_path, status):
    store = Store()
    def failed_get(_key): raise R2S3Error("GET failed", status=status)
    store.get_object = failed_get
    with pytest.raises(R2S3Error): capture_surveillance_baseline(tmp_path, CONFIG, client=store)
    assert not (tmp_path / ".publication/starting-baseline.json").exists()
    assert store.writes == []


def test_invalid_remote_pointer_is_never_captured(tmp_path):
    store = Store()
    store.objects[f"{PREFIX}/current.json"] = b"not a manifest"
    with pytest.raises(ValueError): capture_surveillance_baseline(tmp_path, CONFIG, client=store)
    assert not (tmp_path / ".publication/starting-baseline.json").exists()


def test_remote_intent_still_fences_candidate_after_local_baseline_is_lost(tmp_path):
    a, b, store = tmp_path / "a", tmp_path / "b", Store()
    capture_surveillance_baseline(a, CONFIG, client=store)
    generation(a, release="a")
    store.failure = "/current.json"
    with pytest.raises(R2S3Error): publish_surveillance_data(a, CONFIG, client=store)
    store.failure = None
    generation(b, 2, release="b")
    publish_surveillance_data(b, CONFIG, client=store)
    for path in (a / ".publication").glob("*.json"): path.unlink()
    # An accidentally recaptured local baseline must not defeat the original
    # immutable remote intent's older baseline.
    capture_surveillance_baseline(a, CONFIG, client=store)
    with pytest.raises(R2S3Error, match="remote publication baseline"):
        publish_surveillance_data(a, CONFIG, client=store)
    assert json.loads(store.objects[f"{PREFIX}/current.json"])["release_id"] == "b"


def test_capture_cli_routes_before_generation_and_managed_cli_requires_baseline(tmp_path, monkeypatch, capsys):
    from scripts import publish_surveillance_data as cli
    calls = []
    monkeypatch.setattr(cli, "_config_from_env", lambda: CONFIG)
    def capture(directory, config, **kwargs):
        calls.append(("capture", directory, config, kwargs))
        return {"baseline_captured": True}
    def publish(directory, config, **kwargs):
        calls.append(("publish", directory, config, kwargs))
        return {"pointer_changed": True}
    monkeypatch.setattr(cli, "capture_surveillance_baseline", capture)
    monkeypatch.setattr(cli, "publish_surveillance_data", publish)
    cli.main(["--source-dir", str(tmp_path), "--prefix", "site-data/situation", "--capture-baseline-only"])
    assert calls[0] == ("capture", tmp_path, CONFIG, {"prefix": "site-data/situation"})
    assert json.loads(capsys.readouterr().out)["baseline_captured"]
    cli.main(["--source-dir", str(tmp_path), "--require-starting-baseline"])
    assert calls[1][0] == "publish" and calls[1][3]["require_starting_baseline"] is True
