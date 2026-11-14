"""Read-only queue and public-pointer monitoring for realtime data channels."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import re
import time
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from src.core.country_library import get_country_bootstrap_config
from src.core.config import get_config
from src.domain import Country, Task, TaskStatus, TaskType
from src.domain.collection import PublishDirty
from .planning import plan_changes
from .surveillance_publish import validate_manifest

CHANNELS = ("surveillance", "situation")
DATABASE_CHANNELS = (*CHANNELS, "literature")
ACTIVE = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)
LEVEL = {"ok": 0, "warning": 1, "critical": 2}
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class MonitorThresholds:
    pending_warning_seconds: float = 120
    pending_critical_seconds: float = 600
    running_warning_seconds: float = 600
    running_critical_seconds: float = 1800
    publication_grace_seconds: float = 60

    def __post_init__(self):
        values = vars(self)
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("invalid_monitor_thresholds")
        if self.pending_warning_seconds > self.pending_critical_seconds or self.running_warning_seconds > self.running_critical_seconds:
            raise ValueError("invalid_monitor_threshold_order")


def timestamp(value) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def iso(value) -> str | None:
    parsed = timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def age(value, now: datetime) -> float | None:
    parsed = timestamp(value)
    return round(max(0, (now - parsed).total_seconds()), 3) if parsed is not None else None


def _identifier(value) -> str | None:
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def _digest(value) -> str | None:
    return value if isinstance(value, str) and SHA256.fullmatch(value) else None


def task_channel_clause(channel: str):
    """Legacy data_only tasks belong to surveillance; Situation is explicit."""
    return (
        Task.task_type == TaskType.EXPORT_DATA,
        Task.input_data["data_only"].as_boolean().is_(True),
        func.coalesce(Task.input_data["data_line"].as_string(), "surveillance") == channel,
    )


def recent_failure_clause(now: datetime):
    """Count failures by completion time, falling back for legacy rows."""
    return and_(Task.status == TaskStatus.FAILED,
        func.coalesce(Task.completed_at, Task.updated_at) >= now - timedelta(days=1))


def classify_pending(groups, country_rows, *, channel: str, now: datetime) -> dict:
    """Apply the release planner's exact visibility and parent-expansion rules."""
    def summarize(selected):
        oldest = min((timestamp(row[2]) for row in selected if timestamp(row[2]) is not None), default=None)
        return {"count": sum(row[1] for row in selected), "scope_count": len(selected),
            "oldest_at": iso(oldest), "oldest_age_seconds": age(oldest, now)}
    total = summarize(groups)
    if channel == "surveillance":
        available = {str(code).strip().upper() for code, active in country_rows
            if active and get_country_bootstrap_config(code).get("public_release_enabled", True) is not False}
        rows = [SimpleNamespace(id=index, line=channel, scope_key=group[0],
            change_set={"version": 1, "precise": True} if group[3] else None)
            for index, group in enumerate(groups, 1)]
        plan = plan_changes(rows, available, full=True, max_countries=max(1, len(available)))
        selected = set(plan["dirty_ids"])
        accepted = [row for index, row in enumerate(groups, 1) if index in selected]
        blocked = [row for index, row in enumerate(groups, 1) if index not in selected]
        expanded_countries = len(plan["country_codes"])
    else:
        accepted = [row for row in groups if row[0] == "*"]
        blocked = [row for row in groups if row[0] != "*"]
        expanded_countries = 0
    blocked_summary = summarize(blocked)
    blocked_summary["scopes"] = [row[0] if re.fullmatch(r"[A-Z0-9-]{1,64}", row[0]) else "<invalid-scope>" for row in blocked[:100]]
    blocked_summary["scopes_truncated"] = len(blocked) > 100
    publishable = summarize(accepted)
    publishable["expanded_country_count"] = expanded_countries
    return {"pending": total, "publishable_pending": publishable, "blocked_pending": blocked_summary}


