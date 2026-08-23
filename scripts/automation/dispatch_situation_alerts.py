#!/usr/bin/env python3
"""Dispatch verified signals from a published Situation report.

The dispatcher is intentionally a small, dependency-free boundary between the
static release and the subscription Worker.  Automated dispatch requires the
structured, fail-closed v3.2 policy decision; legacy guarded-auto signals stay
blocked. Secrets are read only from the environment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = (
    ROOT
    / "astro-site"
    / "dist"
    / "site-data"
    / "situation"
    / "v3"
    / "latest.json"
)
ALERT_ROUTE = "/api/internal/situation-alerts"
SCHEMA_VERSION = "situation-alert.v1"
AUTOMATION_POLICY = "tiered_auto_v3.2"
AUTOMATION_MODEL = "multi_horizon_gamma_poisson_v1"
QUALITY_GATE_STATUSES = {"passed", "degraded"}
ANOMALY_STATES = {"alert", "strong"}
SIGNAL_TYPES = {"statistical_signal", "officially_correlated_signal"}
TEMPORAL_RELEVANCE = {"current", "lagged"}
DATA_STATUSES = {"current", "held_back"}
DETECTOR_TIERS = {"common_count", "rare_count", "rate", "context_only"}
OPAQUE_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
TRUE_VALUES = {"1", "true", "yes", "on", "required", "strict", "force"}


class DispatchError(RuntimeError):
    """A safe-to-log dispatcher contract or delivery error."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _record(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DispatchError(f"{field}_object_required")
    return value


def _text(value: Any, field: str, *, maximum: int) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized:
        raise DispatchError(f"{field}_required")
    if len(normalized) > maximum:
        raise DispatchError(f"{field}_too_long")
    return normalized


