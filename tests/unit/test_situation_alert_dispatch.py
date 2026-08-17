from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts.automation import dispatch_situation_alerts as dispatcher


def _signal(*, signal_id: str = "signal-reviewed", basis: str = "analyst_review") -> dict:
    automated = basis == "automated_policy"
    return {
        "identity": {
            "signal_id": signal_id,
            "disease_id": "D001",
            "disease_name": "Example disease",
            "disease_slug": "example-disease",
            "country_code": "US",
            "country_name": "United States",
            "canonical_geography_key": "country:US:national",
            "metric_type": "case_notifications",
            "metric_label": "cases",
        },
        "observation": {
            "data_through": "2026-08-16",
            "data_status": "current" if automated else "held_back",
            "current": 160,
            "expected": 105,
            "completeness": 0.99,
        },
        "anomaly": {
            "model": "robust_quasi_poisson_v1" if automated else "seasonal_robust_z_fallback_v1",
            "detector_tier": "common_count",
            "state": "strong",
            "q_value": 0.001,
            "fit_status": "completed" if automated else "fallback_completed",
            "effect_threshold_passed": True,
        },
        "assessment": {
            "signal_type": "statistical_signal",
            "temporal_relevance": "current" if automated else "lagged",
            "verification_status": "verified",
            "verification_basis": basis,
            "verification_policy_version": "guarded_auto_v1" if automated else None,
            "verified_by": "policy:guarded_auto_v1" if automated else "reviewer:17",
            "verified_at": "2026-08-17T01:55:00Z",
        },
        "evidence_links": [
            {"title": "Official source", "url": "https://example.invalid/evidence"}
        ],
    }


def _report(*signals: dict) -> dict:
    return {
        "schema_version": "situation_room.v3",
        "public_enabled": True,
        "report": {
            "report_id": "situation-v3-daily-2026-08-17-r1",
            "as_of": "2026-08-17T02:00:00Z",
            "status": "published",
        },
        "quality_gate": {"status": "passed", "passed": True},
        "signals": list(signals),
    }


class _Response:
    def __init__(self, payload: dict, *, status: int = 202):
        self.status = status
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