async def _research_publication_state(db, current, *, now, root, settings):
    from .research_publication_receipt import verified_research_commit

    expected = current
    committed_at = timestamp(current.get("completed_at")) if current else None
    if current and current.get("task_uuid") and current.get("created_at"):
        proof = await asyncio.to_thread(verified_research_commit, root,
            task_uuid=current["task_uuid"], release_id=current["release_id"],
            created_at=timestamp(current["created_at"]), completed_at=committed_at,
            papers_origin=settings.r2_papers_public_base_url)
        if proof and proof["manifest_sha256"] == current["manifest_sha256"]:
            expected = {**current, **proof}
            committed_at = timestamp(proof["committed_at"])
    # A site task may complete late, after a newer independent commit. Only its
    # immutable, verified authority receipt can advance the expected pointer.
    clauses = [Task.task_type == TaskType.EXPORT_DATA, Task.status == TaskStatus.COMPLETED,
        Task.input_data["data_only"].as_boolean().is_not(True),
        Task.output_data["r2_papers_published"].as_boolean().is_(True),
        Task.completed_at <= now]
    if committed_at is not None:
        clauses.append(Task.completed_at >= committed_at)
    rows = (await db.execute(select(Task.task_uuid, Task.created_at, Task.completed_at,
        Task.output_data["site_release"]["release_id"].as_string()).where(*clauses)
        .order_by(Task.completed_at.desc(), Task.id.desc()).limit(100))).all()
    for task_uuid, created, completed, release_id in rows:
        proof = await asyncio.to_thread(verified_research_commit, root,
            task_uuid=task_uuid, release_id=release_id, created_at=timestamp(created),
            completed_at=timestamp(completed), papers_origin=settings.r2_papers_public_base_url, site=True)
        if proof and (committed_at is None or timestamp(proof["committed_at"]) > committed_at):
            expected = {**proof, "status": "completed", "completed_at": iso(completed),
                "created_at": iso(created), "age_seconds": age(completed, now)}
            committed_at = timestamp(proof["committed_at"])
    return expected


async def collect_database_snapshot(db, *, now: datetime, channels=CHANNELS,
                                    root: Path = ROOT, settings=None) -> dict:
    """SELECT bounded task details, aggregate all pending markers; never commit."""
    result = {}
    countries = (await db.execute(select(Country.code, Country.is_active))).all() if "surveillance" in channels else []
    for channel in channels:
        if channel not in DATABASE_CHANNELS:
            raise ValueError("unsupported_data_channel")
        # The planner uses Python `version == 1` (also true for JSON true).
        precise = and_(func.jsonb_typeof(PublishDirty.change_set) == "object", or_(
            cast(PublishDirty.change_set, JSONB).contains({"version": 1, "precise": True}),
            cast(PublishDirty.change_set, JSONB).contains({"version": True, "precise": True})))
        groups = (await db.execute(select(PublishDirty.scope_key, func.count(PublishDirty.id),
            func.min(PublishDirty.marked_at), func.min(case((precise, 1), else_=0)),
        ).where(PublishDirty.line == channel, PublishDirty.published_at.is_(None)).group_by(PublishDirty.scope_key))).all()
        clauses = task_channel_clause(channel)
        queue = {status.value: 0 for status in ACTIVE}
        for status, count in (await db.execute(select(Task.status, func.count(Task.id)).where(
            *clauses, Task.status.in_(ACTIVE),
        ).group_by(Task.status))).all():
            queue[getattr(status, "value", status)] = count
        fields = (Task.task_uuid, Task.status, Task.created_at, Task.started_at,
            Task.metadata_["automatic_retry"]["next_attempt_at"].as_string(), Task.retry_count)
        active = []
        for task_id, status, created, started, retry_at, retry_count in (await db.execute(
            select(*fields).where(*clauses, Task.status.in_(ACTIVE)).order_by(Task.created_at).limit(100)
        )).all():
            active.append({"task_uuid": _identifier(task_id), "status": getattr(status, "value", status),
                "created_at": iso(created), "started_at": iso(started),
                "age_seconds": age(started or created, now), "next_attempt_at": iso(retry_at),
                "retry_count": int(retry_count or 0)})
        terminal_fields = (Task.status, Task.completed_at, Task.updated_at,
            Task.output_data["release_id"].as_string(), Task.output_data["manifest_sha256"].as_string())
        async def last_terminal(statuses):
            extra = (Task.task_uuid, Task.created_at) if channel == "literature" else ()
            row = (await db.execute(select(*terminal_fields, *extra).where(
                *clauses, Task.status.in_(statuses),
            ).order_by(func.coalesce(Task.completed_at, Task.updated_at).desc(), Task.id.desc()).limit(1))).first()
            if row is None:
                return None
            status, completed, updated, release_id, digest = row[:5]
            result = {"status": getattr(status, "value", status), "completed_at": iso(completed or updated),
                "age_seconds": age(completed or updated, now), "release_id": _identifier(release_id),
                "manifest_sha256": _digest(digest)}
            if extra:
                result.update(task_uuid=_identifier(row[5]), created_at=iso(row[6]))
            return result
        recent_failed = (await db.execute(select(func.count(Task.id)).where(
            *clauses, recent_failure_clause(now),
        ))).scalar_one()
        result[channel] = {**classify_pending(groups, countries, channel=channel, now=now),
            "queue": queue, "active": active, "active_sample_truncated": sum(queue.values()) > len(active),
            "failed_last_24h": recent_failed,
            "last_completed": await last_terminal((TaskStatus.COMPLETED,)),
            "latest_terminal": await last_terminal((TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED))}
        if channel == "literature":
            config = settings or get_config().data_release
            result[channel]["last_completed"] = await _research_publication_state(
                db, result[channel]["last_completed"], now=now, root=root, settings=config)
            latest_created = (await db.execute(select(Task.created_at).where(*clauses)
                .order_by(Task.created_at.desc(), Task.id.desc()).limit(1))).scalar_one_or_none()
            last = timestamp(latest_created)
            interval = int(config.literature_publish_interval_minutes)
            due = last + timedelta(minutes=interval) if last and last <= now else None
            pending = result[channel]["publishable_pending"]
            oldest = timestamp(pending["oldest_at"])
            pending_due = max(oldest, due) if oldest and due else oldest
            result[channel]["schedule"] = {"interval_minutes": interval,
                "last_task_created_at": iso(last), "next_due_at": iso(due),
                "pending_due_at": iso(pending_due), "pending_overdue_seconds": age(pending_due, now),
                "waiting_for_schedule": bool(pending["count"] and due and due > now)}
    return result


