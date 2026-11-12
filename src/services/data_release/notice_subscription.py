"""Publish newly released, accepted official notices to the subscription outbox."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.automation import dispatch_research_digest as transport
from .research_subscription import ResearchSubscriptionError, _fingerprint, _outbox_lock, _save, _verify_built_artifact, _verify_live_release, outbox_directory, guarded_floor, verify_worker_mode
from .surveillance_publish import validate_manifest


class NoticeSubscriptionError(RuntimeError):
    release_stage = "notice_alert_dispatch"


def _published_outbreaks(root: Path, origin: str, opener: Callable[..., Any]) -> tuple[list[dict], bytes]:
    """Read objects named by the public pointer, never an unpublished local export."""
    body = _situation_pointer(origin, opener)
    try:
        manifest, _ = validate_manifest(body)
        objects = root / "exports" / "situation-data" / "objects"
        result = []
        for logical, entry in manifest["files"].items():
            if not logical.startswith("outbreaks/"):
                continue
            path = objects / Path(entry["url"]).name
            if path.is_symlink() or not path.is_file():
                raise ValueError("missing object")
            data = path.read_bytes()
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("object checksum mismatch")
            value = json.loads(data)
            if not isinstance(value, dict) or value.get("schema_version") != "gids.situation_alerts.v1" or not isinstance(value.get("alerts"), list):
                raise ValueError("invalid outbreak")
            result.append(value)
        return result, body
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise NoticeSubscriptionError("situation_export_invalid") from exc


def _situation_pointer(origin: str, opener: Callable[..., Any]) -> bytes:
    try:
        request = Request(origin + "/site-data/situation/current.json", headers={
            "Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "GIDS-Release-Verifier/1.0",
        })
        with opener(request, timeout=15) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            body = response.read(2 * 1024 * 1024 + 1)
        if status != 200 or len(body) > 2 * 1024 * 1024:
            raise ValueError("invalid public pointer")
        return body
    except Exception as exc:
        raise NoticeSubscriptionError("situation_export_unavailable") from exc


def _capability(worker: str, opener: Callable[..., Any]) -> None:
    request = Request(transport._endpoint(worker, "/health"), headers={
        "Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "GIDS-Release-Verifier/1.0",
    })
    try:
        with opener(request, timeout=15) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            raw = response.read(65537)
        if status != 200 or len(raw) > 65536:
            raise ValueError("invalid_health")
        health = json.loads(raw)
        capabilities = health.get("capabilities") if isinstance(health, dict) else None
        if not isinstance(capabilities, dict) or capabilities.get("official_notice_alert_v1") is not True:
            raise ValueError("unsupported_capability")
    except Exception as exc:
        raise NoticeSubscriptionError("worker_official_notice_capability_required") from exc


def _event(alert: Mapping[str, Any], *, release_id: str, origin: str, mode: str = "full") -> dict[str, Any]:
    alert_id = str(alert.get("alert_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,129}", alert_id):
        raise NoticeSubscriptionError("invalid_official_alert_id")
    country = str(alert.get("country_code") or "").strip().upper()
    disease = str(alert.get("disease_slug") or "").strip().lower()
    result = {
        "schema_version": "official-notice-alert.v1", "idempotency_key": f"official-notice:{alert_id}:r1",
        "release_id": release_id, "public_url": origin + "/situation/",
        "alert_id": alert_id, "notice_id": str(alert.get("notice_id") or "").strip(),
        "outbreak_id": str(alert.get("outbreak_id") or "").strip(),
        "notice_status": str(alert.get("status") or "").strip(),
        "published_date": str(alert.get("published_date") or "").strip(),
        "source_url": str(alert.get("url") or "").strip(),
        "title": str(alert.get("title_en") or "").strip(),
        "summary_en": str(alert.get("summary_en") or "").strip(),
        "summary_zh": str(alert.get("summary_zh") or "").strip(),
        "countries": [country] if country else [], "diseases": [disease] if disease else [],
        "review_verdict": "accept", "review_mode": "cross_model", "publication_status": "published",
    }
    if mode == "pilot":
        result["idempotency_key"] = "pilot:" + result["idempotency_key"]
    for name, limit in (("release_id", 160), ("notice_id", 300), ("outbreak_id", 160),
                        ("title", 200), ("summary_en", 1200), ("summary_zh", 1200)):
        if not result[name] or len(str(result[name])) > limit:
            raise NoticeSubscriptionError(f"invalid_official_{name}")
    if result["notice_status"] not in {"new", "imported", "ongoing", "ended"}:
        raise NoticeSubscriptionError("invalid_official_notice_status")
    for item in (country, disease):
        if item and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", item):
            raise NoticeSubscriptionError("invalid_official_filter")
    source_url = urlsplit(result["source_url"])
    if source_url.scheme != "https" or not source_url.hostname or source_url.username or source_url.password:
        raise NoticeSubscriptionError("invalid_official_source_url")
    return result


def dispatch_after_release(
    *, root: Path, astro_dir: Path, identity: Mapping[str, Any], worker_base_url: str,
    public_base_url: str, token: str, start_date: str, now: datetime | None = None,
    opener: Callable[..., Any] = urlopen, mode: str = "full", guard_activation: bool = False,
) -> dict[str, Any]:
    """Stage events before sending; old history is never automatically replayed.

    The floor is a conscious activation date. Each release admits only notices
    dated within the last seven days; older pending outbox records can still be
    retried if the same public record remains in the deployed release.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise NoticeSubscriptionError("timezone_required")
    today = now.astimezone(timezone.utc).date()
    try:
        floor = date.fromisoformat(start_date)
    except ValueError as exc:
        raise NoticeSubscriptionError("notice_dispatch_start_date_required") from exc
    if floor.isoformat() != start_date:
        raise NoticeSubscriptionError("notice_dispatch_start_date_required")
    if not token or not worker_base_url:
        raise NoticeSubscriptionError("notice_dispatch_configuration_missing")
    if floor > today:
        return {"status": "before_activation_date", "accepted": 0}
    origin = transport._https_origin(public_base_url, "public_base_url")
    if not (astro_dir / "dist" / "situation" / "index.html").is_file():
        raise NoticeSubscriptionError("situation_public_page_not_built")
    # Read the generated files in the exact deployed artifact. Source exports
    # may be regenerated while a release is uploading; matching release.json
    # alone does not prove that a later src/data snapshot was ever published.
    source = astro_dir / "dist" / "situation" / "outbreaks"
    pointer = None
    if source.is_dir():
        try:
            outbreaks = [json.loads(path.read_text(encoding="utf-8")) for path in source.glob("*.json")]
        except (OSError, ValueError) as exc:
            raise NoticeSubscriptionError("situation_export_invalid") from exc
    else:
        outbreaks, pointer = _published_outbreaks(root, origin, opener)
    directory = outbox_directory(root, "official-notices", mode)
    with _outbox_lock(directory):
        if guard_activation:
            verify_worker_mode(worker_base_url, mode, opener)
        floor = date.fromisoformat(guarded_floor(directory, start_date, (today + timedelta(days=1)).isoformat(), mode, guard_activation))
        if floor > today:
            return {"status": "before_activation_date", "accepted": 0}
        existing: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in directory.glob("*.json"):
            if path.name.startswith("."):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise NoticeSubscriptionError("notice_dispatch_outbox_unreadable") from exc
            if not isinstance(record, dict) or record.get("status") not in {"pending", "accepted"} or record.get("id") != path.stem:
                raise NoticeSubscriptionError("notice_dispatch_outbox_invalid")
            existing[path.stem] = (path, record)
        eligible: dict[str, Mapping[str, Any]] = {}
        for obj in outbreaks:
            if not isinstance(obj, dict) or obj.get("schema_version") != "gids.situation_alerts.v1" or not isinstance(obj.get("alerts"), list):
                raise NoticeSubscriptionError("situation_export_invalid")
            for alert in obj["alerts"]:
                if not isinstance(alert, dict):
                    raise NoticeSubscriptionError("situation_export_invalid")
                try:
                    published = date.fromisoformat(str(alert.get("published_date") or ""))
                except ValueError:
                    continue
                alert_id = str(alert.get("alert_id") or "")
                is_pending = alert_id in existing and existing[alert_id][1]["status"] == "pending"
                if not is_pending and not (max(floor, today - timedelta(days=7)) <= published <= today):
                    continue
                review = alert.get("ai")
                if (not isinstance(review, dict) or review.get("verdict") != "accept"
                        or review.get("review_mode") != "cross_model"
                        or alert.get("status") not in {"new", "imported", "ongoing", "ended"}):
                    continue
                if alert_id in eligible and eligible[alert_id] != alert:
                    raise NoticeSubscriptionError("duplicate_official_alert_conflict")
                eligible[alert_id] = alert
        new_ids = [key for key in eligible if key not in existing]
        if len(new_ids) > 100:
            raise NoticeSubscriptionError("notice_dispatch_new_event_limit_exceeded")
        pending_ids = [key for key, (_, rec) in existing.items() if rec["status"] == "pending"]
        if any(key not in eligible for key in pending_ids):
            raise NoticeSubscriptionError("pending_official_notice_no_longer_published")
        try:
            _verify_built_artifact(astro_dir, identity)
            _verify_live_release(origin, identity, opener=opener)
        except ResearchSubscriptionError as exc:
            raise NoticeSubscriptionError(str(exc)) from exc
        if pointer is not None and _situation_pointer(origin, opener) != pointer:
            raise NoticeSubscriptionError("situation_publication_changed")
        # An empty scan is meaningful only after verifying the exact public
        # release and, for independently published data, a stable pointer.
        if not new_ids and not pending_ids:
            return {"status": "no_new_notices", "accepted": 0}
        staged = {key: _event(eligible[key], release_id=str(identity["release_id"]), origin=origin, mode=mode)
                  for key in new_ids}
        for key in pending_ids:
            _, record = existing[key]
            original = record.get("payload")
            if not isinstance(original, dict) or record.get("payload_sha256") != _fingerprint(original):
                raise NoticeSubscriptionError("notice_dispatch_outbox_invalid")
            current = _event(eligible[key], release_id=str(original.get("release_id") or ""), origin=origin, mode=mode)
            if _fingerprint(current) != record["payload_sha256"]:
                raise NoticeSubscriptionError("official_notice_changed_requires_review")
        # Persist the full release set before dispatching any one event.
        for key in sorted(new_ids):
            payload = staged[key]
            record = {"id": key, "status": "pending", "payload": payload, "payload_sha256": _fingerprint(payload)}
            record_path = directory / f"{key}.json"
            _save(record_path, record)
            existing[key] = (record_path, record)
        _capability(worker_base_url, opener)
        accepted = 0
        for key in sorted(pending_ids + new_ids):
            path, record = existing[key]
            original = record.get("payload")
            if not isinstance(original, dict) or record.get("payload_sha256") != _fingerprint(original):
                raise NoticeSubscriptionError("notice_dispatch_outbox_invalid")
            try:
                transport._post_json(
                    transport._endpoint(worker_base_url, "/api/internal/situation-alerts"), token,
                    original, timeout_seconds=15, opener=opener,
                )
            except transport.DispatchError as exc:
                raise NoticeSubscriptionError(str(exc)) from exc
            record["status"] = "accepted"
            _save(path, record)
            accepted += 1
        return {"status": "accepted", "accepted": accepted}
