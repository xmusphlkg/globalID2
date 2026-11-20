"""A committed publication retains its complete closure and no old managed version."""
from dataclasses import replace
import json

import pytest

from src.services.data_release.r2_single_version import managed_publication, seed_active
from src.services.data_release.papers_r2 import R2S3Error
from tests.unit.test_r2_indexed_client import CONFIG, Remote, key_for, make_client, put


def publish(client, channel, body, objects):
    pointer = f'{channel}/current.json'
    with managed_publication(client, channel=channel) as publication:
        previous = client.get_object(pointer)
        publication.stage(body, objects)
        for key, payload in objects.items():
            put(client, key, payload)
        client.put_object_conditional(pointer, body, previous=previous)
        publication.commit(body)
        return publication.cleanup()


def test_commit_retires_old_objects_and_keeps_unmanaged_files_without_listing_again(make_client):
    remote = Remote()
    remote.store('original-data/never-managed', b'original')
    client, index = make_client(remote)
    old, shared, new = (key_for(value) for value in (b'old', b'shared', b'new'))
    channel = 'site-data/research-publication'
    publish(client, channel, b'first', {old: b'old', shared: b'shared'})
    remote.requests.clear()
    result = publish(client, channel, b'second', {shared: b'shared', new: b'new'})
    assert set(remote.objects) == {shared, new, f'{channel}/current.json', 'original-data/never-managed'}
    assert index.get(old) is None
    assert result == {'deleted_objects': 1, 'pending_cleanup_objects': 0}
    assert ('GET', '') not in remote.operations()


def test_shared_objects_survive_until_all_current_channels_release_them(make_client):
    remote = Remote()
    client, _ = make_client(remote)
    shared, only_a, only_b = (key_for(value) for value in (b'shared', b'only a', b'only b'))
    publish(client, 'site-data/a', b'a1', {shared: b'shared', only_a: b'only a'})
    publish(client, 'site-data/b', b'b1', {shared: b'shared', only_b: b'only b'})
    publish(client, 'site-data/a', b'a2', {})
    assert only_a not in remote.objects
    assert shared in remote.objects and only_b in remote.objects
    publish(client, 'site-data/b', b'b2', {})
    assert set(remote.objects) == {'site-data/a/current.json', 'site-data/b/current.json'}


def test_uncommitted_stage_is_protected_until_a_new_commit_retires_it(make_client):
    remote = Remote()
    client, _ = make_client(remote)
    channel = 'site-data/a'
    old, failed, new = (key_for(value) for value in (b'old', b'failed', b'new'))
    publish(client, channel, b'first', {old: b'old'})
    with managed_publication(client, channel=channel) as publication:
        publication.stage(b'failed-pointer', {failed})
        put(client, failed, b'failed')
        with pytest.raises(ValueError, match='pointer changed'):
            publication.commit(b'failed-pointer')
        assert publication.cleanup()['deleted_objects'] == 0
    assert old in remote.objects and failed in remote.objects
    publish(client, channel, b'new-pointer', {new: b'new'})
    assert set(remote.objects) == {new, f'{channel}/current.json'}


def test_partial_cleanup_is_durable_and_retry_never_deletes_current(make_client):
    remote = Remote()
    client, index = make_client(remote)
    channel = 'site-data/a'
    old, new = key_for(b'old'), key_for(b'new')
    publish(client, channel, b'first', {old: b'old'})
    remote.delete_failures.add(old)
    with pytest.raises(R2S3Error, match='batch deletion failed'):
        publish(client, channel, b'new-pointer', {new: b'new'})
    assert remote.objects[f'{channel}/current.json']['body'] == b'new-pointer'
    assert new in remote.objects and old in remote.objects
    assert index.get(old) is None
    remote.delete_failures.clear()
    with managed_publication(client, channel=channel) as resumed:
        resumed.stage(b'new-pointer', {new})
        resumed.commit(b'new-pointer')
        assert resumed.cleanup() == {'deleted_objects': 1, 'pending_cleanup_objects': 0}
    assert set(remote.objects) == {new, f'{channel}/current.json'}


