import json
import hashlib
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


def make_template_candidate(root, directory, version='v1', release='candidate'):
    from tests.unit.test_prepare_papers_templates import populate
    populate(root, version)
    (root / 'style.css').write_text(f'/* {version} */')
    return prepare_papers_content_from_manifest(root,
        build_papers_manifest(root, papers_origin='https://papers.example'),
        papers_origin='https://papers.example', release_id=release, template_dir=directory)


def test_template_catalogue_uploads_json_instead_of_article_html(tmp_path):
    from src.services.data_release.papers_template import assemble_paper
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    store = Store()
    publish_papers_content(candidate, client=store)
    assert store.writes[-1][0] == candidate.manifest_key
    article_entries = [entry for entry in candidate.entries if 'paper_template' in entry]
    assert len(article_entries) == 6
    assert len({entry['key'] for entry in article_entries}) == 2
    for entry in article_entries:
        descriptor = entry['paper_template']
        record = json.loads(store.get_object(entry['key']))
        template = json.loads(store.get_object(f"paper-template-v2/templates/{descriptor['template_sha256']}.json"))
        html = assemble_paper(template, {'schema': 1, 'slug': descriptor['slug'], **record['locales'][descriptor['locale']]})
        assert html == (candidate.root / entry['path']).read_text()
        assert not any(key.startswith(f"releases/sha256/objects/{entry['sha256']}/") for key in store.objects)


def test_template_style_release_reuses_all_article_records(tmp_path):
    first = make_template_candidate(tmp_path / 'first', tmp_path / 't1')
    second = make_template_candidate(tmp_path / 'second', tmp_path / 't2', version='v2')
    first_records = {entry['key'] for entry in first.entries if 'paper_template' in entry}
    assert first_records == {entry['key'] for entry in second.entries if 'paper_template' in entry}
    store = Store()
    publish_papers_content(first, client=store)
    writes = len(store.writes)
    publish_papers_content(second, client=store, previous_objects=first.object_keys)
    assert all(key not in first_records for key, _ in store.writes[writes:])


def test_template_publish_failure_never_commits_catalogue(tmp_path):
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    store = Store()
    store.failure = '/records/'
    with pytest.raises(R2S3Error):
        publish_papers_content(candidate, client=store)
    assert candidate.manifest_key not in store.objects


def test_resume_rejects_template_for_a_different_frozen_article(tmp_path):
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    path = candidate.root / 'research/articles/paper-a/index.html'
    path.write_text(path.read_text().replace('>paper-a</main>', '>updated paper-a</main>'))
    with pytest.raises(ValueError, match='frozen article'):
        prepare_papers_content_from_manifest(candidate.root,
            build_papers_manifest(candidate.root, papers_origin=candidate.origin),
            papers_origin=candidate.origin, release_id='new', template_dir=tmp_path / 'templates')


def test_inventory_reuse_requires_exact_raw_or_gzip_wire_identity(tmp_path):
    from src.services.data_release.papers_content import verified_existing_objects
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    store = Store()
    publish_papers_content(candidate, client=store)
    inventory = {key: {'bytes': len(body), 'etag': hashlib.md5(body, usedforsecurity=False).hexdigest()}
                 for key, body in store.objects.items()}
    assert verified_existing_objects(candidate, inventory) == candidate.object_keys
    record = next(entry for entry in candidate.template_objects if '/records/' in entry['key'])
    raw = (candidate.template_root / record['path']).read_bytes()
    inventory[record['key']] = {'bytes': len(raw), 'etag': hashlib.md5(raw, usedforsecurity=False).hexdigest()}
    assert verified_existing_objects(candidate, inventory) == candidate.object_keys
    inventory[record['key']]['etag'] = '0' * 32
    assert verified_existing_objects(candidate, inventory) == candidate.object_keys - {record['key']}
    inventory[record['key']] = {'bytes': len(raw), 'etag': hashlib.md5(raw, usedforsecurity=False).hexdigest()}
    inventory[record['key']]['bytes'] += 1
    assert verified_existing_objects(candidate, inventory) == candidate.object_keys - {record['key']}


def test_inventory_verification_rejects_changed_local_payload(tmp_path):
    from src.services.data_release.papers_content import verified_existing_objects
    candidate = make_template_candidate(tmp_path / 'site', tmp_path / 'templates')
    record = candidate.template_objects[0]
    (candidate.template_root / record['path']).write_text('{}')
    with pytest.raises(ValueError, match='source changed'):
        verified_existing_objects(candidate, {record['key']: {'bytes': 2, 'etag': 'abc'}})
