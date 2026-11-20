"""One visible queued reservation per publication channel.

A blocked export stays ``QUEUED`` with ``metadata.publication_wait``. The
worker does not claim that row, and the scheduler does not insert another.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_

from src.domain.task import TaskStatus

from .resilience import classify_release_failure

WAIT_KINDS = frozenset({
    "blocked_by_static",
    "baseline_invalid",
    "contract_blocked",
    "transient_backoff",
})
JSON_CHANNELS = frozenset({"surveillance", "situation", "literature"})
STUCK_AFTER = timedelta(seconds=21600)
_CONTRACT_FILES = (
    "astro-site/public/site-data/research/index.json",
    "astro-site/src/data/research/routes.json",
)


def channel_of(input_data: dict[str, Any] | None) -> str | None:
    """Return the reservation channel, or None for lanes that stay independent."""
    data = dict(input_data or {})
    if any(data.get(flag) for flag in ("raw_archive_only", "reports_only", "theme_only")):
        return None
    if data.get("data_only"):
        line = str(data.get("data_line") or "surveillance")
        return line if line in JSON_CHANNELS else None
    return "static"


def claim_allowed(metadata: dict[str, Any] | None, now: datetime) -> bool:
    """Worker claim rule. Only a due transient backoff may leave the queue."""
    wait = (metadata or {}).get("publication_wait")
    if not isinstance(wait, dict):
        return True
    if wait.get("stuck") or wait.get("kind") != "transient_backoff":
        return False
    due = _aware(wait.get("not_before"))
    return due is not None and due <= now


def static_reservation_blocks_json(task: Any, now: datetime) -> bool:
    """Whether a static export currently blocks independent JSON lanes.

    A queued static export parked on a non-runnable publication wait is only a
    durable reservation for its own recovery. Treating it as an active static
    build deadlocks the JSON channels because the worker is deliberately not
    allowed to claim that parked row. Running exports always remain blockers;
    a due transient retry is also still an active reservation.
    """
    input_data = dict(getattr(task, "input_data", None) or {})
    if any(input_data.get(flag) for flag in ("content_only", "reports_only", "code_only", "situation_detached")):
        return False
    if task.status == TaskStatus.RUNNING:
        return True
    return task.status in (TaskStatus.PENDING, TaskStatus.QUEUED) and claim_allowed(
        task.metadata_, now
    )


def failure_kind(exc: BaseException) -> str | None:
    """Park a non-retryable publication failure. Transient retries stay on the old path."""
    message = str(exc)
    if "restore the published baseline" in message:
        return "baseline_invalid"
    if classify_release_failure(exc).retryable:
        return None
    return "contract_blocked"


def contract_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in _CONTRACT_FILES:
        path = root / relative
        digest.update(relative.encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def surveillance_baseline_ready(root: Path, *, downloads: bool) -> bool:
    from src.generation.surveillance_data_export import completed_generation

    state = root / "exports" / "surveillance-state"
    public = root / "exports" / "surveillance-data"
    if completed_generation(state, public) is None:
        return False
    if not downloads:
        return True
    from src.generation.surveillance_downloads import downloads_initialized

    return bool(downloads_initialized(root / "exports" / "site-downloads"))


def build_wait(
    *,
    kind: str,
    channel: str,
    reason: str,
    now: datetime,
    blocked_by: str | None = None,
    blocker_fingerprint: str | None = None,
    since: str | None = None,
    attempt: int = 0,
    stuck: bool = False,
    recheck_seconds: int = 30,
) -> dict[str, Any]:
    if kind not in WAIT_KINDS:
        raise ValueError(f"Unknown publication wait kind: {kind}")
    current = _aware(now) or datetime.now(timezone.utc)
    return {
        "kind": kind,
        "channel": channel,
        "reason": reason,
        "blocked_by": blocked_by,
        "not_before": (current + timedelta(seconds=recheck_seconds)).isoformat(),
        "blocker_fingerprint": blocker_fingerprint,
        "since": since or current.isoformat(),
        "stuck": stuck,
        "attempt": attempt,
    }


def wait_reason(kind: str, channel: str, *, blocked_by: str | None, detail: str = "") -> str:
    if kind == "blocked_by_static":
        target = blocked_by or "the static site release"
        return f"Waiting for static site release {target}; {channel} JSON is not started."
    if kind == "baseline_invalid":
        return (
            "Surveillance incremental baseline is invalid; "
            "waiting until the published baseline is restored."
        )
    if kind == "transient_backoff":
        return "Waiting until the transient publication retry is due."
    line = " ".join(detail.split())[:240]
    if line:
        return f"Publication is waiting on an unchanged contract. {line}"
    return "Publication is waiting on an unchanged contract."


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
