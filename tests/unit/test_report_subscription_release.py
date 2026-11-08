from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.data_release import report_subscription as release


ORIGIN = "https://reports.example.test"
WORKER = "https://worker.example.test"
WEDNESDAY = datetime(2026, 11, 4, tzinfo=timezone.utc)
MONDAY = datetime(2026, 11, 9, tzinfo=timezone.utc)


def report(report_id: int, country: str = "JP") -> dict:
    return {
        "id": report_id, "status": "APPROVED", "country_code": country,
        "period_start": "2026-10-12", "period_end": "2026-10-18",
        "metadata": {
            "quality_gate": {"passed": True},
            "peer_review": {"status": "published", "verdict": "accept", "review_mode": "cross_model"},
            "disease_directory": [{"slug": "influenza"}, {"slug": "measles"}],
        },
    }


class Response:
    status = 200

    def __init__(self, value):
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit):
        return self.body[:limit]


class PublishedReports:
    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.remote: dict[str, bytes] = {}
        self.worker_calls: list[dict] = []
        self.worker_fails = False

    def publish(self, rows: list[dict]) -> None:
        contents = {"site-data/reports/index.json": json.dumps(rows).encode()}
        for row in rows:
            path = f"countries/{row['country_code'].lower()}/reports/{row['id']}/index.html"
            contents[path] = f"<html>report {row['id']}</html>".encode()
        files = []
        for path, body in contents.items():
            local = self.reports_dir / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(body)
            key = path.removesuffix("index.html") if path.endswith("/index.html") else path
            self.remote[f"{ORIGIN}/{key}"] = body
            files.append({"path": path, "sha256": hashlib.sha256(body).hexdigest()})
        manifest = {"schema_version": 1, "reports_origin": ORIGIN, "files": files}
        (self.reports_dir / "reports-manifest.json").write_text(json.dumps(manifest))
        self.remote[f"{ORIGIN}/reports-manifest.json"] = json.dumps(manifest).encode()

    def __call__(self, request, *, timeout):
        if request.full_url == f"{WORKER}/health":
            return Response({"ok": True, "capabilities": {"research_content_items_v1": True}})
        if request.full_url == f"{WORKER}/api/admin/notifications":
            payload = json.loads(request.data)
            self.worker_calls.append(payload)
            if self.worker_fails:
                raise TimeoutError("timeout")
            return Response({"ok": True, "campaign": {"id": "campaign-1", "status": "sent", "progress": {"total": 0}}})
        if request.full_url in self.remote:
            return Response(self.remote[request.full_url])
        raise AssertionError(f"unexpected request: {request.full_url}")


def dispatch(tmp_path, published, now=WEDNESDAY, start_week="2026-W45"):
    return release.dispatch_after_release(root=tmp_path, reports_dir=published.reports_dir,
        worker_base_url=WORKER, reports_base_url=ORIGIN, token="test-only",
        start_week=start_week, now=now, opener=published)


def test_published_reports_are_baselined_then_dispatched_once_after_week_end(tmp_path):
    published = PublishedReports(tmp_path / "reports")
    published.publish([report(1)])
    assert dispatch(tmp_path, published)["status"] == "baseline_recorded"
    published.publish([report(1), report(2)])
    assert dispatch(tmp_path, published)["status"] == "no_completed_reports"
    assert published.worker_calls == []
    result = dispatch(tmp_path, published, MONDAY)
    assert result["completed_weeks"] == ["2026-W45"]
    assert [p["list_codes"] for p in published.worker_calls] == [["reports"], ["weekly_digest"]]
    for payload in published.worker_calls:
        assert payload["frequency"] == "weekly"
        assert payload["content_items"] == [{"id": "2", "countries": ["JP"], "diseases": ["influenza", "measles"]}]
        assert "/countries/jp/reports/2/" in payload["contents"]["en"]["markdown"]
    assert dispatch(tmp_path, published, MONDAY)["status"] == "no_completed_reports"
    assert len(published.worker_calls) == 2


def test_pending_report_campaign_is_retried_with_the_same_payload(tmp_path):
    published = PublishedReports(tmp_path / "reports")
    published.publish([report(1)])
    dispatch(tmp_path, published)
    published.publish([report(1), report(2)])
    dispatch(tmp_path, published)
    published.worker_fails = True
    with pytest.raises(release.ReportSubscriptionError, match="worker_transport_error"):
        dispatch(tmp_path, published, MONDAY)
    state = json.loads((tmp_path / "exports/subscription-outbox/full/reports/2026-W45.json").read_text())
    assert state["status"] == "pending"
    first = published.worker_calls[0]
    published.worker_fails = False
    assert dispatch(tmp_path, published, MONDAY)["status"] == "completed"
    assert first == published.worker_calls[1]


def test_public_index_and_pages_must_match_manifest_before_queueing(tmp_path):
    published = PublishedReports(tmp_path / "reports")
    published.publish([report(1)])
    published.remote[f"{ORIGIN}/site-data/reports/index.json"] = b"[]"
    with pytest.raises(release.ReportSubscriptionError, match="public_index_mismatch"):
        dispatch(tmp_path, published)
    published.publish([report(1)])
    dispatch(tmp_path, published)
    published.publish([report(1), report(2)])
    dispatch(tmp_path, published)
    published.remote[f"{ORIGIN}/countries/jp/reports/2/"] = b"wrong"
    with pytest.raises(release.ReportSubscriptionError, match="public_page_manifest_mismatch"):
        dispatch(tmp_path, published, MONDAY)
    assert published.worker_calls == []


