"""Schemas for site data release workflow APIs."""

from typing import Any, List, Optional

from pydantic import BaseModel


class DataReleaseJobOut(BaseModel):
    job_id: str
    name: str
    enabled: bool
    priority: str
    auto_after_crawls: bool
    include_git_push: bool
    include_cloudflare_deploy: bool
    require_clean_worktree: bool
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    github_remote: str
    github_branch: Optional[str] = None
    cloudflare_project_name: Optional[str] = None
    commit_message_template: str
    notes: Optional[str] = None
    next_run_at: Optional[str] = None
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_status: str
    last_error: Optional[str] = None
    last_task_uuid: Optional[str] = None
    run_count: int = 0
    skipped_count: int = 0


class DataReleaseConfigOut(BaseModel):
    enabled: bool
    timezone: str
    poll_interval_seconds: int
    last_tick_at: Optional[str] = None
    jobs: List[DataReleaseJobOut] = []


class DataReleaseTriggerResult(BaseModel):
    job_id: str
    status: str
    task_uuid: Optional[str] = None
    reason: Optional[str] = None


class DataReleaseJobCreate(BaseModel):
    job_id: str
    name: str
    enabled: bool = True
    priority: str = "high"
    auto_after_crawls: bool = True
    include_git_push: bool = True
    include_cloudflare_deploy: bool = True
    require_clean_worktree: bool = True
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    github_remote: str = "origin"
    github_branch: Optional[str] = None
    cloudflare_project_name: Optional[str] = None
    commit_message_template: str = "chore(data-release): publish site data {timestamp}"
    notes: Optional[str] = None


class DataReleaseJobUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[str] = None
    auto_after_crawls: Optional[bool] = None
    include_git_push: Optional[bool] = None
    include_cloudflare_deploy: Optional[bool] = None
    require_clean_worktree: Optional[bool] = None
    interval_minutes: Optional[int] = None
    daily_time: Optional[str] = None
    timezone: Optional[str] = None
    github_remote: Optional[str] = None
    github_branch: Optional[str] = None
    cloudflare_project_name: Optional[str] = None
    commit_message_template: Optional[str] = None
    notes: Optional[str] = None


class DataReleaseGitCheckOut(BaseModel):
    env_var: str
    repo_url: Optional[str] = None
    branch: str
    raw_base_url: Optional[str] = None
    read_access_ok: bool
    write_access_ok: bool
    read_check_output: Optional[str] = None
    write_check_output: Optional[str] = None
    require_clean_worktree: bool
    dirty_release_paths: List[str] = []
    dirty_blocking_paths: List[str] = []


class DataReleaseCloudflareCheckOut(BaseModel):
    project_name: Optional[str] = None
    token_present: bool
    account_id_present: bool
    project_access_ok: bool
    error: Optional[str] = None


class DataReleaseCommandCheckOut(BaseModel):
    python_path: str
    python_exists: bool
    wrangler_available: bool
    wrangler_version: Optional[str] = None


class DataReleaseChecksOut(BaseModel):
    checked_at: str
    overall_ready: bool
    blockers: List[str] = []
    git: DataReleaseGitCheckOut
    cloudflare: DataReleaseCloudflareCheckOut
    commands: DataReleaseCommandCheckOut
    raw: Optional[dict[str, Any]] = None