def validate_public_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("invalid_public_manifest_url")
    return value


class _ProbeFailure(Exception):
    def __init__(self, code, http_status=None):
        self.code, self.http_status = code, http_status


async def _read(client, url: str, *, limit: int) -> tuple[bytes, dict]:
    async with client.stream("GET", url, headers={"Cache-Control": "no-cache"}) as response:
        if response.status_code != 200:
            raise _ProbeFailure("http_status", response.status_code)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise _ProbeFailure("response_too_large")
        return bytes(body), dict(response.headers)


async def probe_public_manifest(client, url: str, *, timeout_seconds: float = 10,
                                object_limit_bytes: int = 2 * 1024 * 1024) -> dict:
    """Verify decoded bytes (including gzip) without trusting ETag as SHA-256."""
    start = time.monotonic()
    attempts = 0
    transient_errors = []
    async def probe():
        validate_public_url(url)
        body, headers = await _read(client, url, limit=2 * 1024 * 1024)
        manifest, objects = validate_manifest(body)
        release_id = _identifier(manifest.get("release_id"))
        generated = iso(manifest.get("generated_at"))
        if not release_id or generated is None:
            raise _ProbeFailure("invalid_manifest_metadata")
        candidates = sorted((item["bytes"], relative, item) for relative, item in objects.items())
        size, relative, expected = candidates[0]
        if size > object_limit_bytes:
            raise _ProbeFailure("sample_object_exceeds_limit")
        payload, _ = await _read(client, urljoin(url, relative), limit=object_limit_bytes)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != expected["sha256"]:
            raise _ProbeFailure("object_checksum_mismatch")
        return {"available": True, "http_status": 200, "release_id": release_id,
            "manifest_sha256": hashlib.sha256(body).hexdigest(), "generated_at": generated,
            "logical_files": len(manifest["files"]), "immutable_objects": len(objects),
            "sample_verified": True, "sample_bytes": size,
            "pointer_cache_control": headers.get("cache-control", "")[:160]}

    async def probe_with_retry():
        nonlocal attempts
        while True:
            attempts += 1
            try:
                return await probe()
            except httpx.TransportError as error:
                code = "http_timeout" if isinstance(error, httpx.TimeoutException) else "http_unreachable"
                if attempts >= 2:
                    raise
                transient_errors.append(code)
            except _ProbeFailure as error:
                retryable = error.code == "http_status" and error.http_status is not None and (
                    error.http_status == 429 or 500 <= error.http_status <= 599)
                if attempts >= 2 or not retryable:
                    raise
                transient_errors.append("http_status")
            await asyncio.sleep(0.15)

    try:
        result = await asyncio.wait_for(probe_with_retry(), timeout=timeout_seconds)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        result = {"available": False, "error_code": "http_timeout"}
    except _ProbeFailure as error:
        result = {"available": False, "error_code": error.code}
        if error.http_status is not None:
            result["http_status"] = error.http_status
    except httpx.HTTPError:
        result = {"available": False, "error_code": "http_unreachable"}
    except (ValueError, TypeError, KeyError):
        result = {"available": False, "error_code": "invalid_manifest"}
    result["attempts"] = attempts
    if transient_errors:
        result["transient_errors"] = transient_errors
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return result


