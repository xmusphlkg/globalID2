from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from src.services.data_release import notice_subscription as release


IDENTITY = {"release_id": "r-1", "build_digest": "hash", "source_commit": "abc", "deployment_branch": "main"}
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def notice(identifier="a1", published="2026-11-04", verdict="accept"):
    return {"alert_id": identifier, "notice_id": f"notice-{identifier}", "outbreak_id": "OB-1",
            "published_date": published, "url": "https://who.int/example", "title_en": "Official notice",
            "summary_en": "Published information.", "summary_zh": "已发布信息。",
            "country_code": "CN", "disease_slug": "influenza", "status": "new",
            "ai": {"verdict": verdict, "review_mode": "cross_model"}}


class Response:
    status = 200

    def __init__(self, value):
        self.body = json.dumps(value).encode()

    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self, limit): return self.body[:limit]


def files(tmp_path, records):
    astro = tmp_path / "astro-site"
    source = astro / "dist/situation/outbreaks/OB-1.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"schema_version": "gids.situation_alerts.v1", "alerts": records}), encoding="utf-8")
    page = astro / "dist/situation/index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("published", encoding="utf-8")
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY["build_digest"] = _built_site_digest(astro / "dist")
    return astro


def call(tmp_path, astro, opener, *, start_date="2026-11-04"):
    return release.dispatch_after_release(root=tmp_path, astro_dir=astro, identity=IDENTITY,
        worker_base_url="https://worker.example.test", public_base_url="https://site.example.test",
        token="unit-secret", start_date=start_date, now=NOW, opener=opener)


def test_only_new_reviewed_notices_are_dispatched_and_replays_do_not_refanout(tmp_path):
    astro = files(tmp_path, [notice(), notice("old", "2026-11-03"), notice("rejected", verdict="revise")])
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        if request.full_url.endswith("/release.json"): return Response(IDENTITY)
        if request.full_url.endswith("/health"): return Response({"capabilities": {"official_notice_alert_v1": True}})
        return Response({"ok": True})

    assert call(tmp_path, astro, opener) == {"status": "accepted", "accepted": 1}
    assert call(tmp_path, astro, opener)["status"] == "no_new_notices"
    posts = [request for request in requests if request.get_method() == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].data)
    assert payload["idempotency_key"] == "official-notice:a1:r1"
    assert payload["countries"] == ["CN"] and payload["diseases"] == ["influenza"]
    assert "signal" not in payload


def test_timeout_resumes_same_persisted_event(tmp_path):
    astro = files(tmp_path, [notice()])
    failing = True
    payloads = []

    def opener(request, *, timeout):
        if request.full_url.endswith("/release.json"): return Response(IDENTITY)
        if request.full_url.endswith("/health"): return Response({"capabilities": {"official_notice_alert_v1": True}})
        payloads.append(json.loads(request.data))
        if failing: raise TimeoutError()
        return Response({"ok": True, "duplicate": True})

    with pytest.raises(release.NoticeSubscriptionError):
        call(tmp_path, astro, opener)
    saved = tmp_path / "exports/subscription-outbox/full/official-notices/a1.json"
    assert json.loads(saved.read_text())["status"] == "pending"
    failing = False
    assert call(tmp_path, astro, opener)["accepted"] == 1
    assert payloads[0] == payloads[1]


def test_old_worker_cannot_receive_official_notices(tmp_path):
    astro = files(tmp_path, [notice()])
    methods = []

    def opener(request, *, timeout):
        methods.append(request.get_method())
        if request.full_url.endswith("/release.json"): return Response(IDENTITY)
        return Response({"ok": True})

    with pytest.raises(release.NoticeSubscriptionError, match="capability_required"):
        call(tmp_path, astro, opener)
    assert methods == ["GET", "GET"]


def test_start_date_is_required_and_old_backfill_is_never_dispatched(tmp_path):
    astro = files(tmp_path, [notice("old", "2026-10-01")])
    with pytest.raises(release.NoticeSubscriptionError, match="start_date_required"):
        call(tmp_path, astro, lambda *_a, **_k: None, start_date="")
    assert call(tmp_path, astro, lambda *_a, **_k: Response(IDENTITY), start_date="2026-09-01")["status"] == "no_new_notices"