def _number(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DispatchError(f"{field}_number_required")
    result = float(value)
    if not math.isfinite(result):
        raise DispatchError(f"{field}_must_be_finite")
    return result


def _unit_interval(value: Any, field: str, *, nullable: bool = False) -> float | None:
    result = _number(value, field, nullable=nullable)
    if result is not None and not 0 <= result <= 1:
        raise DispatchError(f"{field}_out_of_range")
    return result


def _iso_datetime(value: Any, field: str) -> str:
    text = _text(value, field, maximum=80)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise DispatchError(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        raise DispatchError(f"invalid_{field}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _observed_datetime(value: Any) -> str:
    text = _text(value, "observed_at", maximum=80)
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        else:
            candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                raise ValueError
    except ValueError as exc:
        raise DispatchError("invalid_observed_at") from exc
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _https_url(value: Any, field: str, *, origin_only: bool = False) -> str:
    text = _text(value, field, maximum=2048)
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DispatchError(f"{field}_https_required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DispatchError(f"invalid_{field}") from exc
    if origin_only and (parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise DispatchError(f"{field}_origin_required")
    netloc = parsed.hostname.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    path = "" if origin_only else parsed.path or "/"
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def worker_endpoint(worker_base_url: str) -> str:
    origin = _https_url(worker_base_url, "worker_base_url", origin_only=True).rstrip("/")
    return origin + ALERT_ROUTE


def load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError(f"report_not_found:{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError("report_unreadable_or_invalid_json") from exc
    return _record(value, "report")


def idempotency_key(report_id: str, signal_id: str) -> str:
    digest = hashlib.sha256(f"{report_id}\0{signal_id}".encode("utf-8")).hexdigest()
    return f"situation-alert-v1:{digest}"


def _truncate(value: str, maximum: int) -> str:
    value = " ".join(value.split())
    if len(value) <= maximum:
        return value
    return value[: maximum - 1].rstrip() + "…"


def _summary(
    identity: Mapping[str, Any],
    observation: Mapping[str, Any],
    anomaly: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> str:
    disease = str(identity.get("disease_name") or identity.get("disease_id") or "Disease")
    geography = str(
        identity.get("country_name")
        or identity.get("canonical_geography_key")
        or "reported geography"
    )
    metric = str(
        identity.get("metric_label")
        or identity.get("metric_type")
        or "surveillance metric"
    )
    current = _number(observation.get("current"), "current")
    expected = _number(observation.get("expected"), "expected", nullable=True)
    q_value = _unit_interval(anomaly.get("q_value"), "q_value", nullable=True)
    basis = str(assessment.get("verification_basis") or "")
    policy = assessment.get("verification_policy_version")
    comparison = f"observed {current:g} {metric}"
    if expected is not None:
        comparison += f" versus {expected:g} expected"
    if q_value is not None:
        comparison += f" (q={q_value:g})"
    verification = (
        f"automatically verified by {policy}"
        if basis == "automated_policy"
        else "verified by analyst review"
    )
    return _truncate(f"{disease} in {geography}: {comparison}; {verification}.", 1200)


def _country(identity: Mapping[str, Any]) -> list[str]:
    country = str(identity.get("country_code") or "").strip()
    if not country:
        canonical = str(identity.get("canonical_geography_key") or "")
        parts = canonical.split(":")
        if len(parts) >= 2 and parts[0] == "country":
            country = parts[1].strip()
    return [country.upper()] if country else []


def _disease(identity: Mapping[str, Any]) -> list[str]:
    disease = str(identity.get("disease_slug") or identity.get("disease_id") or "").strip()
    return [disease.lower()] if disease else []


def _evidence_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise DispatchError("evidence_links_array_required")
    urls: list[str] = []
    for item in value:
        link = _record(item, "evidence_link")
        url = _https_url(link.get("url"), "evidence_url")
        if url not in urls:
            urls.append(url)
    if not urls:
        raise DispatchError("signal_evidence_url_required")
    if len(urls) > 20:
        raise DispatchError("too_many_evidence_urls")
    return urls


def _validated_report_header(report: Mapping[str, Any], public_report_url: str) -> dict[str, str]:
    if report.get("schema_version") != "situation_room.v3":
        raise DispatchError("unsupported_situation_report_schema")
    if report.get("public_enabled") is not True:
        raise DispatchError("public_situation_report_required")
    report_meta = _record(report.get("report"), "report_metadata")
    report_id = _text(report_meta.get("report_id"), "report_id", maximum=160)
    if report_meta.get("status") != "published":
        raise DispatchError("published_report_required")
    as_of = _iso_datetime(report_meta.get("as_of"), "report_as_of")
    quality_gate = _record(report.get("quality_gate"), "quality_gate")
    gate_status = str(quality_gate.get("status") or "")
    if gate_status not in QUALITY_GATE_STATUSES or quality_gate.get("passed") is not True:
        raise DispatchError("publishable_quality_gate_required")
    return {
        "report_id": report_id,
        "as_of": as_of,
        "quality_gate_status": gate_status,
        "publication_status": "published",
        "public_url": _https_url(public_report_url, "public_report_url"),
    }


def build_alert_payload(
    report: Mapping[str, Any],
    signal: Mapping[str, Any],
    public_report_url: str,
) -> dict[str, Any]:
    report_header = _validated_report_header(report, public_report_url)
    report_id = report_header["report_id"]
    as_of = report_header["as_of"]

    identity = _record(signal.get("identity"), "signal_identity")
    observation = _record(signal.get("observation"), "signal_observation")
    anomaly = _record(signal.get("anomaly"), "signal_anomaly")
    assessment = _record(signal.get("assessment"), "signal_assessment")
    signal_id = _text(identity.get("signal_id"), "signal_id", maximum=200)
    anomaly_state = str(anomaly.get("state") or "")
    if anomaly_state not in ANOMALY_STATES:
        raise DispatchError("alert_or_strong_signal_required")
    signal_type = str(assessment.get("signal_type") or "")
    if signal_type not in SIGNAL_TYPES:
        raise DispatchError("invalid_signal_type")
    temporal_relevance = str(assessment.get("temporal_relevance") or "")
    if temporal_relevance not in TEMPORAL_RELEVANCE:
        raise DispatchError("current_or_lagged_signal_required")
    data_status = str(observation.get("data_status") or "")
    if data_status not in DATA_STATUSES:
        raise DispatchError("publishable_data_status_required")
    completeness = _unit_interval(observation.get("completeness"), "completeness")
    q_value = _unit_interval(anomaly.get("q_value"), "q_value", nullable=True)
    model = _text(anomaly.get("model"), "model", maximum=120)
    fit_status = _text(anomaly.get("fit_status"), "fit_status", maximum=80)
    detector_tier = str(anomaly.get("detector_tier") or "")
    if detector_tier not in DETECTOR_TIERS:
        raise DispatchError("invalid_detector_tier")
    effect_threshold_passed = anomaly.get("effect_threshold_passed")
    if not isinstance(effect_threshold_passed, bool):
        raise DispatchError("effect_threshold_status_required")
    if assessment.get("verification_status") != "verified":
        raise DispatchError("verified_signal_required")
    basis = str(assessment.get("verification_basis") or "")
    if basis not in {"automated_policy", "analyst_review"}:
        raise DispatchError("invalid_verification_basis")
    policy = assessment.get("verification_policy_version")
    if policy is not None and not isinstance(policy, str):
        raise DispatchError("invalid_verification_policy_version")
    verified_by = _text(assessment.get("verified_by"), "verified_by", maximum=120)
    if not OPAQUE_REVIEWER_ID.fullmatch(verified_by) or "@" in verified_by:
        raise DispatchError("opaque_verified_by_required")
    verified_at = _iso_datetime(assessment.get("verified_at"), "verified_at")
    verified_time = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    report_time = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if verified_time > report_time + timedelta(minutes=5):
        raise DispatchError("verification_after_report_as_of")
    evidence_urls = _evidence_urls(signal.get("evidence_links"))
    automation_payload: dict[str, Any] | None = None

    if basis == "automated_policy":
        if policy != AUTOMATION_POLICY:
            raise DispatchError("tiered_auto_policy_required")
        if verified_by != f"policy:{AUTOMATION_POLICY}":
            raise DispatchError("tiered_auto_verifier_required")
        if temporal_relevance != "current" or data_status != "current":
            raise DispatchError("tiered_auto_current_signal_required")
        if not effect_threshold_passed:
            raise DispatchError("tiered_auto_effect_threshold_required")
        if completeness is None or completeness < 0.95:
            raise DispatchError("tiered_auto_completeness_required")
        automation = _record(
            assessment.get("automation_decision"), "automation_decision"
        )
        if automation.get("status") != "auto_verified":
            raise DispatchError("automation_decision_not_verified")
        if automation.get("policy_version") != AUTOMATION_POLICY:
            raise DispatchError("automation_decision_policy_mismatch")
        _text(
            automation.get("calibration_hash"),
            "automation_calibration_hash",
            maximum=128,
        )
        if automation.get("gate_reasons") != []:
            raise DispatchError("automation_gate_reasons_must_be_empty")
        decided_at = _iso_datetime(
            automation.get("decided_at"), "automation_decided_at"
        )
        decision_time = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
        if decision_time > report_time + timedelta(minutes=5):
            raise DispatchError("automation_decision_after_report_as_of")
        automation_basis = str(automation.get("basis") or "")
        matched_event_ids = automation.get("matched_event_ids")
        if not isinstance(matched_event_ids, list) or not all(
            isinstance(event_id, str) and event_id.strip()
            for event_id in matched_event_ids
        ):
            raise DispatchError("invalid_automation_matched_event_ids")
        if automation_basis == "calibrated_statistical":
            if q_value is None or q_value > 0.025:
                raise DispatchError("calibrated_statistical_q_required")
            if (
                model != AUTOMATION_MODEL
                or fit_status != "completed"
                or detector_tier != "common_count"
            ):
                raise DispatchError("calibrated_statistical_primary_fit_required")
        elif automation_basis == "official_corroboration":
            if not matched_event_ids:
                raise DispatchError("official_corroboration_event_required")
            if q_value is None or q_value > 0.05:
                raise DispatchError("official_corroboration_review_q_required")
        else:
            raise DispatchError("invalid_automation_basis")
        automation_payload = {
            "status": "auto_verified",
            "basis": automation_basis,
            "policy_version": AUTOMATION_POLICY,
            "calibration_hash": str(automation["calibration_hash"]).strip(),
            "gate_reasons": [],
            "matched_event_ids": list(dict.fromkeys(matched_event_ids)),
            "decided_at": decided_at,
        }
    else:
        if policy is not None:
            raise DispatchError("analyst_review_policy_must_be_null")
        if verified_by.startswith("policy:"):
            raise DispatchError("analyst_reviewer_required")

    disease_name = str(identity.get("disease_name") or identity.get("disease_id") or "Disease")
    geography = str(
        identity.get("country_name")
        or identity.get("canonical_geography_key")
        or "reported geography"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "idempotency_key": idempotency_key(report_id, signal_id),
        "report": report_header,
        "signal": {
            "signal_id": signal_id,
            "analysis_status": "analyzed",
            "anomaly_state": anomaly_state,
            "signal_type": signal_type,
            "temporal_relevance": temporal_relevance,
            "data_status": data_status,
            "completeness": completeness,
            "q_value": q_value,
            "model": model,
            "fit_status": fit_status,
            "detector_tier": detector_tier,
            "effect_threshold_passed": effect_threshold_passed,
            "verification_status": "verified",
            "verification_basis": basis,
            "verification_policy_version": policy,
            "automation_decision": automation_payload,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "observed_at": _observed_datetime(observation.get("data_through")),
            "title": _truncate(f"{disease_name} — {geography}", 200),
            "summary": _summary(identity, observation, anomaly, assessment),
            "countries": _country(identity),
            "diseases": _disease(identity),
            "evidence_urls": evidence_urls,
        },
    }


def alert_payloads(
    report: Mapping[str, Any],
    public_report_url: str,
) -> tuple[list[dict[str, Any]], int, int]:
    # Validate publication state even when a report contains no eligible
    # signals. This prevents a malformed release from looking like a harmless
    # empty dispatch.
    _validated_report_header(report, public_report_url)
    signals = report.get("signals")
    if not isinstance(signals, list):
        raise DispatchError("signals_array_required")
    payloads: list[dict[str, Any]] = []
    skipped = 0
    blocked_automatic = 0
    seen: set[str] = set()
    for raw_signal in signals:
        signal = _record(raw_signal, "signal")
        assessment = _record(signal.get("assessment"), "signal_assessment")
        if assessment.get("verification_status") != "verified":
            skipped += 1
            continue
        try:
            payload = build_alert_payload(report, signal, public_report_url)
        except DispatchError as exc:
            if str(exc) != "automated_policy_dispatch_disabled":
                raise
            # The Worker understands this contract for forward compatibility,
            # but current calibration does not justify automatic publication
            # or email. Keep this filter upstream of all network activity.
            blocked_automatic += 1
            continue
        signal_id = str(payload["signal"]["signal_id"])
        if signal_id in seen:
            raise DispatchError("duplicate_signal_id")
        seen.add(signal_id)
        payloads.append(payload)
    return payloads, skipped, blocked_automatic


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    return int(status or 0)


def post_alert(
    endpoint: str,
    token: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    opener: Callable[..., Any] = urlopen,
) -> bool:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "globalid-situation-dispatch/1",
        },
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status = _response_status(response)
            response_body = response.read(64 * 1024)
    except HTTPError as exc:
        retryable = exc.code in {408, 425, 429} or exc.code >= 500
        raise DispatchError(
            f"worker_http_error:{exc.code}", retryable=retryable
        ) from exc
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        # Do not include the transport exception text: a custom client could
        # echo request headers and therefore the bearer token.
        raise DispatchError(
            f"worker_transport_error:{type(exc).__name__}", retryable=True
        ) from exc
    if status < 200 or status >= 300:
        retryable = status in {408, 425, 429} or status >= 500
        raise DispatchError(f"worker_http_error:{status}", retryable=retryable)
    try:
        result = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError("worker_invalid_json_response", retryable=True) from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DispatchError("worker_rejected_response")
    return result.get("duplicate") is True


def _strict_config(environment: Mapping[str, str], cli_strict: bool) -> bool:
    configured = str(
        environment.get("SITUATION_ALERT_DISPATCH_STRICT") or ""
    ).strip().lower()
    return cli_strict or configured in TRUE_VALUES


def dispatch(
    *,
    report_path: Path,
    worker_base_url: str,
    public_report_url: str,
    token: str,
    timeout_seconds: float,
    max_attempts: int = 3,
    retry_base_seconds: float = 1,
    retry_max_seconds: float = 10,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    endpoint = worker_endpoint(worker_base_url)
    if not token:
        raise DispatchError("ingest_token_required")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise DispatchError("timeout_seconds_out_of_range")
    if max_attempts < 1 or max_attempts > 8:
        raise DispatchError("max_attempts_out_of_range")
    for value, field, upper in (
        (retry_base_seconds, "retry_base_seconds", 60),
        (retry_max_seconds, "retry_max_seconds", 120),
    ):
        if not math.isfinite(value) or value < 0 or value > upper:
            raise DispatchError(f"{field}_out_of_range")
    if retry_max_seconds < retry_base_seconds:
        raise DispatchError("retry_max_seconds_below_base")
    report = load_report(report_path)
    payloads, skipped, blocked_automatic = alert_payloads(report, public_report_url)
    report_id = str(_record(report.get("report"), "report_metadata").get("report_id") or "")
    sent = 0
    duplicates = 0
    delivery_attempts = 0
    retried = 0
    failures: list[dict[str, str]] = []
    for payload in payloads:
        signal_id = str(payload["signal"]["signal_id"])
        for attempt in range(1, max_attempts + 1):
            delivery_attempts += 1
            try:
                duplicate = post_alert(
                    endpoint,
                    token,
                    payload,
                    timeout_seconds=timeout_seconds,
                    opener=opener,
                )
                sent += 1
                duplicates += int(duplicate)
                break
            except DispatchError as exc:
                if not exc.retryable or attempt == max_attempts:
                    failures.append({"signal_id": signal_id, "error": str(exc)})
                    break
                retried += 1
                delay = min(retry_max_seconds, retry_base_seconds * (2 ** (attempt - 1)))
                if delay:
                    sleep(delay)
    return {
        "status": "failed" if failures else "completed",
        "report_id": report_id,
        "eligible": len(payloads),
        "skipped_unverified": skipped,
        "blocked_automatic": blocked_automatic,
        "accepted": sent,
        "duplicates": duplicates,
        "delivery_attempts": delivery_attempts,
        "retried": retried,
        "failed": len(failures),
        "failures": failures,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--worker-base-url",
        default=None,
        help="HTTPS Worker origin; defaults to SITUATION_ALERT_WORKER_URL",
    )
    parser.add_argument(
        "--public-report-url",
        default=None,
        help="HTTPS public Situation URL; defaults to SITUATION_PUBLIC_REPORT_URL",
    )
    parser.add_argument(
        "--token-env",
        default="SITUATION_ALERT_INGEST_TOKEN",
        help="Name of the environment variable containing the bearer token",
    )
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--max-attempts", type=int, default=None)
    parser.add_argument("--retry-base-seconds", type=float, default=None)
    parser.add_argument("--retry-max-seconds", type=float, default=None)
    parser.add_argument(
        "--strict-config",
        action="store_true",
        help="Fail instead of skipping when dispatch settings are absent",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = parse_args(argv)
    env = os.environ if environment is None else environment
    strict = _strict_config(env, args.strict_config)
    token_env = str(args.token_env or "").strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", token_env):
        print(json.dumps({"status": "failed", "error": "invalid_token_env_name"}), file=sys.stderr)
        return 2
    worker_base_url = str(
        args.worker_base_url or env.get("SITUATION_ALERT_WORKER_URL") or ""
    ).strip()
    public_report_url = str(
        args.public_report_url or env.get("SITUATION_PUBLIC_REPORT_URL") or ""
    ).strip()
    token = str(env.get(token_env) or "").strip()
    missing = [
        name
        for name, value in (
            ("SITUATION_ALERT_WORKER_URL", worker_base_url),
            ("SITUATION_PUBLIC_REPORT_URL", public_report_url),
            (token_env, token),
        )
        if not value
    ]
    if missing:
        result = {
            "status": "failed" if strict else "skipped",
            "reason": "configuration_missing",
            "missing": missing,
        }
        print(json.dumps(result, sort_keys=True), file=sys.stderr if strict else sys.stdout)
        return 2 if strict else 0
    timeout = args.timeout_seconds
    if timeout is None:
        raw_timeout = str(env.get("SITUATION_ALERT_TIMEOUT_SECONDS") or "15").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            print(
                json.dumps({"status": "failed", "error": "invalid_timeout_seconds"}),
                file=sys.stderr,
            )
            return 2
    max_attempts = args.max_attempts
    if max_attempts is None:
        raw_attempts = str(env.get("SITUATION_ALERT_MAX_ATTEMPTS") or "3").strip()
        try:
            max_attempts = int(raw_attempts)
        except ValueError:
            print(
                json.dumps({"status": "failed", "error": "invalid_max_attempts"}),
                file=sys.stderr,
            )
            return 2

    def _float_setting(argument: float | None, environment_name: str, default: str) -> float:
        raw_value = argument if argument is not None else env.get(environment_name, default)
        try:
            return float(str(raw_value).strip())
        except ValueError as exc:
            raise DispatchError(f"invalid_{environment_name.lower()}") from exc

    try:
        retry_base_seconds = _float_setting(
            args.retry_base_seconds,
            "SITUATION_ALERT_RETRY_BASE_SECONDS",
            "1",
        )
        retry_max_seconds = _float_setting(
            args.retry_max_seconds,
            "SITUATION_ALERT_RETRY_MAX_SECONDS",
            "10",
        )
        result = dispatch(
            report_path=args.report,
            worker_base_url=worker_base_url,
            public_report_url=public_report_url,
            token=token,
            timeout_seconds=timeout,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
            opener=opener,
            sleep=sleep,
        )
    except DispatchError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    stream = sys.stderr if result["failed"] else sys.stdout
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stream)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
