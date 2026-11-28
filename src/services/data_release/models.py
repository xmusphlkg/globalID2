"""Domain models, scheduling helpers, and channel-conflict clauses for data releases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_

from src.domain import Task, TaskStatus, TaskType
from src.services.data_release.channel_control import is_automatic_json_paused


@dataclass
class DataReleaseJobConfig:
    job_id: str
    name: str
    enabled: bool = True
    priority: str = "high"
    auto_after_crawls: bool = False
    # When enabled, publish the validated v2 snapshot after local generation.
    include_git_push: bool = True
    include_cloudflare_deploy: bool = True
    require_clean_worktree: bool = True
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = "02:00"
    timezone: Optional[str] = "UTC"
    github_remote: str = "origin"
    github_branch: Optional[str] = None
    cloudflare_project_name: Optional[str] = None
    commit_message_template: str = "chore(data-release): publish site data {timestamp}"
    notes: Optional[str] = None


@dataclass
class DataReleaseJobState:
    next_run_at: Optional[datetime] = None
    last_started_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    last_status: str = "idle"
    last_error: Optional[str] = None
    last_task_uuid: Optional[str] = None
    run_count: int = 0
    skipped_count: int = 0


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _coerce_status(value: Any, fallback: str = "idle") -> str:
    if hasattr(value, "value"):
        return str(value.value)
    if value is None:
        return fallback
    return str(value)


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


def _data_line(input_data: dict[str, Any]) -> str:
    line = str(input_data.get("data_line") or "surveillance")
    if line not in {"surveillance", "situation", "literature"}:
        raise ValueError(f"Unknown JSON publication line: {line}")
    return line


def _data_enabled(config: Any, input_data: dict[str, Any]) -> bool:
    return bool(getattr(config, f"{_data_line(input_data)}_json_enabled", False))


def _parked_json_reservation(line: str):
    return and_(
        Task.task_type == TaskType.EXPORT_DATA,
        Task.status == TaskStatus.QUEUED,
        Task.input_data["data_only"].as_boolean().is_(True),
        func.coalesce(Task.input_data["data_line"].as_string(), "surveillance") == line,
        Task.metadata_["publication_wait"]["kind"].as_string().in_(
            ("contract_blocked", "baseline_invalid", "blocked_by_static")
        ).is_(True),
    )


def _release_conflict_clause(
    input_data: dict[str, Any],
    *,
    is_paused: Callable[[str], bool] = is_automatic_json_paused,
    parked_reservation: Callable[[str], Any] = _parked_json_reservation,
):
    """Channel reservation, including legacy surveillance-exporting sites."""
    archive = Task.input_data["raw_archive_only"].as_boolean().is_(True)
    data = Task.input_data["data_only"].as_boolean().is_(True)
    literature = and_(data, (Task.input_data["data_line"].as_string() == "literature").is_(True))
    static_safe = or_(
        Task.input_data[
            "situation_detached"
            if input_data.get("data_only") and _data_line(input_data) == "situation"
            else "content_only"
        ].as_boolean().is_(True),
        Task.input_data["reports_only"].as_boolean().is_(True),
        Task.input_data["code_only"].as_boolean().is_(True),
    )
    if input_data.get("raw_archive_only"):
        return archive
    if input_data.get("data_only"):
        if _data_line(input_data) == "literature":
            # Research has private data artifacts but reads static site inputs.
            return and_(~archive, or_(literature, ~data))
        conflict = and_(~archive, ~literature, or_(data, ~static_safe))
        other = "surveillance" if _data_line(input_data) == "situation" else "situation"
        conflict = and_(conflict, ~parked_reservation(other))
        if is_paused(other):
            other_line = (
                Task.input_data["data_line"].as_string().is_distinct_from("situation")
                if other == "surveillance"
                else (Task.input_data["data_line"].as_string() == "situation").is_(True)
            )
            # A parked retry owns its own candidate, not the other channel's
            # output directory. Queued/running work keeps the shared JSON slot.
            conflict = and_(conflict, ~and_(Task.status == TaskStatus.RETRYING, data, other_line))
        return conflict
    if input_data.get("reports_only") or input_data.get("code_only"):
        return and_(~archive, or_(~data, literature))
    if input_data.get("content_only") or input_data.get("situation_detached"):
        conflicting_data = []
        if not input_data.get("content_only"):
            conflicting_data.append(
                and_(data, Task.input_data["data_line"].as_string().is_distinct_from("situation"))
            )
        if not input_data.get("situation_detached"):
            conflicting_data.append(
                and_(data, Task.input_data["data_line"].as_string() == "situation")
            )
        return and_(~archive, or_(~data, literature, *conflicting_data))
    # An old site task can read/write observations and published curves. Until
    # it drains, it owns both the static and the surveillance channel.
    return ~archive


def compute_next_run(
    job: DataReleaseJobConfig,
    *,
    default_timezone: str = "UTC",
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Compute the next scheduled execution timestamp for a release job."""
    timezone_name = job.timezone or default_timezone
    zone = ZoneInfo(timezone_name)
    current = now.astimezone(zone) if now else datetime.now(zone)
    if job.interval_minutes:
        return current + timedelta(minutes=max(1, job.interval_minutes))
    if job.daily_time:
        hour_text, minute_text = job.daily_time.split(":", 1)
        scheduled = datetime.combine(
            current.date(),
            time(hour=int(hour_text), minute=int(minute_text), tzinfo=zone),
        )
        if scheduled <= current:
            scheduled += timedelta(days=1)
        return scheduled
    return None