def test_uses_built_release_data_even_if_source_export_changes_after_build(tmp_path):
    astro = files(tmp_path, [notice("published")])
    source = astro / "src/data/situation/international/outbreaks/OB-1.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"schema_version": "gids.situation_alerts.v1", "alerts": [notice("unpublished")]}))
    sent = []
    def opener(request, *, timeout):
        if request.full_url.endswith("/release.json"): return Response(IDENTITY)
        if request.full_url.endswith("/health"): return Response({"capabilities": {"official_notice_alert_v1": True}})
        sent.append(json.loads(request.data))
        return Response({"ok": True})
    assert call(tmp_path, astro, opener)["accepted"] == 1
    assert [item["alert_id"] for item in sent] == ["published"]


def test_changed_build_artifact_is_never_dispatched_as_the_previous_release(tmp_path):
    astro = files(tmp_path, [notice("published")])
    built = astro / "dist/situation/outbreaks/OB-1.json"
    built.write_text(json.dumps({"schema_version": "gids.situation_alerts.v1", "alerts": [notice("changed")]}))
    with pytest.raises(release.NoticeSubscriptionError, match="release_build_digest_mismatch"):
        call(tmp_path, astro, lambda *_args, **_kwargs: pytest.fail("must not reach network"))


def test_pilot_notice_and_full_notice_do_not_share_event_idempotency(tmp_path):
    astro = files(tmp_path, [notice()])
    keys = []
    def opener(request, *, timeout):
        if request.full_url.endswith('/release.json'): return Response(IDENTITY)
        if request.full_url.endswith('/health'): return Response({'capabilities': {'official_notice_alert_v1': True}, 'delivery_mode': 'pilot'})
        keys.append(json.loads(request.data)['idempotency_key'])
        return Response({'ok': True})
    args = dict(root=tmp_path, astro_dir=astro, identity=IDENTITY, worker_base_url='https://worker.example.test',
                public_base_url='https://site.example.test', token='unit-secret', start_date='2026-11-04', now=NOW, opener=opener)
    assert release.dispatch_after_release(**args, mode='pilot', guard_activation=True)['accepted'] == 1
    assert release.dispatch_after_release(**args, mode='full')['accepted'] == 1
    assert keys == ['pilot:official-notice:a1:r1', 'official-notice:a1:r1']


def test_full_activation_starts_next_day_without_replaying_pilot_notices(tmp_path):
    astro = files(tmp_path, [notice()])
    result = release.dispatch_after_release(root=tmp_path, astro_dir=astro, identity=IDENTITY,
        worker_base_url='https://worker.example.test', public_base_url='https://site.example.test',
        token='unit-secret', start_date='2026-11-01', now=NOW, mode='full', guard_activation=True,
        opener=lambda request, timeout: Response({'delivery_mode': 'full'}))
    assert result['status'] == 'before_activation_date'
    marker = json.loads((tmp_path / 'exports/subscription-outbox/full/official-notices/.activation.json').read_text())
    assert marker['floor'] == '2026-11-05'
    result = release.dispatch_after_release(root=tmp_path, astro_dir=astro, identity=IDENTITY,
        worker_base_url='https://worker.example.test', public_base_url='https://site.example.test',
        token='unit-secret', start_date='2026-11-01', now=datetime(2026, 11, 5, tzinfo=timezone.utc),
        mode='full', guard_activation=True, opener=lambda request, timeout: Response(IDENTITY if request.full_url.endswith('/release.json') else {'delivery_mode': 'full'}))
    assert result['status'] == 'no_new_notices'


