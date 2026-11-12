import json
import gzip
import pytest

from src.services.data_release.papers_bundle import build_papers_manifest
from src.services.data_release.papers_content import prepare_papers_content, prepare_papers_content_from_manifest, publish_papers_content
from src.services.data_release.papers_immutable import prepare_immutable_papers
from src.services.data_release.papers_r2 import R2S3Error
from tests.unit.test_surveillance_publish import Store


def make_candidate(root, contents):
    root.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manifest = build_papers_manifest(root, papers_origin="https://papers.example")
    return prepare_papers_content(prepare_immutable_papers(
        papers_dir=root, manifest=manifest, papers_origin="https://papers.example"), release_id="candidate")


def test_content_keys_reuse_unchanged_html_across_generations(tmp_path):
    first = make_candidate(tmp_path / "first", {"research/articles/a/index.html": "A", "research/articles/b/index.html": "B"})
    second = make_candidate(tmp_path / "second", {"research/articles/a/index.html": "A", "research/articles/b/index.html": "new B"})
    class AuditStore(Store):
        def __init__(self):
            super().__init__()
            self.read_keys = []
        def get_object(self, key):
            self.read_keys.append(key)
            return super().get_object(key)
    store = AuditStore()
    result1 = publish_papers_content(first, client=store)
    previous = {entry['key'] for entry in first.entries}
    result2 = publish_papers_content(second, client=store, previous_objects=previous)
    assert result1['uploaded_objects'] == 2
    assert result2['uploaded_objects'] == 1 and result2['reused_objects'] == 1
    assert store.read_keys == []
    assert result1['generation_sha256'] != result2['generation_sha256']
    assert store.writes[-1][0] == second.manifest_key
    assert json.loads(store.get_object(second.manifest_key))["file_count"] == 2


def test_failed_upload_never_commits_catalogue(tmp_path):
    candidate = make_candidate(tmp_path / "papers", {"research/articles/a/index.html": "A"})
    store = Store()
    store.failure = '/objects/'
    with pytest.raises(R2S3Error):
        publish_papers_content(candidate, client=store)
    assert candidate.manifest_key not in store.objects


def test_preparing_from_build_receipts_does_not_read_files(tmp_path):
    root = tmp_path / "papers"
    root.mkdir()
    path = root / "article.html"
    path.write_text("article")
    manifest = build_papers_manifest(root, papers_origin="https://papers.example")
    path.unlink()
    result = prepare_papers_content_from_manifest(root, manifest, papers_origin="https://papers.example", release_id="one")
    assert len(result.entries) == 1


def test_publishing_rejects_changed_sources(tmp_path):
    candidate = make_candidate(tmp_path / 'papers', {'research/articles/a/index.html': 'original'})
    (tmp_path / 'papers/research/articles/a/index.html').write_text('changed')
    with pytest.raises(ValueError, match='source changed'):
        publish_papers_content(candidate, client=Store())


@pytest.mark.parametrize('name', ['article.html', 'data.json', 'app.js', 'style.css', 'icon.svg'])
def test_text_uploads_compress_without_changing_catalogue_or_decoded_content(tmp_path, name):
    content = 'Repeated publication content. ' * 400
    candidate = make_candidate(tmp_path / 'papers', {name: content})
    store = Store()
    result = publish_papers_content(candidate, client=store)
    entry = candidate.entries[0]
    wire = store.objects[entry['key']]
    assert store.encodings[entry['key']] == 'gzip'
    assert len(wire) < entry['bytes'] // 10
    assert gzip.decompress(wire) == content.encode()
    assert store.get_object(entry['key']) == content.encode()
    assert result['uploaded_objects'] == 1
    assert store.objects[candidate.manifest_key] == candidate.manifest
    assert all(store.objects[key] == body for key, body in candidate.shards)
    # A retry and a pre-existing uncompressed object remain create-once.
    assert publish_papers_content(candidate, client=store)['uploaded_objects'] == 0
    store.objects[entry['key']] = content.encode()
    store.encodings[entry['key']] = None
    assert publish_papers_content(candidate, client=store)['uploaded_objects'] == 0
    assert store.objects[entry['key']] == content.encode()


@pytest.mark.parametrize('name,content', [('small.html', 'small'), ('image.png', 'binary' * 400)])
def test_small_and_binary_objects_keep_original_representation(tmp_path, name, content):
    candidate = make_candidate(tmp_path / 'papers', {name: content})
    store = Store()
    publish_papers_content(candidate, client=store)
    key = candidate.entries[0]['key']
    assert store.objects[key] == content.encode()
    assert store.encodings[key] is None