def build_release_task_payload(
    job: DataReleaseJobConfig,
    *,
    default_timezone: str,
    branch: str,
    project_name: str,
    download_repo_url: str,
    download_url_base: str,
    raw_archive: Any,
    generated_paths: Sequence[str],
    manual: bool,
    trigger: str,
    trigger_task_uuid: Optional[str],
    full: bool,
    reports_only: bool,
    code_only: bool,
    data_only: bool,
    data_line: str,
    theme_only: bool,
    situation_detached: bool,
    literature_detached: bool,
    content_only: bool,
    incremental_country_codes: list[str],
    incremental_site: bool,
    release_plan: dict[str, Any],
    dirty_ids: list[int],
    pending_ids: list[int] | None,
    marked_before: str,
    suppress_subscription_dispatch: bool,
    publication_plan: dict[str, Any] | None,
    surveillance_rebuild_all: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Build the human-readable description parts and frozen task input_data payload."""
    description_parts = [
        f"Refresh and generate site data, then build Astro site for release job {job.job_id}.",
        (
            f"Incrementally archive raw crawler data to {raw_archive.branch}."
            if raw_archive.enabled
            else "Raw crawler archive publication disabled for this run."
        ),
        (
            f"Publish partitioned CSV/JSON/XLSX downloads to {branch}."
            if job.include_git_push
            else "GitHub download publication disabled for this run."
        ),
        f"Pages: {project_name}" if job.include_cloudflare_deploy else "Cloudflare deploy disabled.",
    ]
    if code_only:
        description_parts = [
            "Build and publish code/templates using the existing data snapshot; no collection or download generation."
        ]
    if reports_only:
        description_parts = [
            "Refresh report data, build report pages, publish changed files to Reports R2, and verify the public report host."
        ]
    if data_only:
        description_parts = (
            ["Build and publish a frozen literature generation independently of AI analysis and other data channels."]
            if data_line == "literature"
            else [f"Publish changed {data_line} JSON snapshots and their manifest without rebuilding static pages."]
        )
        if data_line == "surveillance" and job.include_git_push:
            description_parts.append(
                "Incrementally export and push matching CSV/JSON/XLSX downloads to GitHub before acknowledging these changes."
            )

    input_data: dict[str, Any] = {
        # Maintenance publications can refresh content/templates without
        # creating subscriber campaigns. Freeze this choice before queueing.
        **({"suppress_subscription_dispatch": True} if suppress_subscription_dispatch else {}),
        "release_job_id": job.job_id,
        "release_job_name": job.name,
        "priority": job.priority,
        "auto_after_crawls": job.auto_after_crawls,
        "include_git_push": bool(
            job.include_git_push
            and not reports_only
            and not code_only
            and (not data_only or data_line == "surveillance")
        ),
        "include_cloudflare_deploy": bool(
            job.include_cloudflare_deploy and not reports_only and not data_only
        ),
        "require_clean_worktree": job.require_clean_worktree,
        "download_repo_branch": branch,
        "deployment_branch": job.github_branch,
        "github_repo_url": download_repo_url,
        "download_url_base": download_url_base,
        "cloudflare_project_name": project_name,
        "commit_message_template": job.commit_message_template,
        "timezone": job.timezone or default_timezone,
        "trigger": trigger,
        "manual_trigger": manual,
        "incremental_country_codes": incremental_country_codes,
        "incremental_site": incremental_site,
        "full": full,
        "reports_only": reports_only,
        "code_only": code_only,
        "data_only": data_only,
        "data_line": data_line if data_only else None,
        "situation_detached": situation_detached,
        "literature_detached": literature_detached,
        # Freeze the selected publication contract. A configuration change
        # before execution/retry must not turn a static content task into
        # a surveillance exporter (or acknowledge unselected data rows).
        "content_only": content_only,
        "country_scopes_exact": True,
        "release_plan": release_plan,
        "continuation_dirty_ids": release_plan.get("continuation_dirty_ids", []),
        "continuation_parent_task_uuid": trigger_task_uuid if pending_ids is not None else None,
        "publish_dirty_ids": dirty_ids,
        "publish_dirty_before": None,
        "publish_selected_at": marked_before,
        "publish_change_count": len(dirty_ids),
        "trigger_task_uuid": trigger_task_uuid,
        "generated_paths": list(generated_paths),
        "raw_archive_enabled": bool(raw_archive.enabled and not data_only),
        "raw_archive_repo_url": raw_archive.repo_url,
        "raw_archive_branch": raw_archive.branch,
    }
    if surveillance_rebuild_all:
        input_data["surveillance_rebuild_all"] = True
    if publication_plan:
        input_data["publication_plan"] = dict(publication_plan)
        input_data["publication_expected_generations"] = dict(
            publication_plan.get("expected_generations", {})
        )
        if publication_plan.get("execution_mode") == "country_template":
            input_data["country_template_only"] = True
            input_data["country_template_codes"] = list(publication_plan.get("country_codes", []))
    if theme_only:
        input_data.update(
            theme_only=True,
            include_git_push=False,
            include_cloudflare_deploy=False,
            raw_archive_enabled=False,
            publish_dirty_ids=[],
            continuation_dirty_ids=[],
            publish_change_count=0,
            content_only=False,
            situation_detached=False,
            literature_detached=False,
        )
        description_parts = [
            "Publish and verify the frozen compatible theme candidate; no content, HTML or Pages build."
        ]
    return description_parts, input_data