def test_changed_other_channel_pointer_stops_cleanup(make_client):
    remote = Remote()
    client, _ = make_client(remote)
    old, new = key_for(b'old'), key_for(b'new')
    publish(client, 'site-data/a', b'a1', {old: b'old'})
    publish(client, 'site-data/b', b'b1', {})
    remote.store('site-data/b/current.json', b'external change', cache_control='no-store')
    with pytest.raises(ValueError, match='current pointer differs'):
        publish(client, 'site-data/a', b'a2', {new: b'new'})
    assert old in remote.objects and new in remote.objects


def test_same_pointer_digest_cannot_adopt_a_different_reference_set(make_client):
    client, _ = make_client(Remote())
    with managed_publication(client, channel='site-data/a') as publication:
        publication.stage(b'candidate', {key_for(b'one')})
        with pytest.raises(ValueError, match='references changed'):
            publication.stage(b'candidate', {key_for(b'two')})


def test_seed_records_existing_current_and_never_deletes_remote(make_client):
    remote = Remote()
    channel = 'site-data/a'
    old, new = key_for(b'old'), key_for(b'new')
    remote.store(old, b'old')
    remote.store(f'{channel}/current.json', b'first', cache_control='no-store')
    client, _ = make_client(remote)
    seed_active(client, channel=channel, pointer_key=f'{channel}/current.json', body=b'first', keys={old})
    assert not any(method in {'DELETE', 'POST', 'PUT'} for method, _key, _headers in remote.requests)
    publish(client, channel, b'new-pointer', {new: b'new'})
    assert set(remote.objects) == {new, f'{channel}/current.json'}


def test_surveillance_publisher_leaves_only_current_json_objects(make_client, tmp_path):
    from src.services.data_release.surveillance_publish import publication_upload_sizes, publish_surveillance_data
    from tests.unit.test_surveillance_publish import PREFIX, generation

    remote = Remote()
    client, index = make_client(remote)
    generation(tmp_path, release='first')
    publish_surveillance_data(tmp_path, CONFIG, client=client)
    first = remote.objects[f'{PREFIX}/current.json']['body']
    generation(tmp_path, value=2, release='second')
    body = (tmp_path / 'current.json').read_bytes()
    remote.requests.clear()
    result = publish_surveillance_data(tmp_path, CONFIG, client=client)
    expected = publication_upload_sizes(body, prefix=PREFIX, previous=first)
    assert set(remote.objects) == set(expected)
    assert set(index.snapshot()) == set(expected)
    assert result['retention']['deleted_objects'] == 3
    assert ('GET', '') not in remote.operations()
    remote.requests.clear()
    resumed = publish_surveillance_data(tmp_path, CONFIG, client=client)
    assert resumed['pointer_changed'] is False
    assert not any(method in {'PUT', 'POST', 'DELETE'} for method, _key, _headers in remote.requests)


def test_literature_keeps_one_combined_papers_and_json_closure(make_client, tmp_path):
    from src.services.data_release.literature_publish import PREFIX, publish_literature_candidate
    from src.services.data_release.papers_content import candidate_upload_sizes
    from src.services.data_release.surveillance_publish import capture_surveillance_baseline, publication_upload_sizes
    from tests.unit.test_papers_content import make_candidate
    from tests.unit.test_surveillance_publish import generation

    remote = Remote()
    client, index = make_client(remote)
    config = replace(CONFIG, public_base_url='https://papers.example')
    previous = None
    for number in (1, 2):
        run = tmp_path / f'run-{number}'
        run.mkdir()
        capture_surveillance_baseline(run, config, prefix=PREFIX, client=client)
        generation(run, value=number, release='candidate')
        candidate = make_candidate(tmp_path / f'papers-{number}', {'article.html': f'Article {number}'})
        result = publish_literature_candidate(run=run, objects_dir=run / 'objects', papers=candidate,
            config=config, receipt=tmp_path / 'receipt.json', client=client)
        body = remote.objects[f'{PREFIX}/current.json']['body']
        expected = set(candidate_upload_sizes(candidate)) | set(publication_upload_sizes(body, prefix=PREFIX, previous=previous))
        assert set(remote.objects) == expected
        assert set(index.snapshot()) == expected
        assert json.loads(body)['papers']['papers_generation_sha256'] == candidate.generation
        previous = body
    assert result['retention']['deleted_objects'] > 3
