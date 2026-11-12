"""Queue a weekly digest of newly published reports after a verified R2 release.

The first successful observation establishes a baseline and never sends the
existing catalogue. Later newly observed reports are attributed to their UTC
observation week. The durable, immutable weekly outbox contains public data
only; recipient and delivery state remain in the subscription Worker.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from scripts.automation import dispatch_research_digest as transport
from .research_subscription import _fingerprint, _outbox_lock, _save, _valid_week, completed_week, delivery_mode, outbox_directory, guarded_floor, verify_worker_mode


class ReportSubscriptionError(RuntimeError):
    release_stage = "report_digest_dispatch"


def _load_json(path: Path, reason: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportSubscriptionError(reason) from exc


def _get(url: str, opener: Callable[..., Any], *, limit: int) -> bytes:
    req = Request(url, headers={
        "Cache-Control": "no-cache", "Accept-Encoding": "identity",
        "User-Agent": "GIDS-Release-Verifier/1.0",
    })
    try:
        with opener(req, timeout=30) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            data = response.read(limit + 1)
        if status != 200 or len(data) > limit:
            raise ValueError("invalid_response")
        return data
    except Exception as exc:
        raise ReportSubscriptionError("report_public_verification_failed") from exc


def _manifest_index(manifest: Any, origin: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("reports_origin") != origin:
        raise ReportSubscriptionError("report_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > 100_000:
        raise ReportSubscriptionError("report_manifest_invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ReportSubscriptionError("report_manifest_invalid")
        path, sha = item.get("path"), item.get("sha256")
        if not isinstance(path, str) or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ReportSubscriptionError("report_manifest_invalid")
        if path.startswith("/") or ".." in path.split("/") or path in result:
            raise ReportSubscriptionError("report_manifest_invalid")
        result[path] = item
    return result


def _verified_catalogue(reports_dir: Path, origin: str, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    local = _manifest_index(_load_json(reports_dir / "reports-manifest.json", "report_manifest_invalid"), origin)
    try:
        remote = _manifest_index(json.loads(_get(origin + "/reports-manifest.json", opener, limit=10_000_000)), origin)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportSubscriptionError("report_public_verification_failed") from exc
    key = "site-data/reports/index.json"
    index = reports_dir / key
    if key not in local or key not in remote or local[key]["sha256"] != remote[key]["sha256"]:
        raise ReportSubscriptionError("report_public_manifest_mismatch")
    try:
        data = index.read_bytes()
    except OSError as exc:
        raise ReportSubscriptionError("report_index_unavailable") from exc
    if hashlib.sha256(data).hexdigest() != local[key]["sha256"]:
        raise ReportSubscriptionError("report_index_manifest_mismatch")
    public_index = _get(origin + "/site-data/reports/index.json", opener, limit=10_000_000)
    if hashlib.sha256(public_index).hexdigest() != local[key]["sha256"]:
        raise ReportSubscriptionError("report_public_index_mismatch")
    try:
        rows = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportSubscriptionError("report_index_invalid") from exc
    if not isinstance(rows, list) or len(rows) > 100_000 or not all(isinstance(row, dict) for row in rows):
        raise ReportSubscriptionError("report_index_invalid")
    return rows, {path: item for path, item in local.items()
                  if path in remote and item["sha256"] == remote[path]["sha256"]}


def _report(row: Mapping[str, Any], origin: str) -> dict[str, Any] | None:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    gate = row.get("quality_gate") if isinstance(row.get("quality_gate"), dict) else meta.get("quality_gate")
    review = meta.get("peer_review")
    if (row.get("status") != "APPROVED" or not isinstance(gate, dict) or gate.get("passed") is not True
            or not isinstance(review, dict) or review.get("status") != "published"
            or review.get("verdict") != "accept" or review.get("review_mode") != "cross_model"):
        return None
    report_id = str(row.get("id") or "")
    country = str(row.get("country_code") or "").upper()
    start, end = str(row.get("period_start") or ""), str(row.get("period_end") or "")
    if not re.fullmatch(r"[0-9]{1,12}", report_id) or not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", country):
        raise ReportSubscriptionError("report_identity_invalid")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise ReportSubscriptionError("report_period_invalid")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ReportSubscriptionError("report_period_invalid") from exc
    if start_date > end_date:
        raise ReportSubscriptionError("report_period_invalid")
    directory = meta.get("disease_directory") or []
    if not isinstance(directory, list):
        raise ReportSubscriptionError("report_diseases_invalid")
    diseases: list[str] = []
    for entry in directory:
        if not isinstance(entry, dict):
            raise ReportSubscriptionError("report_diseases_invalid")
        slug = str(entry.get("slug") or "").strip().lower()
        if slug and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", slug):
            raise ReportSubscriptionError("report_diseases_invalid")
        if slug and slug not in diseases:
            diseases.append(slug)
    if len(diseases) > 190:
        raise ReportSubscriptionError("report_diseases_limit_exceeded")
    path = f"countries/{country.lower()}/reports/{report_id}/index.html"
    return {"id": report_id, "country": country, "period_start": start, "period_end": end,
            "diseases": diseases, "path": path, "url": f"{origin}/countries/{country.lower()}/reports/{report_id}/"}


def _undo_cloudflare_html_transforms(body: bytes) -> bytes:
    """Reverse only Cloudflare's known email, beacon, and JS-detection rewrites.

    These are added by the public zone after R2 serves the object; all other
    changes remain subject to the exact published manifest hash comparison.
    """
    def restore_email(match: re.Match[bytes]) -> bytes:
        encoded = bytes.fromhex(match.group(1).decode("ascii"))
        decoded = bytes(value ^ encoded[0] for value in encoded[1:])
        if not decoded or any(value < 33 or value > 126 or value in b'"<>' for value in decoded):
            raise ReportSubscriptionError("report_public_page_transform_invalid")
        return b'href="mailto:' + decoded + b'"'

    body = re.sub(rb'href="/cdn-cgi/l/email-protection#((?:[0-9a-fA-F]{2}){2,512})"', restore_email, body)
    body = re.sub(rb'<script data-cfasync="false" src="/cdn-cgi/scripts/[a-f0-9]{8}/cloudflare-static/email-decode\.min\.js"></script>', b'', body)
    # Do not strip arbitrary attributes from the live page: an injected onload,
    # extra script or unexpected beacon format must fail the manifest check.
    beacon = (rb'<script type="module" src="https://static\.cloudflareinsights\.com/beacon\.min\.js/v[a-z0-9]+" '
              rb'integrity="sha512-[A-Za-z0-9+/=]{80,100}" '
              rb'data-cf-beacon=\'\{"version":"[0-9.]+","token":"[a-f0-9]{32}","r":[0-9]+,"spa":[0-9]+\}\' '
              rb'crossorigin="anonymous"></script>\n?')
    body = re.sub(beacon, b'', body)
    # Match the exact Cloudflare JS-detection bootstrap, allowing only its
    # documented dynamic ray identifier and base64 timestamp to vary. Merely
    # containing Cloudflare substrings is insufficient: otherwise malicious
    # code inserted in the same script could disappear before hash checking.
    prefix = b'''<script>(function(){function c(){var b=a.contentDocument||(a.contentWindow&&a.contentWindow.document);if(b){var d=b.createElement('script');d.innerHTML="window.__CF$cv$params={r:' '''.rstrip()
    middle = b''',t:' '''.strip()
    after_timestamp = b'''};var l=window.parent.location,o;try{o=new URL(l.origin).origin}catch(e){try{o=new URL(new URL(l.href).origin).origin}catch(e){o=''}}var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js?ray='''
    suffix = b''',a=document.createElement('script');a.src=o?new URL(u,o).href:u;document.getElementsByTagName('head')[0].appendChild(a);";b.getElementsByTagName('head')[0].appendChild(d)}}if(document.body){var a=document.createElement('iframe');a.height=1;a.width=1;a.style.position='absolute';a.style.top=0;a.style.left=0;a.style.border='none';a.style.visibility='hidden';document.body.appendChild(a);if('loading'!==document.readyState)c();else if(window.addEventListener)document.addEventListener('DOMContentLoaded',c);else{var e=document.onreadystatechange||function(){};document.onreadystatechange=function(b){e(b);'loading'!==document.readyState&&(document.onreadystatechange=e,c())}}}})();</script>'''
    # Quotes around the dynamic values are deliberately part of the static
    # pieces to prevent extra JS from being smuggled through a variable field.
    challenge = (re.escape(prefix) + rb'([a-f0-9]{16,32})' + re.escape(b"'") + re.escape(middle)
                 + rb'[A-Za-z0-9+/=]{4,100}' + re.escape(b"'") + re.escape(after_timestamp)
                 + rb'\1' + re.escape(b"'") + re.escape(suffix))
    return re.sub(challenge, b'', body)


def _verify_pages(items: list[dict[str, Any]], reports_dir: Path, manifest: Mapping[str, Mapping[str, Any]], opener: Callable[..., Any]) -> None:
    for item in items:
        path = item["path"]
        entry = manifest.get(path)
        if not entry:
            raise ReportSubscriptionError("report_public_page_not_published")
        try:
            local = (reports_dir / path).read_bytes()
        except OSError as exc:
            raise ReportSubscriptionError("report_public_page_not_built") from exc
        if len(local) > 10_000_000 or hashlib.sha256(local).hexdigest() != entry["sha256"]:
            raise ReportSubscriptionError("report_public_page_manifest_mismatch")
        live = _get(item["url"], opener, limit=10_000_000)
        if hashlib.sha256(_undo_cloudflare_html_transforms(live)).hexdigest() != entry["sha256"]:
            raise ReportSubscriptionError("report_public_page_manifest_mismatch")


def _payload(week: str, items: list[dict[str, Any]], list_code: str) -> dict[str, Any]:
    if not items or len(items) > 100:
        raise ReportSubscriptionError("report_digest_item_limit_exceeded")
    label_en = "Report updates" if list_code == "reports" else "Weekly digest"
    label_zh = "报告更新" if list_code == "reports" else "每周摘要"
    lines_en = [f"# GIDS {label_en} — {week}", "", "Newly published surveillance reports this week:", ""]
    lines_zh = [f"# GIDS {label_zh} — {week}", "", "本周新发布的监测报告：", ""]
    for item in items:
        label = f"{item['country']} · {item['period_start']} – {item['period_end']}"
        lines_en.append(f"- [{label}]({item['url']})")
        lines_zh.append(f"- [{label}]({item['url']})")
    def item_content(item: Mapping[str, Any]) -> str:
        label = f"{item['country']} · {item['period_start']} – {item['period_end']}"
        return f"- [{label}]({item['url']})"
    return {
        "idempotency_key": f"reports-weekly:{week}:{list_code}:r1", "list_codes": [list_code],
        "frequency": "weekly", "default_locale": "en", "source_locale": "en", "target_locales": ["en", "zh"],
        "contents": {
            "en": {"subject": f"GIDS {label_en} — {week}", "markdown": "\n".join(lines_en)},
            "zh": {"subject": f"GIDS {label_zh} — {week}", "markdown": "\n".join(lines_zh)},
        },
        "personalized_templates": {
            "en": "\n".join(lines_en[:3] + ["", "{{GIDS_PERSONALIZED_ITEMS}}"]),
            "zh": "\n".join(lines_zh[:3] + ["", "{{GIDS_PERSONALIZED_ITEMS}}"]),
        },
        "content_items": [{"id": item["id"], "countries": [item["country"]], "diseases": item["diseases"],
                           "localized_markdown": {"en": item_content(item), "zh": item_content(item)}} for item in items],
        "created_by": "report-release-dispatcher", "max_recipients": 10000,
    }


def dispatch_after_release(*, root: Path, reports_dir: Path, worker_base_url: str, reports_base_url: str,
                           token: str, start_week: str, now: datetime | None = None,
                           opener: Callable[..., Any] = urlopen, mode: str = "full", guard_activation: bool = False,
                           integrated_week_start: str = "") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ReportSubscriptionError("timezone_required")
    now = now.astimezone(timezone.utc)
    if not _valid_week(start_week):
        raise ReportSubscriptionError("report_dispatch_start_week_required")
    if integrated_week_start and not _valid_week(integrated_week_start):
        raise ReportSubscriptionError("integrated_week_start_invalid")
    if not token or not worker_base_url:
        raise ReportSubscriptionError("report_dispatch_configuration_missing")
    origin = transport._https_origin(reports_base_url, "reports_base_url")
    year, week, _ = now.isocalendar()
    this_week = f"{year:04d}-W{week:02d}"
    last_week = completed_week(now)
    if this_week < start_week:
        return {"status": "before_activation_week", "week": this_week}
    directory = outbox_directory(root, "reports", mode)
    with _outbox_lock(directory):
        if guard_activation:
            verify_worker_mode(worker_base_url, mode, opener)
        start_week = guarded_floor(directory, start_week, this_week, mode, guard_activation)
        rows, manifest = _verified_catalogue(reports_dir, origin, opener)
        catalogue: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = _report(row, origin)
            if item:
                if item["id"] in catalogue:
                    raise ReportSubscriptionError("report_id_duplicate")
                catalogue[item["id"]] = item
        state_path = directory / "observations.json"
        if not state_path.exists():
            _save(state_path, {"schema_version": 1, "baseline_at": now.isoformat(),
                               "observations": {key: {"week": "baseline"} for key in catalogue}})
            return {"status": "baseline_recorded", "baseline_count": len(catalogue)}
        state = _load_json(state_path, "report_observations_invalid")
        observations = state.get("observations") if isinstance(state, dict) and state.get("schema_version") == 1 else None
        if not isinstance(observations, dict):
            raise ReportSubscriptionError("report_observations_invalid")
        for report_id, item in catalogue.items():
            if report_id not in observations:
                observations[report_id] = {"week": this_week, "fingerprint": _fingerprint(item)}
        _save(state_path, state)
        eligible_weeks: set[str] = set()
        for observation in observations.values():
            if not isinstance(observation, dict):
                raise ReportSubscriptionError("report_observations_invalid")
            observed_week = observation.get("week")
            if observed_week == "baseline":
                continue
            if not isinstance(observed_week, str) or not _valid_week(observed_week):
                raise ReportSubscriptionError("report_observations_invalid")
            if start_week <= observed_week <= last_week:
                eligible_weeks.add(observed_week)
        completed: list[str] = []
        for eligible_week in sorted(eligible_weeks):
            path = directory / f"{eligible_week}.json"
            ids = sorted(key for key, value in observations.items() if value.get("week") == eligible_week)
            if path.exists():
                record = _load_json(path, "report_dispatch_outbox_invalid")
                if not isinstance(record, dict) or record.get("week") != eligible_week or record.get("status") not in {"pending", "completed"}:
                    raise ReportSubscriptionError("report_dispatch_outbox_invalid")
                if record["status"] == "completed":
                    continue
            else:
                record = None
            integrated_owns_week = bool(integrated_week_start and eligible_week >= integrated_week_start)
            # A pre-existing outbox may already have reached the Worker, even
            # when its local completed_lists does not record an acknowledgement.
            # Require reconciliation before changing its recipients or payload.
            if integrated_owns_week and record and "weekly_digest" in record.get("payloads", {}):
                raise ReportSubscriptionError("weekly_digest_cutover_pending_requires_reconciliation")
            if any(report_id not in catalogue or observations[report_id].get("fingerprint") != _fingerprint(catalogue[report_id]) for report_id in ids):
                raise ReportSubscriptionError("report_changed_or_removed_requires_review")
            items = [catalogue[report_id] for report_id in ids]
            _verify_pages(items, reports_dir, manifest, opener)
            codes = ("reports",) if integrated_owns_week else ("reports", "weekly_digest")
            payloads = {code: _payload(eligible_week, items, code) for code in codes}
            if mode == "pilot":
                for payload in payloads.values():
                    payload["idempotency_key"] = "pilot:" + payload["idempotency_key"]
            fingerprint = _fingerprint(payloads)
            if record:
                previous = record.get("payloads")
                if isinstance(previous, dict) and all(isinstance(value, dict) and "personalized_templates" not in value for value in previous.values()):
                    legacy = {}
                    for code, payload in payloads.items():
                        old = dict(payload)
                        old.pop("personalized_templates", None)
                        old["content_items"] = [{key: value for key, value in item.items() if key != "localized_markdown"}
                                                for item in payload["content_items"]]
                        legacy[code] = old
                    fingerprint = _fingerprint(legacy)
                if record.get("payload_sha256") != fingerprint or _fingerprint(previous or {}) != fingerprint:
                    raise ReportSubscriptionError("report_dispatch_content_changed_requires_review")
            if not record:
                record = {"week": eligible_week, "payloads": payloads, "payload_sha256": fingerprint, "status": "pending", "completed_lists": []}
                _save(path, record)
            if not isinstance(record.get("completed_lists"), list) or any(code not in payloads for code in record["completed_lists"]):
                raise ReportSubscriptionError("report_dispatch_outbox_invalid")
            for code in payloads:
                if code in record["completed_lists"]:
                    continue
                try:
                    response = transport.queue_campaign(worker_base_url, token, record["payloads"][code], timeout_seconds=15, opener=opener)
                    safe = transport._safe_campaign_result(response, source_count=len(items))
                    status = safe["status"]
                    progress = (response.get("campaign") or {}).get("progress") or {}
                    if safe["progress"]["queued"]:
                        processed = transport.process_campaign(worker_base_url, token, safe["campaign_id"],
                            batch_size=5, max_batches=500, timeout_seconds=120, opener=opener)
                        status, progress = str(processed.get("status") or "unknown"), processed.get("progress") or {}
                    if status != "sent" or any(int(progress.get(field) or 0) for field in ("queued", "sending", "deferred", "failed", "unknown")):
                        raise ReportSubscriptionError("report_campaign_incomplete_or_failed")
                except transport.DispatchError as exc:
                    raise ReportSubscriptionError(str(exc)) from exc
                record["completed_lists"].append(code)
                _save(path, record)
            record["status"] = "completed"
            _save(path, record)
            completed.append(eligible_week)
        return {"status": "completed" if completed else "no_completed_reports", "completed_weeks": completed}


async def dispatch_if_enabled(service: Any, runtime: Any, *, reports_origin: str) -> dict[str, Any] | None:
    """Observe reports only after the release has successfully published them."""
    env_value = getattr(service, "_env_value", lambda _name, default="": default)
    if env_value("SUBSCRIPTIONS__REPORT_DIGEST_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        return await asyncio.to_thread(
            dispatch_after_release,
            root=runtime.root_dir,
            reports_dir=service._reports_site_dir(),
            worker_base_url=env_value("SUBSCRIPTIONS__PUBLIC_BASE_URL"),
            reports_base_url=reports_origin,
            token=env_value("SUBSCRIPTIONS__ADMIN_API_TOKEN"),
            start_week=env_value("SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK"),
            integrated_week_start=env_value("SUBSCRIPTIONS__INTEGRATED_WEEKLY_START_WEEK"),
            mode=delivery_mode(env_value),
            guard_activation=True,
        )
    except Exception as exc:
        # The report release has already been published. Keep it successful,
        # preserve the durable outbox for the next run, and expose a bounded,
        # non-sensitive failure in the release result and structured log.
        code = str(exc) if isinstance(exc, ReportSubscriptionError) else "internal_error"
        logging.getLogger(__name__).error(json.dumps({
            "event": "report_subscription_dispatch_failed", "error_code": code,
            "error_type": type(exc).__name__,
        }))
        return {"status": "failed", "error_code": code}