def _write_report(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_builds_analyst_review_contract_and_stable_per_signal_idempotency():
    report = _report(_signal())
    payload = dispatcher.build_alert_payload(
        report,
        report["signals"][0],
        "https://globalinfectiousdisease.com/situation/2026-08-17/",
    )

    assert payload["schema_version"] == "situation-alert.v1"
    assert payload["signal"] == {
        "signal_id": "signal-reviewed",
        "analysis_status": "analyzed",
        "anomaly_state": "strong",
        "signal_type": "statistical_signal",
        "temporal_relevance": "lagged",
        "data_status": "held_back",
        "completeness": 0.99,
        "q_value": 0.001,
        "model": "seasonal_robust_z_fallback_v1",
        "fit_status": "fallback_completed",
        "detector_tier": "common_count",
        "effect_threshold_passed": True,
        "verification_status": "verified",
        "verification_basis": "analyst_review",
        "verification_policy_version": None,
        "verified_by": "reviewer:17",
        "verified_at": "2026-08-17T01:55:00Z",
        "observed_at": "2026-08-16T00:00:00Z",
        "title": "Example disease — United States",
        "summary": (
            "Example disease in United States: observed 160 cases versus 105 "
            "expected (q=0.001); verified by analyst review."
        ),
        "countries": ["US"],
        "diseases": ["example-disease"],
        "evidence_urls": ["https://example.invalid/evidence"],
    }
    assert payload["idempotency_key"] == dispatcher.idempotency_key(
        "situation-v3-daily-2026-08-17-r1", "signal-reviewed"
    )
    assert payload["idempotency_key"] != dispatcher.idempotency_key(
        "situation-v3-daily-2026-08-17-r1", "another-signal"
    )


def test_production_payload_filter_blocks_guarded_auto_but_keeps_reviewed_signal():
    report = _report(
        _signal(signal_id="automatic", basis="automated_policy"),
        _signal(signal_id="reviewed"),
    )

    payloads, skipped, blocked = dispatcher.alert_payloads(
        report, "https://globalinfectiousdisease.com/situation/"
    )

    assert [payload["signal"]["signal_id"] for payload in payloads] == ["reviewed"]
    assert skipped == 0
    assert blocked == 1
    with pytest.raises(dispatcher.DispatchError, match="automated_policy_dispatch_disabled"):
        dispatcher.build_alert_payload(
            report,
            report["signals"][0],
            "https://globalinfectiousdisease.com/situation/",
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda signal: signal["assessment"].update(
                verification_policy_version="future-auto-v2"
            ),
            "guarded_auto_policy_required",
        ),
        (
            lambda signal: signal["anomaly"].update(
                model="seasonal_robust_z_fallback_v1"
            ),
            "guarded_auto_primary_fit_required",
        ),
        (
            lambda signal: signal["observation"].update(data_status="held_back"),
            "guarded_auto_current_signal_required",
        ),
    ],
)
def test_malformed_or_unknown_automatic_policy_fails_instead_of_silent_skip(
    mutation, error
):
    signal = _signal(basis="automated_policy")
    mutation(signal)
    with pytest.raises(dispatcher.DispatchError, match=error):
        dispatcher.alert_payloads(
            _report(signal), "https://globalinfectiousdisease.com/situation/"
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda signal: signal["assessment"].update(verification_basis="unknown"),
            "invalid_verification_basis",
        ),
        (
            lambda signal: signal["assessment"].update(verified_by="name@example.com"),
            "opaque_verified_by_required",
        ),
        (
            lambda signal: signal["assessment"].update(
                verification_policy_version="manual-v1"
            ),
            "analyst_review_policy_must_be_null",
        ),
        (
            lambda signal: signal["observation"].update(data_status="delayed"),
            "publishable_data_status_required",
        ),
        (lambda signal: signal.update(evidence_links=[]), "signal_evidence_url_required"),
    ],
)
def test_rejects_malformed_verified_analyst_signal(mutation, error):
    signal = _signal()
    mutation(signal)
    with pytest.raises(dispatcher.DispatchError, match=error):
        dispatcher.alert_payloads(
            _report(signal), "https://globalinfectiousdisease.com/situation/"
        )


def test_main_posts_each_reviewed_signal_with_bearer_and_timeout(tmp_path, capsys):
    report = _report(_signal(signal_id="one"), _signal(signal_id="two"))
    path = _write_report(tmp_path, report)
    calls = []

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return _Response({"ok": True, "duplicate": len(calls) == 2})

    secret = "unit-test-super-secret"
    result = dispatcher.main(
        ["--report", str(path), "--timeout-seconds", "7.5"],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": secret,
        },
        opener=opener,
    )

    assert result == 0
    assert len(calls) == 2
    assert all(timeout == 7.5 for _, timeout in calls)
    assert all(request.full_url.endswith("/api/internal/situation-alerts") for request, _ in calls)
    assert all(request.get_header("Authorization") == f"Bearer {secret}" for request, _ in calls)
    bodies = [json.loads(request.data) for request, _ in calls]
    assert {body["signal"]["signal_id"] for body in bodies} == {"one", "two"}
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert '"accepted": 2' in output.out
    assert '"duplicates": 1' in output.out


def test_main_does_not_make_request_for_guarded_auto(tmp_path, capsys):
    path = _write_report(tmp_path, _report(_signal(basis="automated_policy")))

    def opener(*_args, **_kwargs):
        raise AssertionError("automatic signal must not reach the network")

    result = dispatcher.main(
        ["--report", str(path)],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": "secret-value",
        },
        opener=opener,
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["eligible"] == 0
    assert output["blocked_automatic"] == 1


