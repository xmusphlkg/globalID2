import pytest

from src.services.data_release.papers_reuse import read_committed_papers_receipt, write_committed_papers_receipt
from tests.unit.test_papers_content import make_candidate


def test_receipt_only_reuses_the_bound_committed_generation_and_destination(tmp_path):
    candidate = make_candidate(tmp_path / "papers", {"research/articles/a/index.html": "A"})
    pointer = {"release_id": candidate.release_id, "papers": {
        "papers_generation_sha256": candidate.generation, "papers_origin": candidate.origin}}
    path = tmp_path / "receipt.json"
    assert read_committed_papers_receipt(path, pointer=pointer, target="bucket-a") == set()
    write_committed_papers_receipt(path, candidate=candidate, pointer=pointer, target="bucket-a")
    assert read_committed_papers_receipt(path, pointer=pointer, target="bucket-a") == {candidate.entries[0]["key"]}
    assert read_committed_papers_receipt(path, pointer=pointer, target="bucket-b") == set()
    changed = {**pointer, "release_id": "other"}
    assert read_committed_papers_receipt(path, pointer=changed, target="bucket-a") == set()
    with pytest.raises(ValueError, match="not the committed"):
        write_committed_papers_receipt(path, candidate=candidate, pointer=changed, target="bucket-a")
