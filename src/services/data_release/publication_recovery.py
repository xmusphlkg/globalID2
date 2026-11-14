"""Task-local publication settings and fail-closed prepared-candidate recovery.

Secrets are never persisted here. A prepared task may finish using its original
destinations after unrelated settings/source edits, but may never build again.
"""
from __future__ import annotations

import copy
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qsl

from .surveillance_publish import _atomic_receipt


def _secret(name: str) -> bool:
    return any(word in name.lower().replace("-", "_") for word in ("secret", "token", "password", "access_key", "private_key",
        "credential", "api_key", "apikey", "authorization", "cookie", "signature", "signing_key"))


# Only established publication controls may be persisted. A newly introduced
# environment variable must be reviewed before joining this durable contract.
ENVIRONMENT_KEYS = frozenset("""
GIDS_ASTRO_BUILD_CONCURRENCY GIDS_ASTRO_RENDER_WORKERS GIDS_BUILD_CHECK_WORKERS
GIDS_BUILD_SEO_WORKERS GIDS_BUNDLE_ASSET_WORKERS GIDS_BUNDLE_HTML_WORKERS
GIDS_EXTERNAL_SURVEILLANCE_DATA GIDS_PAGES_DATA_WORKER GIDS_PAPERS_PROJECTION_V3
GIDS_PUBLIC_BASE_URL GIDS_RESEARCH_PUBLICATION_ENABLED GIDS_RESEARCH_STATIC_HOST
GIDS_RESEARCH_COLLECTION_RENDER_WORKERS
GIDS_RUNTIME_DATA_READY GIDS_RUNTIME_SITUATION_READY GIDS_STATIC_RENDER
PUBLIC_GA4_MEASUREMENT_ID PUBLIC_GIDS_MAINTENANCE_MESSAGE
PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN PUBLIC_GIDS_THEME_V1_URL
R2_ACCOUNT_ID R2_ENDPOINT_URL R2_PAPERS_BUCKET R2_PAPERS_MAX_TOTAL_BYTES
R2_PAPERS_PUBLIC_BASE_URL R2_PAPERS_UPLOAD_WORKERS R2_REPORTS_BUCKET R2_REPORTS_PUBLIC_BASE_URL
SUBSCRIPTIONS__DELIVERY_MODE SUBSCRIPTIONS__INTEGRATED_WEEKLY_START_WEEK
SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE
SUBSCRIPTIONS__PUBLIC_BASE_URL SUBSCRIPTIONS__REPORT_DIGEST_ENABLED SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK
SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK
SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE SUBSCRIPTIONS__REMOTE_ENVIRONMENT
SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC SUBSCRIPTIONS__D1_DATABASE_NAME SUBSCRIPTIONS__D1_DATABASE_ID
""".split())