def test_missing_configuration_skips_unless_strict(capsys):
    assert dispatcher.main([], environment={}) == 0
    skipped = json.loads(capsys.readouterr().out)
    assert skipped["status"] == "skipped"
    assert dispatcher.main(["--strict-config"], environment={}) == 2
    failed = json.loads(capsys.readouterr().err)
    assert failed["status"] == "failed"


@pytest.mark.parametrize(
    "url",
    [
        "http://subscriptions.example.invalid",
        "https://user:secret@subscriptions.example.invalid",
        "https://subscriptions.example.invalid/other",
        "https://subscriptions.example.invalid?token=secret",
    ],
)
def test_worker_endpoint_requires_clean_https_origin(url):
    with pytest.raises(dispatcher.DispatchError):
        dispatcher.worker_endpoint(url)


def test_transport_failure_is_nonzero_and_never_prints_secret(tmp_path, capsys):
    path = _write_report(tmp_path, _report(_signal()))
    secret = "never-print-this-secret"

    def opener(*_args, **_kwargs):
        raise URLError(f"transport echoed {secret}")

    result = dispatcher.main(
        ["--report", str(path)],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": secret,
        },
        opener=opener,
        sleep=lambda _delay: None,
    )

    output = capsys.readouterr()
    assert result == 1
    assert secret not in output.out + output.err
    assert "worker_transport_error:URLError" in output.err


def test_transient_delivery_recovers_with_bounded_backoff(tmp_path, capsys):
    path = _write_report(tmp_path, _report(_signal()))
    calls = 0
    sleeps = []

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("temporary network failure")
        if calls == 2:
            raise HTTPError(request.full_url, 503, "unavailable", {}, None)
        return _Response({"ok": True, "duplicate": False})

    result = dispatcher.main(
        [
            "--report",
            str(path),
            "--max-attempts",
            "4",
            "--retry-base-seconds",
            "0.25",
            "--retry-max-seconds",
            "1",
        ],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": "secret-value",
        },
        opener=opener,
        sleep=sleeps.append,
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["accepted"] == 1
    assert output["delivery_attempts"] == 3
    assert output["retried"] == 2
    assert sleeps == [0.25, 0.5]


def test_permanent_worker_rejection_is_not_retried(tmp_path, capsys):
    path = _write_report(tmp_path, _report(_signal()))
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 422, "invalid", {}, None)

    result = dispatcher.main(
        ["--report", str(path), "--max-attempts", "8"],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": "secret-value",
        },
        opener=opener,
        sleep=lambda _delay: pytest.fail("422 must not retry"),
    )

    assert result == 1
    output = json.loads(capsys.readouterr().err)
    assert output["delivery_attempts"] == 1
    assert output["retried"] == 0
    assert output["failures"][0]["error"] == "worker_http_error:422"
    assert calls == 1


def test_contract_drift_fails_before_any_request(tmp_path):
    signal = deepcopy(_signal())
    signal["assessment"]["verification_basis"] = "future_automatic_policy"
    path = _write_report(tmp_path, _report(signal))
    called = False

    def opener(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Response({"ok": True})

    assert dispatcher.main(
        ["--report", str(path)],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": "secret-value",
        },
        opener=opener,
    ) == 2
    assert called is False


def test_invalid_empty_report_is_not_treated_as_success(tmp_path):
    report = _report()
    report["quality_gate"] = {"status": "failed", "passed": False}
    path = _write_report(tmp_path, report)

    assert dispatcher.main(
        ["--report", str(path)],
        environment={
            "SITUATION_ALERT_WORKER_URL": "https://subscriptions.example.invalid",
            "SITUATION_PUBLIC_REPORT_URL": "https://globalinfectiousdisease.com/situation/",
            "SITUATION_ALERT_INGEST_TOKEN": "secret-value",
        },
        opener=lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    ) == 2
