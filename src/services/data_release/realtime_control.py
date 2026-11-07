"""Control-plane projections and safe actions for independent JSON publication.

Reads only publication markers, task metadata and the local monitor receipt.
Manual actions delegate to the existing transactional publication queue; this
module never collects observations, acknowledges markers or reuses candidates.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import quote

from sqlalchemy import and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from src.core.config import get_config
from src.core.country_library import get_country_bootstrap_config
from src.domain import Country, Task, TaskStatus
from src.domain.collection import PublishDirty
from .realtime_monitor import ACTIVE, CHANNELS, age, collect_database_snapshot, iso, task_channel_clause, timestamp
from .planning import plan_changes

ROOT = Path(__file__).resolve().parents[3]
MONITOR_PATH = ROOT / "logs/realtime-data-monitor/latest.json"
MONITOR_STALE_SECONDS = 180
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_SCOPE = re.compile(r"[A-Z0-9-]{1,64}\Z")
_TASK_FIELDS = (Task.task_uuid, Task.status, Task.progress, Task.created_at, Task.started_at,
    Task.completed_at, Task.input_data, Task.output_data, Task.metadata_)
_RETRY_STATUS = {"not_scheduled", "scheduled", "queued", "terminal", "succeeded"}
_RETRY_CATEGORIES = {"permanent", "transient_external", "preflight", "non_external_stage", "unclassified_external"}
_ACTION_REASONS = {"already_running", "release_pipeline_busy", "no_pending_changes", "no_publishable_changes",
    "selection_changed", "continuation_already_queued", "channel_paused", "automatic_paused"}
_MONITOR_ISSUES = {"database_unavailable", "pending_changes_delayed", "pending_scopes_blocked",
    "running_task_delayed", "queued_task_delayed", "retry_schedule_missing", "retry_overdue",
    "active_task_sample_truncated", "latest_release_unsuccessful", "public_pointer_unavailable",
    "public_clock_ahead", "completed_release_receipt_missing", "public_completed_version_mismatch",
    "http_timeout", "http_unreachable", "http_status", "response_too_large", "invalid_manifest",
    "invalid_manifest_metadata", "sample_object_exceeds_limit", "object_checksum_mismatch"}
_MONITOR_ISSUES.update({"automatic_publication_paused", "control_state_invalid", "control_state_unavailable"})


class RealtimeControlError(ValueError):
    def __init__(self, code: str, status_code: int = 409, *, task_uuid=None):
        super().__init__(code)
        self.code, self.status_code = code, status_code
        self.task_uuid = _identifier(task_uuid)
        self.href = _href(self.task_uuid)


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def _digest(value):
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _enum(value, allowed, fallback=None):
    return value if isinstance(value, str) and value in allowed else fallback


def _count(value):
    try:
        return max(0, min(int(value or 0), 2**31 - 1))
    except (ValueError, TypeError, OverflowError):
        return 0


def _seconds(value):
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _href(task_uuid):
    return f"/publish/tasks?detail={quote(task_uuid, safe='')}" if task_uuid else None


def _channel(channel):
    if channel not in CHANNELS:
        raise RealtimeControlError("unknown_realtime_channel", 404)
    return channel


def project_run(row) -> dict:
    """Whitelist operational fields; raw input/output/errors never cross HTTP."""
    data, output, metadata = _mapping(row.input_data), _mapping(row.output_data), _mapping(row.metadata_)
    retry, continuation = _mapping(metadata.get("automatic_retry")), _mapping(metadata.get("release_continuation"))
    remaining = data.get("continuation_dirty_ids")
    remaining = len(remaining) if isinstance(remaining, list) else 0
    timing = _mapping(output.get("release_timing"))
    identifier = _identifier(row.task_uuid)
    if not identifier:
        raise RealtimeControlError("invalid_realtime_task_identity", 503)
    status = getattr(row.status, "value", row.status)
    return {"task_uuid": identifier, "status": status if status in {item.value for item in TaskStatus} else "unknown",
        "progress": min(100, _count(row.progress)), "created_at": iso(row.created_at),
        "started_at": iso(row.started_at), "completed_at": iso(row.completed_at),
        "job_id": _identifier(data.get("release_job_id") or metadata.get("release_job_id")),
        "release_id": _identifier(output.get("release_id")), "manifest_sha256": _digest(output.get("manifest_sha256")),
        "country_count": len(data["incremental_country_codes"]) if isinstance(data.get("incremental_country_codes"), list) else 0,
        "selected_change_count": len(data["publish_dirty_ids"]) if isinstance(data.get("publish_dirty_ids"), list) else 0,
        "retry": {"status": _enum(retry.get("status"), _RETRY_STATUS, "unknown" if retry else "none"),
            "scheduled_attempts": _count(retry.get("scheduled_attempts")), "max_attempts": _count(retry.get("max_attempts")),
            "next_attempt_at": iso(retry.get("next_attempt_at")), "retryable": retry.get("retryable") is True,
            "exhausted": retry.get("exhausted") is True,
            "category": _enum(retry.get("category"), _RETRY_CATEGORIES, "unknown" if retry.get("category") else None)},
        "continuation": {"status": _enum(continuation.get("status"), {"queued", "exhausted"},
                "unknown" if continuation else "pending" if remaining else "none"),
            "remaining_count": remaining, "task_uuid": _identifier(continuation.get("task_uuid"))},
        "release_timing": {key: _seconds(timing.get(key)) for key in
            ("export_seconds", "publish_and_ack_seconds", "elapsed_seconds")} if timing else None,
        "href": _href(identifier)}


def _monitor_receipt(path: Path) -> tuple[dict, str | None]:
    try:
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            return {}, "monitor_invalid"
        payload = json.loads(path.read_bytes())
        if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
                or not isinstance(payload.get("channels"), dict) or timestamp(payload.get("checked_at")) is None):
            return {}, "monitor_invalid"
        return payload, None
    except FileNotFoundError:
        return {}, "monitor_missing"
    except (OSError, ValueError, TypeError):
        return {}, "monitor_unreadable"


def project_monitor(payload: dict, error: str | None, channel: str, *, now: datetime) -> tuple[dict, dict]:
    channels = _mapping(payload.get("channels"))
    raw_record = channels.get(channel)
    if not error and not isinstance(raw_record, dict):
        error = "monitor_channel_missing"
    elif not error and not isinstance(raw_record.get("public"), dict):
        error = "monitor_invalid"
    observed = iso(payload.get("checked_at"))
    elapsed = age(observed, now)
    future = bool(observed and (timestamp(observed) - now).total_seconds() > 300)
    stale = error is not None or elapsed is None or elapsed > MONITOR_STALE_SECONDS or future
    record = _mapping(raw_record)
    public = _mapping(record.get("public"))
    issues_source = record.get("issues") if isinstance(record.get("issues"), list) else []
    issues = [{"level": item["level"], "code": _enum(item.get("code"), _MONITOR_ISSUES, "monitor_unknown_issue")}
        for item in issues_source if isinstance(item, dict) and _enum(item.get("level"), {"warning", "critical"})][:100]
    if error or stale:
        issues.append({"level": "warning", "code": error or ("monitor_clock_ahead" if future else "monitor_stale")})
    status = _enum(record.get("status"), {"ok", "warning", "critical"}, "unknown")
    monitor = {"checked_at": observed, "age_seconds": elapsed, "stale": stale, "status": status,
        "http_status": public.get("http_status") if type(public.get("http_status")) is int and 100 <= public["http_status"] <= 599 else None,
        "sample_verified": public.get("sample_verified") if type(public.get("sample_verified")) is bool else None,
        "issues": issues}
    manifest = {"release_id": _identifier(public.get("release_id")), "sha256": _digest(public.get("manifest_sha256")),
        "generated_at": iso(public.get("generated_at")), "observed_at": observed,
        "available": public.get("available") if type(public.get("available")) is bool else None, "stale": stale}
    return monitor, manifest


def _get_controls():
    from .channel_control import get_realtime_controls
    return get_realtime_controls()


async def _set_control(channel, paused):
    from .channel_control import set_realtime_control
    return await set_realtime_control(channel, paused)


class RealtimeControlService:
    def __init__(self, *, monitor_path=MONITOR_PATH, settings_reader=None,
                 controls_reader=None, control_writer=None, release_service=None):
        self.monitor_path = Path(monitor_path)
        self.settings_reader = settings_reader or (lambda: get_config().data_release)
        self.controls_reader = controls_reader or _get_controls
        self.control_writer = control_writer or _set_control
        self.release_service = release_service

    def _enabled(self, channel):
        return bool(getattr(self.settings_reader(), f"{channel}_json_enabled", False))

    def _controls(self):
        try:
            value = self.controls_reader()
            if not isinstance(value, dict):
                raise ValueError("invalid control state")
        except Exception:
            return {channel: {"paused": True, "updated_at": None, "error_code": "control_state_unavailable"} for channel in CHANNELS}
        result = {}
        for channel in CHANNELS:
            item = _mapping(value.get(channel))
            valid = type(item.get("paused")) is bool
            result[channel] = {"paused": item.get("paused") if valid and not item.get("error_code") else True,
                "updated_at": iso(item.get("updated_at")),
                "error_code": _enum(item.get("error_code"), {"control_state_invalid", "control_state_unavailable"},
                    "control_state_unavailable" if item.get("error_code") or not valid else None)}
        return result

    async def overview(self, db, *, now=None):
        now = now or datetime.now(timezone.utc)
        snapshot = await collect_database_snapshot(db, now=now)
        payload, monitor_error = await asyncio.to_thread(_monitor_receipt, self.monitor_path)
        controls = self._controls()
        channels = {}
        for channel in CHANNELS:
            data, control = snapshot[channel], controls[channel]
            enabled = self._enabled(channel)
            clauses = task_channel_clause(channel)
            current = (await db.execute(select(*_TASK_FIELDS).where(*clauses, Task.status.in_(ACTIVE)).order_by(
                case((Task.status == TaskStatus.RUNNING, 0), (Task.status == TaskStatus.RETRYING, 1), else_=2),
                Task.created_at.asc(), Task.id.asc()).limit(1))).first()
            latest = {}
            for kind, statuses in (("latest_success", (TaskStatus.COMPLETED,)), ("latest_failure", (TaskStatus.FAILED, TaskStatus.CANCELLED))):
                row = (await db.execute(select(*_TASK_FIELDS).where(*clauses, Task.status.in_(statuses)).order_by(
                    func.coalesce(Task.completed_at, Task.updated_at).desc(), Task.id.desc()).limit(1))).first()
                latest[kind] = project_run(row) if row is not None else None
            monitor, manifest = project_monitor(payload, monitor_error, channel, now=now)
            channels[channel] = {"channel": channel, "enabled": enabled, "paused": control["paused"],
                "effective_enabled": enabled and not control["paused"], "manual_enabled": enabled,
                "control_updated_at": control["updated_at"], "control_error_code": control["error_code"],
                "backlog": {"total": data["pending"], "publishable": data["publishable_pending"], "blocked": data["blocked_pending"]},
                "queue": data["queue"], "current_run": project_run(current) if current is not None else None,
                **latest, "failed_last_24h": data["failed_last_24h"], "monitor": monitor, "manifest": manifest}
        return {"checked_at": iso(now), "channels": channels, "independence": {"static_site_separate": True,
            "message": "Realtime actions publish only JSON. Static HTML has its own release workflow. Pausing automatic publication keeps existing tasks and manual actions available."}}

    async def _countries(self, db):
        rows = (await db.execute(select(Country.code, Country.is_active))).all()
        known = {str(code).strip().upper(): active for code, active in rows}
        available = {code for code, active in known.items() if active
            and get_country_bootstrap_config(code).get("public_release_enabled", True) is not False}
        return known, available

    async def _scope_precision(self, db, scopes):
        if not scopes:
            return {}
        value = PublishDirty.change_set
        if db.get_bind().dialect.name == "postgresql":
            exact = and_(func.jsonb_typeof(value) == "object", or_(
                cast(value, JSONB).contains({"version": 1, "precise": True}),
                cast(value, JSONB).contains({"version": True, "precise": True})))
        else:
            # SQLite mirrors the strict JSON boolean check in isolated tests;
            # malformed legacy markers remain coarse rather than raising casts.
            exact = and_(func.json_type(value) == "object", func.json_type(value, "$.precise") == "true",
                or_(func.json_type(value, "$.version") == "true",
                    and_(func.json_type(value, "$.version").in_(["integer", "real"]), func.json_extract(value, "$.version") == 1)))
        rows = (await db.execute(select(PublishDirty.scope_key, func.min(case((exact, 1), else_=0))).where(
            PublishDirty.line == "surveillance", PublishDirty.published_at.is_(None),
            PublishDirty.scope_key.in_(sorted(scopes))).group_by(PublishDirty.scope_key))).all()
        return {scope: bool(precise) for scope, precise in rows}

    async def pending(self, db, channel, *, page=1, page_size=20, eligibility="all", now=None):
        channel = _channel(channel)
        now = now or datetime.now(timezone.utc)
        conditions = [PublishDirty.line == channel, PublishDirty.published_at.is_(None)]
        known, available = await self._countries(db) if channel == "surveillance" else ({}, set())
        if channel == "surveillance":
            if eligibility == "publishable":
                conditions.append(PublishDirty.scope_key.in_(sorted(available)))
            elif eligibility == "blocked":
                conditions.append(PublishDirty.scope_key.notin_(sorted(available)))
        elif eligibility == "publishable":
            conditions.append(PublishDirty.scope_key == "*")
        elif eligibility == "blocked":
            conditions.append(PublishDirty.scope_key != "*")
        total = (await db.execute(select(func.count(PublishDirty.id)).where(*conditions))).scalar_one()
        rows = (await db.execute(select(PublishDirty).where(*conditions).order_by(
            PublishDirty.marked_at.asc(), PublishDirty.id.asc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
        precision = await self._scope_precision(db, {row.scope_key for row in rows if row.scope_key in available}) if channel == "surveillance" else {}
        scope_affected = {}
        for scope, exact in precision.items():
            marker = SimpleNamespace(id=1, line="surveillance", scope_key=scope,
                change_set={"version": 1, "precise": True} if exact else None)
            scope_affected[scope] = len(plan_changes([marker], available, full=True)["country_codes"])
        items = []
        for row in rows:
            valid = row.scope_key == "*" if channel == "situation" else row.scope_key in available
            reason = (None if valid else "invalid_scope" if channel == "situation" or not _SCOPE.fullmatch(row.scope_key)
                else "unknown_country" if row.scope_key not in known else "inactive_country" if not known[row.scope_key] else "disabled_country")
            changes = _mapping(row.change_set)
            precise = changes.get("version") == 1 and changes.get("precise") is True
            ids = list(changes.get("disease_ids") or []) if isinstance(changes.get("disease_ids"), list) else []
            entries = changes.get("changes") if isinstance(changes.get("changes"), list) else []
            for change in entries:
                if isinstance(change, dict):
                    ids.extend([change.get("disease_id")])
                    if isinstance(change.get("disease_ids"), list):
                        ids.extend(change["disease_ids"])
            ids = sorted({value for value in ids if isinstance(value, str) and re.fullmatch(r"D[0-9A-Z_-]{2,30}", value)})[:200]
            affected = scope_affected.get(row.scope_key, 0) if channel == "surveillance" else 0
            items.append({"id": row.id, "channel": channel, "scope_key": row.scope_key if _SCOPE.fullmatch(row.scope_key) or row.scope_key == "*" else "<invalid-scope>",
                "marked_at": iso(row.marked_at), "age_seconds": age(row.marked_at, now),
                "source_task_uuid": _identifier(row.source_task_uuid), "precise": precise, "disease_ids": ids,
                "publishable": valid, "blocked_reason": reason, "affected_country_count": affected})
        return {"items": items, "page": page, "page_size": page_size, "total": total, "total_pages": math.ceil(total / page_size) if total else 0}

    async def runs(self, db, channel, *, page=1, page_size=20, statuses=None):
        clauses = list(task_channel_clause(_channel(channel)))
        if statuses:
            clauses.append(Task.status.in_(statuses))
        total = (await db.execute(select(func.count(Task.id)).where(*clauses))).scalar_one()
        rows = (await db.execute(select(*_TASK_FIELDS).where(*clauses).order_by(
            Task.created_at.desc(), Task.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return {"items": [project_run(row) for row in rows], "page": page, "page_size": page_size,
            "total": total, "total_pages": math.ceil(total / page_size) if total else 0}

    async def enqueue(self, channel, *, job_id=None, retry=False):
        channel = _channel(channel)
        if not self._enabled(channel):
            raise RealtimeControlError("realtime_channel_disabled", 409)
        service = self.release_service
        if service is None:
            from src.services.data_release_service import data_release_service
            service = data_release_service
        if job_id is None:
            try:
                jobs = await service.load_jobs()
            except Exception as error:
                raise RealtimeControlError("realtime_queue_unavailable", 503) from error
            job = next((job for job in jobs if getattr(job, "enabled", False)), None)
            if job is None:
                raise RealtimeControlError("no_enabled_release_job", 409)
            job_id = job.job_id
        try:
            result = await service.trigger_job(job_id, manual=True,
                trigger="dashboard-realtime-retry" if retry else "dashboard-realtime",
                data_only=True, data_line=channel)
        except ValueError as error:
            code = "release_job_not_found" if "job not found" in str(error).lower() else "realtime_selection_invalid"
            raise RealtimeControlError(code, 404 if code == "release_job_not_found" else 422) from error
        except RuntimeError as error:
            raise RealtimeControlError("realtime_queue_unavailable", 409) from error
        except Exception as error:
            raise RealtimeControlError("realtime_queue_unavailable", 503) from error
        identifier = _identifier(result.get("task_uuid"))
        if retry and result.get("status") != "queued" and _enum(result.get("reason"), {"already_running", "release_pipeline_busy"}):
            raise RealtimeControlError("realtime_task_reserved", 409, task_uuid=identifier)
        if result.get("status") == "queued" and not identifier:
            raise RealtimeControlError("invalid_realtime_task_identity", 503)
        return {"channel": channel, "status": "queued" if result.get("status") == "queued" else "skipped",
            "task_uuid": identifier, "reason": _enum(result.get("reason"), _ACTION_REASONS,
                "request_skipped" if result.get("reason") else None), "href": _href(identifier)}

    async def control(self, channel, *, paused):
        channel = _channel(channel)
        try:
            await self.control_writer(channel, paused)
        except Exception as error:
            raise RealtimeControlError("realtime_control_write_failed", 503) from error
        control = self._controls()[channel]
        if control["error_code"]:
            raise RealtimeControlError(control["error_code"], 503)
        enabled = self._enabled(channel)
        return {"channel": channel, "enabled": enabled, "paused": control["paused"],
            "effective_enabled": enabled and not control["paused"], "updated_at": control["updated_at"],
            "error_code": control["error_code"]}


realtime_control_service = RealtimeControlService()