def _safe_values(value):
    if isinstance(value, dict):
        for item in value.values():
            _safe_values(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _safe_values(item)
    elif isinstance(value, str) and "://" in value:
        parsed = urlsplit(value)
        if ((parsed.username and not (parsed.scheme == "ssh" and parsed.username == "git" and not parsed.password))
                or parsed.password or any(_secret(key) or key.lower() in {"key", "auth", "sig"}
                    for key, _ in parse_qsl(parsed.query))):
            raise ValueError("publication_credential_url_not_allowed")


def _json(value):
    return json.loads(json.dumps(value, default=str))


def configuration_environment(config) -> dict[str, str]:
    """Serialize typed settings for both root nesting and field-level aliases."""
    from src.core.config import DataReleaseSettings
    values = config.model_dump(mode="json") if hasattr(config, "model_dump") else vars(config)
    result = {}
    for name, value in values.items():
        field = DataReleaseSettings.model_fields.get(name)
        if field is None:
            continue
        encoded = json.dumps(value, separators=(",", ":")) if isinstance(value, (bool, int, float, dict, list)) else str(value or "")
        result[f"DATA_RELEASE__{name.upper()}"] = encoded
        if isinstance(field.validation_alias, str):
            result[field.validation_alias] = encoded
    return result


def freeze_environment(service, job_configuration: dict | None) -> dict:
    """Freeze resolved nonsecret settings, including defaults outside release config."""
    from src.services.data_release_service import DataReleaseJobConfig
    config = service._config()
    values = config.model_dump(mode="json") if hasattr(config, "model_dump") else vars(config)
    job_values = {key: value for key, value in (job_configuration or {}).items()
                  if key in {field.name for field in fields(DataReleaseJobConfig)}}
    job = DataReleaseJobConfig(**{"job_id": "site-release", "name": "Site", **job_values})
    resolve = lambda name, default, *args: getattr(service, name, lambda *a: default)(*args)
    branch = resolve("_download_repo_branch", job.github_branch, job)
    project = resolve("_cloudflare_project_name", job.cloudflare_project_name, job.cloudflare_project_name)
    job_values.update(github_branch=branch, cloudflare_project_name=project)
    raw = resolve("_raw_archive_runtime", SimpleNamespace(enabled=False, repo_url="", branch="main"))
    env = {**resolve("_env_file_values", {}), **os.environ}
    env = {key: value for key, value in env.items() if key in ENVIRONMENT_KEYS}
    result = _json({"schema": 1, "release": {k: v for k, v in values.items() if not _secret(k)},
        "job": {"job_id": job.job_id, "name": job.name, **job_values},
        "cloudflare_account_id": resolve("_cloudflare_account_id", ""),
        "download_repo_url": resolve("_download_repo_url", ""),
        "download_repo_branch": branch, "download_url_base": resolve("_download_repo_raw_base", "", job),
        "project_name": project, "raw_archive": vars(raw), "environment": env})
    _safe_values(result)
    return result


def replay_environment(service, frozen: dict):
    """Shallow-copy the service so concurrent tasks retain their own settings."""
    from src.services.data_release_service import DataReleaseJobConfig
    from .publication_execution import PublicationPlanError
    if frozen.get("schema") != 1:
        raise PublicationPlanError("publication_execution_environment_invalid")
    _safe_values(frozen)
    live = service._config()
    live_account = service._cloudflare_account_id()
    # A credential rotation in the same account is allowed; an account switch
    # needs explicit intervention rather than assigning new credentials blindly.
    if live_account != frozen["cloudflare_account_id"]:
        raise PublicationPlanError("publication_credential_account_changed")
    if ((getattr(live, "r2_account_id", "") or live_account)
            != (frozen["release"].get("r2_account_id") or frozen["cloudflare_account_id"])):
        raise PublicationPlanError("publication_credential_account_changed")
    values = live.model_dump() if hasattr(live, "model_dump") else vars(live)
    values = {**values, **frozen["release"]}
    config = live.model_copy(update=values) if hasattr(live, "model_copy") else SimpleNamespace(**values)
    scoped = copy.copy(service)
    scoped._config = lambda: config
    scoped._cloudflare_account_id = lambda: frozen["cloudflare_account_id"]
    scoped._download_repo_url = lambda: frozen["download_repo_url"]
    scoped._download_repo_branch = lambda job: frozen["download_repo_branch"]
    scoped._download_repo_raw_base = lambda job: frozen["download_url_base"]
    scoped._cloudflare_project_name = lambda project: frozen["project_name"]
    archive = dict(frozen["raw_archive"])
    if "repository_dir" in archive:
        archive["repository_dir"] = Path(archive["repository_dir"])
    scoped._raw_archive_runtime = lambda: SimpleNamespace(**archive)
    async def jobs():
        return [DataReleaseJobConfig(**frozen["job"])]
    scoped.load_jobs = jobs
    live_env = getattr(service, "_env_value", lambda name, default="": os.getenv(name, default))
    scoped._env_value = lambda name, default="": (live_env(name, default) if _secret(name)
        else frozen["environment"].get(name, default))
    command = scoped._run_logged_command
    async def run(*args, **kwargs):
        # Unknown typed settings retain their parser defaults; blanking whole
        # prefixes turns bool/int fields into invalid values. Every known
        # release field is replayed with its actual type and validation alias.
        blocked = {key: None for key in ENVIRONMENT_KEYS}
        kwargs["env"] = {**blocked, **frozen["environment"], **configuration_environment(config), **(kwargs.get("env") or {}),
            "GIDS_PUBLICATION_RECOVERY_ONLY": "1" if getattr(scoped, "_publication_recovery", None) else "0"}
        return await command(*args, **kwargs)
    scoped._run_logged_command = run
    return scoped


def recovery_path(root: Path, task) -> Path:
    if Path(task.task_uuid).name != task.task_uuid or task.task_uuid in {"", ".", ".."}:
        raise ValueError("Invalid recovery task UUID")
    return root / "exports/publication-recovery" / f"{task.task_uuid}.json"


def _binding(task) -> dict:
    from .publication_execution import digest
    return {"task_uuid": task.task_uuid, "selection_digest": digest(task.input_data or {})}


def load_recovery(root: Path, task) -> dict | None:
    path = recovery_path(root, task)
    if not path.exists():
        return None
    try:
        if path.is_symlink():
            raise ValueError()
        value = json.loads(path.read_bytes())
        if value.get("schema") != 1 or value.get("binding") != _binding(task):
            raise ValueError()
        ready = root / value["ready_path"]
        if (ready.is_symlink() or not ready.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(ready.read_bytes()).hexdigest() != value["ready_sha256"]):
            raise ValueError()
        for relative, expected in value.get("additional_files", {}).items():
            path = root / relative
            if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError("publication_prepared_candidate_unavailable") from error


def save_recovery(root: Path, task, ready: Path, **context) -> None:
    if not (task.input_data or {}).get("publication_plan"):
        return
    path = recovery_path(root, task)
    if path.exists():
        load_recovery(root, task)
        return
    if "checks" in context:
        # Preflight diagnostic output may include transport credentials. Only
        # these resolved operational values are needed to finish this candidate.
        checks = context["checks"]
        context["checks"] = {"overall_ready": bool(checks.get("overall_ready")),
            "cloudflare": {key: checks.get("cloudflare", {}).get(key) for key in ("production_branch", "subdomain")},
            "git": {"ssh_transport": checks.get("git", {}).get("ssh_transport")}}
    _safe_values(context)
    _atomic_receipt(path, {"schema": 1, "binding": _binding(task),
        "ready_path": str(ready.relative_to(root)),
        "ready_sha256": hashlib.sha256(ready.read_bytes()).hexdigest(), **context})


def require_candidate(service, ready: bool) -> None:
    if getattr(service, "_publication_recovery", None) and not ready:
        raise ValueError("publication_prepared_candidate_unavailable")


def reports_fence(service, root: Path, frozen: dict | None = None) -> dict:
    """Refuse an old static retry after the independent reports channel advanced."""
    from .papers_r2 import R2S3Client
    from .publication_execution import digest
    config = service._r2_reports_config()
    if config is None:
        raise ValueError("publication_frozen_destination_unavailable")
    candidate = digest(json.loads((root / "exports/reports-site" / config.manifest_key).read_bytes()))
    client = R2S3Client(config)
    try:
        body = client.get_object(config.manifest_key)
    finally:
        client.close()
    remote = digest(json.loads(body)) if body is not None else None
    if frozen is not None:
        if candidate != frozen["candidate"] or remote not in {frozen["baseline"], frozen["candidate"]}:
            raise ValueError("publication_reports_baseline_changed")
        return frozen
    return {"candidate": candidate, "baseline": remote}
