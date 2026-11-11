from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from src.services.data_release import research_subscription as release


IDENTITY = {"release_id": "r-1", "build_digest": "hash", "source_commit": "abc", "deployment_branch": "main"}
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def brief():
    return {
        "week": "2026-W44", "url": "/research/weekly/2026-W44/",
        "brief_status": "automatically_compiled_not_editorially_reviewed",
        "methodology": {"en": "Published evidence.", "zh": "已发表证据。"},
        "cited_findings": [{"article_id": "one", "title": "Research", "finding_en": "Findings.",
                            "finding_zh": "发现。", "source_url": "/research/articles/one/",
                            "provenance": "published_bilingual_structured_summary"}],
        "articles": [{"article_id": "one", "editorial_status": "published",
                      "content_tier": "quality_gated_multilingual_evidence",
                      "diseases": [{"slug": "influenza"}], "countries": [{"code": "CN"}]}],
    }


class Response:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit):
        return self.body[:limit]


def files(tmp_path):
    astro = tmp_path / "astro-site"
    source = astro / "src/data/research/weekly/2026-W44.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(brief()), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for prefix in ("", "zh"):
        page = astro / "dist" / prefix / "research/weekly/2026-W44/index.html"
        page.parent.mkdir(parents=True)
        page.write_text(f'<span hidden data-research-brief-sha256="{digest}"></span>', encoding="utf-8")
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY["build_digest"] = _built_site_digest(astro / "dist")
    return astro


def test_built_locales_must_match_the_exact_brief_bytes(tmp_path):
    astro = files(tmp_path)
    source = astro / "src/data/research/weekly/2026-W44.json"
    source.write_text(json.dumps({**brief(), "week": "2026-W44", "extra": "changed after build"}))
    with pytest.raises(release.ResearchSubscriptionError, match="research_digest_build_data_mismatch"):
        release._payload_for_week(astro, "2026-W44", "https://site.example.test")


def call(tmp_path, astro, opener, *, start_week="2026-W44"):
    return release.dispatch_after_release(
        root=tmp_path, astro_dir=astro, identity=IDENTITY,
        worker_base_url="https://worker.example.test", public_base_url="https://site.example.test",
        token="test-only", start_week=start_week, now=NOW, opener=opener,
    )


def test_completed_week_uses_utc_and_year_boundary():
    assert release.completed_week(NOW) == "2026-W44"
    assert release.completed_week(datetime(2027, 1, 4, tzinfo=timezone.utc)) == "2026-W53"


def test_independent_digest_requires_current_pointer_and_active_public_route():
    identity = {'release_id': 'literature-one', 'papers_generation_sha256': 'a' * 64}
    class Reply(Response):
        def __init__(self, body, headers=None):
            super().__init__(body)
            self.headers = headers or {}
    def opener(request, timeout):
        if request.full_url.endswith('/current.json'):
            return Reply({'release_id': identity['release_id'], 'papers': {'papers_generation_sha256': 'a' * 64}})
        return Reply({}, {'X-GIDS-Research-Release': identity['release_id']})
    release._verify_live_literature_pointer('https://site.example.test', identity, opener=opener)
    def not_active(request, timeout):
        if request.full_url.endswith('/current.json'):
            return Reply({'release_id': identity['release_id'], 'papers': {'papers_generation_sha256': 'a' * 64}})
        return Reply({}, {})
    with pytest.raises(release.ResearchSubscriptionError, match='pointer_mismatch'):
        release._verify_live_literature_pointer('https://site.example.test', identity, opener=not_active)


def test_no_send_before_activation_floor(tmp_path):
    def no_network(*_args, **_kwargs):
        raise AssertionError("must not access network")

    assert call(tmp_path, tmp_path / "site", no_network, start_week="2026-W45")["status"] == "before_activation_week"