def evaluate_channel(database: dict | None, public: dict, *, now: datetime,
                     thresholds: MonitorThresholds, control: dict | None = None) -> dict:
    issues = []
    def issue(level, code):
        issues.append({"level": level, "code": code})
    def timed(seconds, warning, critical, code):
        if seconds is not None and seconds >= warning:
            issue("critical" if seconds >= critical else "warning", code)
    paused = bool(control and control.get("paused"))
    if control and control.get("error_code"):
        issue("critical", control["error_code"])
    elif paused:
        issue("warning", "automatic_publication_paused")
    if database is None:
        issue("critical", "database_unavailable")
    else:
        pending = database["publishable_pending"]
        if not paused:
            pending_delay = pending["oldest_age_seconds"]
            due = timestamp(database.get("schedule", {}).get("next_due_at"))
            if due is not None and pending_delay is not None:
                pending_delay = min(pending_delay, age(due, now))
            timed(pending_delay, thresholds.pending_warning_seconds,
                thresholds.pending_critical_seconds, "pending_changes_delayed")
        if database["blocked_pending"]["count"]:
            issue("warning", "pending_scopes_blocked")
        for task in database["active"]:
            if task["status"] == "running":
                timed(task["age_seconds"], thresholds.running_warning_seconds,
                    thresholds.running_critical_seconds, "running_task_delayed")
            elif task["status"] in {"pending", "queued"}:
                timed(task["age_seconds"], thresholds.pending_warning_seconds,
                    thresholds.pending_critical_seconds, "queued_task_delayed")
            elif task["status"] == "retrying":
                if task["next_attempt_at"] is None:
                    issue("critical", "retry_schedule_missing")
                elif not paused:
                    timed(age(task["next_attempt_at"], now), thresholds.pending_warning_seconds,
                        thresholds.pending_critical_seconds, "retry_overdue")
        if database["active_sample_truncated"]:
            issue("warning", "active_task_sample_truncated")
        terminal = database["latest_terminal"]
        if terminal and terminal["status"] in {"failed", "cancelled"}:
            issue("warning" if sum(database["queue"].values()) else "critical", "latest_release_unsuccessful")
    if not public.get("available"):
        issue("critical", public.get("error_code", "public_pointer_unavailable"))
    else:
        if public.get("transient_errors"):
            issue("warning", "transient_http_recovered")
        generated = timestamp(public["generated_at"])
        if generated and (generated - now).total_seconds() > 300:
            issue("warning", "public_clock_ahead")
        if database and database["last_completed"]:
            expected = database["last_completed"]
            if expected["manifest_sha256"] is None or expected["release_id"] is None:
                issue("warning", "completed_release_receipt_missing")
            elif (expected["manifest_sha256"] != public["manifest_sha256"]
                  or expected["release_id"] != public["release_id"]):
                active_ids = {f"{prefix}-{task['task_uuid']}" for task in database["active"]
                    for prefix in ("data", "situation", "literature")}
                if public["release_id"] not in active_ids:
                    issue("critical" if expected["age_seconds"] > thresholds.publication_grace_seconds else "warning",
                        "public_completed_version_mismatch")
    status = max((item["level"] for item in issues), key=LEVEL.get, default="ok")
    result = {"status": status, "issues": issues, "database": database, "public": public}
    if control is not None:
        result["control"] = control
    return result


def assemble_report(database: dict | None, public: dict, *, now: datetime,
                    thresholds: MonitorThresholds, database_error: str | None = None,
                    controls: dict | None = None) -> dict:
    channels = {name: evaluate_channel(database.get(name) if database is not None else None,
        payload, now=now, thresholds=thresholds,
        control=controls.get(name) if controls is not None else None) for name, payload in public.items()}
    report = {"schema_version": 1, "service": "realtime-data", "checked_at": iso(now),
        "status": max((value["status"] for value in channels.values()), key=LEVEL.get, default="ok"),
        "channels": channels}
    if database_error:
        report["database_error_code"] = database_error
    return report


def report_text(report: dict) -> str:
    lines = [f"{report['checked_at']} realtime-data {report['status']}"]
    for channel, result in report["channels"].items():
        db, public = result["database"], result["public"]
        details = f"pending={db['pending']['count']} publishable={db['publishable_pending']['count']} oldest={db['publishable_pending']['oldest_age_seconds']}s blocked={db['blocked_pending']['count']} queue={db['queue']}" if db else "database=unavailable"
        lines.append(f"{channel}: {result['status']} {details} release={public.get('release_id', '-')} http={public.get('http_status', '-')} issues={','.join(item['code'] for item in result['issues']) or '-'}")
    return "\n".join(lines)