def test_independent_publication_uses_verified_objects_and_rejects_corruption(tmp_path):
    astro = files(tmp_path, [])
    (astro / 'dist/situation/outbreaks/OB-1.json').unlink()
    (astro / 'dist/situation/outbreaks').rmdir()
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY['build_digest'] = _built_site_digest(astro / 'dist')
    body = json.dumps({'schema_version': 'gids.situation_alerts.v1', 'alerts': [notice()]}).encode()
    digest = hashlib.sha256(body).hexdigest()
    obj = tmp_path / 'exports/situation-data/objects' / (digest + '.json')
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    manifest = {'schema_version': 1, 'release_id': 'situation-1', 'files': {
        'outbreaks/OB-1.json': {'url': f'objects/{digest}.json', 'sha256': digest, 'bytes': len(body)}}}
    sent = []
    def opener(request, *, timeout):
        if request.full_url.endswith('/site-data/situation/current.json'): return Response(manifest)
        if request.full_url.endswith('/release.json'): return Response(IDENTITY)
        if request.full_url.endswith('/health'): return Response({'capabilities': {'official_notice_alert_v1': True}})
        sent.append(json.loads(request.data))
        return Response({'ok': True})
    assert call(tmp_path, astro, opener)['accepted'] == 1
    assert [item['alert_id'] for item in sent] == ['a1']
    obj.write_bytes(b'corrupt')
    with pytest.raises(release.NoticeSubscriptionError, match='situation_export_invalid'):
        call(tmp_path, astro, opener)


def test_independent_publication_change_is_rejected_before_staging(tmp_path):
    astro = files(tmp_path, [])
    (astro / 'dist/situation/outbreaks/OB-1.json').unlink()
    (astro / 'dist/situation/outbreaks').rmdir()
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY['build_digest'] = _built_site_digest(astro / 'dist')
    body = json.dumps({'schema_version': 'gids.situation_alerts.v1', 'alerts': [notice()]}).encode()
    digest = hashlib.sha256(body).hexdigest()
    obj = tmp_path / 'exports/situation-data/objects' / (digest + '.json')
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    manifest = {'schema_version': 1, 'release_id': 'situation-1', 'files': {
        'outbreaks/OB-1.json': {'url': f'objects/{digest}.json', 'sha256': digest, 'bytes': len(body)}}}
    calls = 0
    def opener(request, *, timeout):
        nonlocal calls
        if request.full_url.endswith('/site-data/situation/current.json'):
            calls += 1
            return Response({**manifest, 'release_id': f'situation-{calls}'})
        if request.full_url.endswith('/release.json'): return Response(IDENTITY)
        pytest.fail('must not dispatch')
    with pytest.raises(release.NoticeSubscriptionError, match='situation_publication_changed'):
        call(tmp_path, astro, opener)
    assert not (tmp_path / 'exports/subscription-outbox/full/official-notices/a1.json').exists()


def test_empty_independent_scan_requires_matching_public_release_and_stable_pointer(tmp_path):
    astro = files(tmp_path, [])
    (astro / 'dist/situation/outbreaks/OB-1.json').unlink()
    (astro / 'dist/situation/outbreaks').rmdir()
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY['build_digest'] = _built_site_digest(astro / 'dist')
    body = json.dumps({'schema_version': 'gids.situation_alerts.v1', 'alerts': []}).encode()
    digest = hashlib.sha256(body).hexdigest()
    obj = tmp_path / 'exports/situation-data/objects' / (digest + '.json')
    obj.parent.mkdir(parents=True)
    obj.write_bytes(body)
    manifest = {'schema_version': 1, 'release_id': 'situation-1', 'files': {
        'outbreaks/OB-1.json': {'url': f'objects/{digest}.json', 'sha256': digest, 'bytes': len(body)}}}
    calls = []
    changed = False
    def opener(request, *, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith('/site-data/situation/current.json'):
            return Response({**manifest, 'release_id': 'situation-2'} if changed and calls.count(request.full_url) == 2 else manifest)
        if request.full_url.endswith('/release.json'): return Response(IDENTITY)
        pytest.fail('an empty scan must not dispatch')
    assert call(tmp_path, astro, opener)['status'] == 'no_new_notices'
    assert len(calls) == 3 and calls[0] == calls[2]
    calls.clear()
    changed = True
    with pytest.raises(release.NoticeSubscriptionError, match='situation_publication_changed'):
        call(tmp_path, astro, opener)