def test_immutable_outbox_retries_after_failure_and_never_recreates_completed(tmp_path):
    astro = files(tmp_path)
    calls = []
    fail_once = True

    def opener(request, *, timeout):
        nonlocal fail_once
        calls.append(request.full_url)
        if request.full_url.endswith("/release.json"):
            return Response(IDENTITY)
        if request.full_url.endswith("/health"):
            return Response({"ok": True, "capabilities": {"research_content_items_v1": True, "research_personalized_items_v1": True}})
        if fail_once:
            fail_once = False
            raise TimeoutError("connection timeout")
        posted = json.loads(request.data)
        assert posted["idempotency_key"] == "research-digest:2026-W44:r1"
        return Response({"ok": True, "duplicate": True, "campaign": {
            "id": "campaign-1", "status": "sent", "progress": {"total": 1, "sent": 1},
        }})

    with pytest.raises(release.ResearchSubscriptionError, match="worker_transport_error"):
        call(tmp_path, astro, opener)
    record_path = tmp_path / "exports/subscription-outbox/full/research/2026-W44.json"
    assert json.loads(record_path.read_text())["status"] == "pending"
    assert call(tmp_path, astro, opener)["status"] == "completed"
    assert call(tmp_path, astro, opener)["status"] == "already_completed"
    assert calls.count("https://worker.example.test/api/admin/notifications") == 2


def test_legacy_pending_outbox_resumes_its_exact_original_payload(tmp_path):
    astro = files(tmp_path)
    payload = release._payload_for_week(astro, "2026-W44", "https://site.example.test")
    payload.pop("personalized_templates")
    for item in payload["content_items"]:
        item.pop("localized_markdown")
    directory = tmp_path / "exports/subscription-outbox/full/research"
    directory.mkdir(parents=True)
    (directory / "2026-W44.json").write_text(json.dumps({
        "week": "2026-W44", "payload": payload, "payload_sha256": release._fingerprint(payload),
        "release_id": "r-1", "status": "pending",
    }), encoding="utf-8")
    posted = []
    def opener(request, *, timeout):
        if request.full_url.endswith("/release.json"):
            return Response(IDENTITY)
        if request.full_url.endswith("/health"):
            return Response({"ok": True, "capabilities": {"research_content_items_v1": True}})
        posted.append(json.loads(request.data))
        return Response({"ok": True, "duplicate": True, "campaign": {
            "id": "legacy", "status": "sent", "progress": {"total": 1, "sent": 1},
        }})
    assert call(tmp_path, astro, opener)["status"] == "completed"
    assert posted == [payload]


def test_release_mismatch_rejects_before_any_worker_request(tmp_path):
    astro = files(tmp_path)
    calls = []

    def opener(request, *, timeout):
        calls.append(request.full_url)
        return Response({**IDENTITY, "release_id": "different"})

    with pytest.raises(release.ResearchSubscriptionError, match="public_release_verification_failed"):
        call(tmp_path, astro, opener)
    assert calls == ["https://site.example.test/release.json"]


def test_pending_outbox_survives_the_next_week(tmp_path):
    astro = files(tmp_path)
    failing = True

    def opener(request, *, timeout):
        if request.full_url.endswith("/release.json"):
            return Response(IDENTITY)
        if request.full_url.endswith("/health"):
            return Response({"ok": True, "capabilities": {"research_content_items_v1": True, "research_personalized_items_v1": True}})
        if failing:
            raise TimeoutError()
        return Response({"ok": True, "duplicate": True, "campaign": {
            "id": "campaign-1", "status": "sent", "progress": {"total": 1, "sent": 1},
        }})

    with pytest.raises(release.ResearchSubscriptionError):
        call(tmp_path, astro, opener)
    failing = False
    result = release.dispatch_after_release(
        root=tmp_path, astro_dir=astro, identity=IDENTITY,
        worker_base_url="https://worker.example.test", public_base_url="https://site.example.test",
        token="test-only", start_week="2026-W44", now=datetime(2026, 11, 11, tzinfo=timezone.utc),
        opener=opener,
    )
    assert result["completed_weeks"] == ["2026-W44"]


