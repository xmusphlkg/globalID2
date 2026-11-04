"""Dispatch the last completed public Research Radar week after a site release.

The small on-disk outbox stores only public content and is fsync'd before the
Worker is contacted. A repeated release resumes the same immutable campaign.
The Worker owns recipients and individual delivery state.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterator, Mapping
from urllib.request import Request, urlopen

from scripts.automation import dispatch_research_digest as dispatcher


class ResearchSubscriptionError(RuntimeError):
    release_stage = "research_digest_dispatch"


def completed_week(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("timezone_required")
    today = now.astimezone(timezone.utc).date()
    previous_sunday = today - timedelta(days=today.weekday() + 1)
    year, week, _ = previous_sunday.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _valid_week(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-W\d{2}", value):
        return False
    try:
        date.fromisocalendar(int(value[:4]), int(value[6:]), 1)
        return True
    except ValueError:
        return False


@contextmanager
def _outbox_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / ".lock"
    with lock.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _save(path: Path, record: Mapping[str, Any]) -> None:
    data = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fd, temporary = tempfile.mkstemp(prefix=".research-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _verify_live_release(
    origin: str, identity: Mapping[str, Any], *, opener: Callable[..., Any],
) -> None:
    origin = dispatcher._https_origin(origin, "public_base_url")
    request = Request(origin + "/release.json", headers={
        "Accept": "application/json", "Cache-Control": "no-cache",
        "User-Agent": "GIDS-Release-Verifier/1.0",
    })
    try:
        with opener(request, timeout=15) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            raw = response.read(65537)
        if status != 200 or len(raw) > 65536:
            raise ValueError("invalid_manifest_response")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ValueError("invalid_manifest")
        for key in ("release_id", "build_digest", "source_commit", "deployment_branch"):
            if not identity.get(key) or manifest.get(key) != identity.get(key):
                raise ValueError("manifest_mismatch")
    except Exception as exc:
        raise ResearchSubscriptionError("public_release_verification_failed") from exc


def _payload_for_week(astro_dir: Path, week: str, public_base_url: str) -> dict[str, Any]:
    path = astro_dir / "src" / "data" / "research" / "weekly" / f"{week}.json"
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        for locale in ("", "zh"):
            page = astro_dir / "dist" / locale / "research" / "weekly" / week / "index.html"
            if not page.is_file():
                raise ResearchSubscriptionError("research_digest_public_page_not_built")
            html = page.read_text(encoding="utf-8")
            matches = re.findall(r'data-research-brief-sha256="([0-9a-f]{64})"', html)
            if len(matches) != 1 or matches[0] != digest:
                raise ResearchSubscriptionError("research_digest_build_data_mismatch")
        brief = json.loads(raw)
        if not isinstance(brief, dict):
            raise ResearchSubscriptionError("research_digest_brief_invalid")
        payload = dispatcher.build_campaign_payload(brief, public_base_url)
    except (OSError, ValueError) as exc:
        raise ResearchSubscriptionError("research_digest_brief_unreadable") from exc
    except dispatcher.DispatchError as exc:
        raise ResearchSubscriptionError(str(exc)) from exc
    if brief.get("week") != week:
        raise ResearchSubscriptionError("research_digest_week_mismatch")
    return payload


def _fingerprint(payload: Mapping[str, Any]) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _verify_built_artifact(astro_dir: Path, identity: Mapping[str, Any]) -> None:
    """Ensure local deployable bytes still match the verified release identity."""
    from .pipeline import _built_site_digest

    expected = str(identity.get("build_digest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ResearchSubscriptionError("release_build_digest_invalid")
    try:
        actual = _built_site_digest(astro_dir / "dist")
    except (OSError, RuntimeError) as exc:
        raise ResearchSubscriptionError("release_build_unreadable") from exc
    if actual != expected:
        raise ResearchSubscriptionError("release_build_digest_mismatch")


def delivery_mode(env: Callable[..., str]) -> str:
    """Default to the restricted pilot; reject unrecognized modes."""
    mode = env("SUBSCRIPTIONS__DELIVERY_MODE", "pilot").strip().lower()
    if mode not in {"pilot", "full"}:
        raise ResearchSubscriptionError("subscription_delivery_mode_invalid")
    return mode


def outbox_directory(root: Path, stream: str, mode: str) -> Path:
    if mode not in {"pilot", "full"}:
        raise ResearchSubscriptionError("subscription_delivery_mode_invalid")
    base = root / "exports" / "subscription-outbox"
    return base / mode / stream


def guarded_floor(directory: Path, requested: str, minimum: str, mode: str, guard: bool) -> str:
    """Pin full rollout to its first activation, never replay the pilot period."""
    if mode != "full" or not guard:
        return requested
    path = directory / ".activation.json"
    if path.exists():
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
            floor = marker["floor"]
            if not isinstance(floor, str) or not floor:
                raise ValueError("invalid floor")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ResearchSubscriptionError("subscription_activation_marker_invalid") from exc
        return max(requested, floor)
    floor = max(requested, minimum)
    _save(path, {"floor": floor})
    return floor


def verify_worker_mode(worker: str, expected: str, opener: Callable[..., Any]) -> None:
    request = Request(dispatcher._endpoint(worker, "/health"), headers={
        "Accept": "application/json", "Cache-Control": "no-cache",
        "User-Agent": "GIDS-Release-Verifier/1.0",
    })
    try:
        with opener(request, timeout=15) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            raw = response.read(65537)
        if status != 200 or len(raw) > 65536 or json.loads(raw).get("delivery_mode") != expected:
            raise ValueError("worker_mode_mismatch")
    except Exception as exc:
        raise ResearchSubscriptionError("subscription_worker_mode_mismatch") from exc


def dispatch_after_release(
    *, root: Path, astro_dir: Path, identity: Mapping[str, Any],
    worker_base_url: str, public_base_url: str, token: str, start_week: str,
    now: datetime | None = None, opener: Callable[..., Any] = urlopen, mode: str = "full", guard_activation: bool = False,
) -> dict[str, Any]:
    """Queue/process only a completed week at or after the explicit activation floor.

    A failed send can be resumed by re-running the same release; creation uses
    the Worker's campaign idempotency key. This outbox is per persistent host;
    multiple independent machines need shared storage/coordination instead.
    """
    now = now or datetime.now(timezone.utc)
    if not _valid_week(start_week):
        raise ResearchSubscriptionError("research_dispatch_start_week_required")
    if not token or not worker_base_url:
        raise ResearchSubscriptionError("research_dispatch_configuration_missing")
    week = completed_week(now)
    if week < start_week:
        return {"status": "before_activation_week", "week": week}
    directory = outbox_directory(root, "research", mode)
    with _outbox_lock(directory):
        if guard_activation:
            verify_worker_mode(worker_base_url, mode, opener)
        year, current_week, _ = now.astimezone(timezone.utc).isocalendar()
        start_week = guarded_floor(directory, start_week, f"{year:04d}-W{current_week:02d}", mode, guard_activation)
        if week < start_week:
            return {"status": "before_activation_week", "week": week}
        records: dict[str, tuple[Path, dict[str, Any]]] = {}
        for record_path in sorted(directory.glob("*.json")):
            record_week = record_path.stem
            if not _valid_week(record_week) or not (start_week <= record_week <= week):
                continue
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ResearchSubscriptionError("research_dispatch_outbox_unreadable") from exc
            if not isinstance(record, dict) or record.get("week") != record_week or record.get("status") not in {"pending", "completed"}:
                raise ResearchSubscriptionError("research_dispatch_outbox_invalid")
            records[record_week] = (record_path, record)
        pending = [item for item, (_, record) in records.items() if record["status"] == "pending"]
        source_path = astro_dir / "src" / "data" / "research" / "weekly" / f"{week}.json"
        built_page = astro_dir / "dist" / "research" / "weekly" / week / "index.html"
        if week not in records and built_page.is_file() and not source_path.is_file():
            raise ResearchSubscriptionError("research_digest_brief_unreadable")
        if week not in records and source_path.is_file():
            pending.append(week)
        if not pending:
            return {"status": "already_completed" if week in records else "no_completed_week_brief", "week": week}
        _verify_built_artifact(astro_dir, identity)
        _verify_live_release(public_base_url, identity, opener=opener)
        completed: list[str] = []
        for pending_week in sorted(pending):
            payload = _payload_for_week(astro_dir, pending_week, public_base_url)
            if mode == "pilot":
                payload["idempotency_key"] = "pilot:" + payload["idempotency_key"]
            fingerprint = _fingerprint(payload)
            if pending_week in records:
                record_path, record = records[pending_week]
                if record.get("payload_sha256") != fingerprint or _fingerprint(record.get("payload") or {}) != fingerprint:
                    raise ResearchSubscriptionError("research_dispatch_content_changed_requires_review")
            else:
                record_path = directory / f"{pending_week}.json"
                record = {"week": pending_week, "payload": payload, "payload_sha256": fingerprint,
                          "release_id": identity["release_id"], "status": "pending"}
                _save(record_path, record)
            try:
                result = dispatcher.queue_campaign(worker_base_url, token, record["payload"],
                                                   timeout_seconds=15, opener=opener)
                safe = dispatcher._safe_campaign_result(result, source_count=len(payload["content_items"]))
                latest_status = safe["status"]
                latest_progress = (result.get("campaign") or {}).get("progress") or {}
                if safe["progress"]["queued"]:
                    processed = dispatcher.process_campaign(worker_base_url, token, safe["campaign_id"],
                        batch_size=20, max_batches=500, timeout_seconds=120, opener=opener)
                    latest_status = str(processed.get("status") or "unknown")
                    latest_progress = processed.get("progress") or {}
                if latest_status != "sent" or any(int(latest_progress.get(field) or 0) for field in (
                    "queued", "sending", "deferred", "failed", "unknown",
                )):
                    raise ResearchSubscriptionError("research_campaign_incomplete_or_failed")
            except dispatcher.DispatchError as exc:
                raise ResearchSubscriptionError(str(exc)) from exc
            record["status"] = "completed"
            record["campaign_id"] = safe["campaign_id"]
            _save(record_path, record)
            completed.append(pending_week)
        return {"status": "completed", "week": week, "completed_weeks": completed}
