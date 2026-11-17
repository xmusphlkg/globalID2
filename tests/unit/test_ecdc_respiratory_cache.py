"""Shared ECDC cache conserves API quota without disguising stale discovery."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.ecdc_respiratory import DATASETS, REPOSITORY, TREE_URL
from src.services.ingestion.countries import ecdc_respiratory as connector

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/EU_HIGH_FREQUENCY"
MOMENT = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)


def documents():
    responses, entries = {}, []
    for path in ["README.MD", *DATASETS]:
        payload = (FIXTURES / Path(path).name).read_bytes()
        sha = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        entries.append({"path": path, "type": "blob", "sha": sha, "size": len(payload)})
        url = f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{sha}"
        responses[url] = json.dumps({"encoding": "base64", "sha": sha, "content": base64.b64encode(payload).decode(), "size": len(payload)}).encode()
    responses[TREE_URL] = json.dumps({"sha": "official-tree", "truncated": False, "tree": entries}).encode()
    return responses


class Crawler:
    def __init__(self, responses):
        self.responses = responses; self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url not in self.responses:
            raise RuntimeError("upstream unavailable")
        return SimpleNamespace(content=self.responses[url])


def test_fresh_tree_and_immutable_blobs_keep_original_capture_time_and_full_bytes(tmp_path):
    crawler = Crawler(documents())
    first = connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT)
    second = connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT + timedelta(minutes=10))
    assert len(crawler.calls) == 5
    assert first == second and len(second) == 5
    assert all(item[3]["retrieved_at"] == MOMENT.isoformat() for item in second)
    # Refresh discovery, but reuse immutable content and its original timestamp.
    third = connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT + timedelta(minutes=16))
    assert len(crawler.calls) == 6 and crawler.calls[-1] == TREE_URL
    assert third[0][3]["retrieved_at"] != first[0][3]["retrieved_at"]
    assert third[1:] == first[1:]


def test_expired_tree_network_failure_cannot_claim_a_fresh_capture(tmp_path):
    crawler = Crawler(documents())
    connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT)
    del crawler.responses[TREE_URL]
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT + timedelta(seconds=connector.TREE_TTL_SECONDS))
    assert len(crawler.calls) == 6 and crawler.calls[-1] == TREE_URL
    receipt = json.loads((tmp_path / "tree.json").read_bytes())
    assert receipt["retrieved_at"] == MOMENT.isoformat()


def test_corrupt_blob_cannot_pass_even_with_a_recomputed_cache_checksum(tmp_path):
    crawler = Crawler(documents())
    first = connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT)
    sha = first[1][3]["git_blob_sha1"]
    payload = b"corrupted source bytes"
    (tmp_path / (sha + ".bin")).write_bytes(payload)
    receipt = json.loads((tmp_path / (sha + ".json")).read_bytes())
    receipt.update(sha256=hashlib.sha256(payload).hexdigest(), byte_size=len(payload))
    (tmp_path / (sha + ".json")).write_text(json.dumps(receipt))
    repaired = connector.cached_capture(crawler, cache_directory=tmp_path, now=MOMENT + timedelta(seconds=1))
    assert len(crawler.calls) == 6 and crawler.calls[-1].endswith(sha)
    assert repaired[1][0] == first[1][0]
    assert repaired[1][3]["retrieved_at"] == (MOMENT + timedelta(seconds=1)).isoformat()


def _process_capture(directory, responses, calls, start, output):
    try:
        start.wait(10)

        def get(url):
            with calls.get_lock():
                calls.value += 1
            return SimpleNamespace(content=responses[url])

        captured = connector.cached_capture(SimpleNamespace(get=get), cache_directory=directory, now=MOMENT)
        output.put({"artifacts": len(captured), "timestamps": [r[3]["retrieved_at"] for r in captured]})
    except Exception as error:
        output.put({"error": repr(error)})


def test_four_processes_share_one_five_request_capture(tmp_path):
    context = multiprocessing.get_context("spawn")
    calls = context.Value("i", 0); start = context.Event(); output = context.Queue()
    processes = [context.Process(target=_process_capture, args=(str(tmp_path), documents(), calls, start, output)) for _ in range(4)]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [output.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert calls.value == 5
        assert all(result == {"artifacts": 5, "timestamps": [MOMENT.isoformat()] * 5} for result in results)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate(); process.join(timeout=2)