def test_content_changes_do_not_automatically_create_a_second_campaign(tmp_path):
    astro = files(tmp_path)

    def opener(request, *, timeout):
        if request.full_url.endswith("/release.json"):
            return Response(IDENTITY)
        if request.full_url.endswith("/health"):
            return Response({"ok": True, "capabilities": {"research_content_items_v1": True, "research_personalized_items_v1": True}})
        raise TimeoutError()

    with pytest.raises(release.ResearchSubscriptionError):
        call(tmp_path, astro, opener)
    source = astro / "src/data/research/weekly/2026-W44.json"
    changed = brief()
    changed["cited_findings"][0]["finding_en"] = "Changed after publication."
    source.write_text(json.dumps(changed), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for prefix in ("", "zh"):
        page = astro / "dist" / prefix / "research/weekly/2026-W44/index.html"
        page.write_text(f'<span hidden data-research-brief-sha256="{digest}"></span>', encoding="utf-8")
    from src.services.data_release.pipeline import _built_site_digest
    IDENTITY["build_digest"] = _built_site_digest(astro / "dist")
    with pytest.raises(release.ResearchSubscriptionError, match="requires_review"):
        call(tmp_path, astro, opener)


def test_pilot_outbox_and_key_are_isolated_from_full_delivery(tmp_path):
    astro = files(tmp_path)
    keys = []

    def opener(request, *, timeout):
        if request.full_url.endswith('/release.json'):
            return Response(IDENTITY)
        if request.full_url.endswith('/health'):
            return Response({'delivery_mode': 'pilot', 'capabilities': {'research_content_items_v1': True, 'research_personalized_items_v1': True}})
        keys.append(json.loads(request.data)['idempotency_key'])
        return Response({'ok': True, 'campaign': {'id': 'c1', 'status': 'sent', 'progress': {'total': 0}}})

    args = dict(root=tmp_path, astro_dir=astro, identity=IDENTITY,
                worker_base_url='https://worker.example.test', public_base_url='https://site.example.test',
                token='test-only', start_week='2026-W44', now=NOW, opener=opener)
    assert release.dispatch_after_release(**args, mode='pilot', guard_activation=True)['status'] == 'completed'
    assert release.dispatch_after_release(**args, mode='full')['status'] == 'completed'
    assert keys == ['pilot:research-digest:2026-W44:r1', 'research-digest:2026-W44:r1']
    assert (tmp_path / 'exports/subscription-outbox/pilot/research/2026-W44.json').exists()
    assert (tmp_path / 'exports/subscription-outbox/full/research/2026-W44.json').exists()


def test_full_activation_does_not_replay_last_completed_week(tmp_path):
    result = release.dispatch_after_release(root=tmp_path, astro_dir=tmp_path, identity=IDENTITY,
        worker_base_url='https://worker.example.test', public_base_url='https://site.example.test',
        token='test-only', start_week='2026-W40', now=NOW, mode='full', guard_activation=True,
        opener=lambda request, timeout: Response({'delivery_mode': 'full'}))
    assert result['status'] == 'before_activation_week'
    marker = json.loads((tmp_path / 'exports/subscription-outbox/full/research/.activation.json').read_text())
    assert marker['floor'] == '2026-W45'


def test_worker_phase_mismatch_fails_closed(tmp_path):
    astro = files(tmp_path)
    def opener(request, *, timeout):
        assert request.full_url.endswith('/health')
        return Response({'delivery_mode': 'full'})
    with pytest.raises(release.ResearchSubscriptionError, match='subscription_worker_mode_mismatch'):
        release.dispatch_after_release(root=tmp_path, astro_dir=astro, identity=IDENTITY,
            worker_base_url='https://worker.example.test', public_base_url='https://site.example.test',
            token='test-only', start_week='2026-W44', now=NOW, mode='pilot', guard_activation=True, opener=opener)