def test_only_quality_gated_cross_model_published_reports_are_eligible():
    accepted = report(1)
    assert release._report(accepted, ORIGIN)["id"] == "1"
    for changed in [
        {**report(2), "status": "DRAFT"},
        {**report(3), "metadata": {**report(3)["metadata"], "peer_review": {"status": "published", "verdict": "accept", "review_mode": "single_model"}}},
        {**report(4), "metadata": {**report(4)["metadata"], "quality_gate": {"passed": False}}},
    ]:
        assert release._report(changed, ORIGIN) is None


def test_impossible_or_reversed_report_periods_are_rejected():
    for start, end in [("2026-02-30", "2026-03-01"), ("2026-10-19", "2026-10-18")]:
        with pytest.raises(release.ReportSubscriptionError, match="report_period_invalid"):
            release._report({**report(5), "period_start": start, "period_end": end}, ORIGIN)


def test_cloudflare_email_rewrite_is_reversed_without_masking_content_changes():
    original = b'<a href="mailto:person@example.test">Mail</a></body>'
    email = b"person@example.test"
    encoded = bytes([0x42]) + bytes(c ^ 0x42 for c in email)
    transformed = (b'<a href="/cdn-cgi/l/email-protection#' + encoded.hex().encode() +
        b'">Mail</a><script data-cfasync="false" src="/cdn-cgi/scripts/abcdef12/cloudflare-static/email-decode.min.js"></script></body>')
    assert release._undo_cloudflare_html_transforms(transformed) == original
    assert release._undo_cloudflare_html_transforms(transformed.replace(b"Mail", b"Changed")) != original


def test_cloudflare_normalization_does_not_hide_injected_script_code():
    # Merely containing the Cloudflare markers is not enough: the old loose
    # regex removed this whole attacker-controlled block before hashing.
    forged = (b'<script>(function(){function c(){var b=a.contentDocument||evil();'
              b'window.__CF$cv$params={};doEvil();'
              b'"/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray=abc";}}</script>')
    assert release._undo_cloudflare_html_transforms(forged) == forged
    beacon = (b'<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js/vabc" '
              b'integrity="sha512-' + b'A' * 88 + b'" '
              b'data-cf-beacon=\'{"version":"2024.11.0","token":"' + b'a' * 32 + b'","r":1,"spa":2}\' '
              b'onload="evil()" crossorigin="anonymous"></script>')
    assert release._undo_cloudflare_html_transforms(beacon) == beacon


@pytest.mark.asyncio
async def test_release_hook_is_off_by_default_and_passes_config_when_enabled(tmp_path):
    runtime = SimpleNamespace(root_dir=tmp_path)
    service = SimpleNamespace(_reports_site_dir=lambda: tmp_path / "reports", _env_value=lambda name, default="": default)
    assert await release.dispatch_if_enabled(service, runtime, reports_origin=ORIGIN) is None
    values = {"SUBSCRIPTIONS__REPORT_DIGEST_ENABLED": "true", "SUBSCRIPTIONS__PUBLIC_BASE_URL": WORKER,
              "SUBSCRIPTIONS__ADMIN_API_TOKEN": "test-only", "SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK": "2026-W45"}
    service._env_value = lambda name, default="": values.get(name, default)
    with patch.object(release, "dispatch_after_release", return_value={"status": "baseline_recorded"}) as mocked:
        assert await release.dispatch_if_enabled(service, runtime, reports_origin=ORIGIN) == {"status": "baseline_recorded"}
    assert mocked.call_args.kwargs["start_week"] == "2026-W45"
    assert mocked.call_args.kwargs["reports_base_url"] == ORIGIN

    # A notification outage must not fail a report bundle already uploaded to R2.
    with patch.object(release, "dispatch_after_release", side_effect=release.ReportSubscriptionError("worker_transport_error:TimeoutError")):
        result = await release.dispatch_if_enabled(service, runtime, reports_origin=ORIGIN)
    assert result == {"status": "failed", "error_code": "worker_transport_error:TimeoutError"}


def test_pilot_report_observations_and_campaign_keys_are_separate(tmp_path):
    published = PublishedReports(tmp_path / 'reports')
    published.publish([report(1)])
    args = dict(root=tmp_path, reports_dir=published.reports_dir, worker_base_url=WORKER,
                reports_base_url=ORIGIN, token='test-only', start_week='2026-W45', opener=published)
    assert release.dispatch_after_release(**args, now=WEDNESDAY, mode='pilot')['status'] == 'baseline_recorded'
    published.publish([report(1), report(2)])
    release.dispatch_after_release(**args, now=WEDNESDAY, mode='pilot')
    release.dispatch_after_release(**args, now=MONDAY, mode='pilot')
    assert all(item['idempotency_key'].startswith('pilot:reports-weekly:') for item in published.worker_calls)
    # Switching modes creates a fresh full baseline, so pilot observations cannot
    # turn existing reports into a historical mailing to everyone.
    assert release.dispatch_after_release(**args, now=MONDAY, mode='full')['status'] == 'baseline_recorded'
    assert len(published.worker_calls) == 2
