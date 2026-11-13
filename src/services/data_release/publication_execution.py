"""Plan guards and enqueue adapter for the existing release task system."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import select

from .artifact_checkpoint import source_fingerprint, rendering_configuration
from .papers_r2 import R2S3Client
from .surveillance_publish import _atomic_receipt


class PublicationPlanError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def task_identity(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "gids-publication-run:" + key))


def load_plan(data_dir: Path, plan_id: str, plan_digest: str | None = None) -> dict:
    try:
        if str(UUID(plan_id)) != plan_id:
            raise ValueError()
        path = data_dir / "publication-plans" / f"{plan_id}.json"
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError()
        plan = json.loads(path.read_bytes())
        expected = digest({k: v for k, v in plan.items() if k != "plan_digest"})
        if plan.get("plan_id") != plan_id or plan.get("plan_digest") != expected:
            raise ValueError()
        if plan_digest is not None and plan_digest != expected:
            raise PublicationPlanError("publication_plan_digest_mismatch")
        return plan
    except PublicationPlanError:
        raise
    except FileNotFoundError:
        raise PublicationPlanError("publication_plan_not_found", 404) from None
    except (OSError, ValueError, TypeError, KeyError):
        raise PublicationPlanError("publication_plan_invalid") from None


def configuration_digest(service, root: Path) -> str:
    from src.services.settings_service import system_settings_service
    config = service._config()
    values = config.model_dump(mode="json") if hasattr(config, "model_dump") else vars(config)
    return digest({"release": values, "rendering": rendering_configuration(root),
                   "cloudflare": system_settings_service.cloudflare_runtime(),
                   "github": system_settings_service.github_runtime()})


def prepare_execution(plan: dict, *, root: Path, data_dir: Path, service, kind: str,
                      channel: str | None, job_configuration: dict | None, rollback_of: str | None = None) -> None:
    from . import theme_publication as theme
    plan.update(execution_mode=None, blocked_reason=None, rollback_of=rollback_of)
    if kind == "auto":
        if plan["changed_files"] and {change["kind"] for change in plan["changed_files"]} == {"theme"}:
            kind = "theme"
        else:
            kind = "content" if plan["kind"] == "content" and len(plan["channels"]) == 1 else "auto"
            channel = plan["channels"][0] if kind == "content" else None
    execution = {"job_configuration_digest": digest(job_configuration),
                 "configuration_digest": configuration_digest(service, root)}
    if kind == "theme":
        config, reason = theme.configuration(service)
        if config is None:
            plan["blocked_reason"] = reason
            return
        directory = data_dir / "publication-plans" / plan["plan_id"]
        rollback_directory = None
        if rollback_of:
            target = load_plan(data_dir, rollback_of)
            rollback_directory = data_dir / "publication-plans" / rollback_of
            try:
                receipt = json.loads((rollback_directory / "theme-receipt.json").read_bytes())
                if target.get("execution_mode") != "theme" or not receipt.get("public_verified"):
                    raise ValueError()
            except (OSError, ValueError):
                raise PublicationPlanError("theme_rollback_target_unverified") from None
        prepared = theme.prepare(root=root, directory=directory, config=config, rollback_directory=rollback_directory)
        from .theme_preview import prepare_theme_previews
        try:
            prepare_theme_previews(root=root, directory=directory,
                public_origin=getattr(service._config(), "main_site_origin", "https://www.globalinfectiousdisease.com"))
        except (OSError, ValueError):
            pass
        execution.update(theme_prepared=prepared, theme_destination=theme.frozen_destination(config))
        plan.update(kind="theme", channels=["theme"], execution_mode="theme", content_record_count=0,
                    html_page_count=0, affected_templates=[], asset_compile_scope=["theme_v1"],
                    pages_deploy_required=False, worker_deploy_required=False, cost_estimate_confidence="high",
                    dirty_ids=[], pending_scopes={}, expected_generations={"theme": prepared["baseline_sha256"]})
        plan["warnings"] = ["theme_coverage_home_country_only", "public_visibility_requires_verification"]
    elif kind == "content":
        if channel not in {"surveillance", "situation", "literature"}:
            plan["blocked_reason"] = "publication_select_single_content_channel"
            return
        if not getattr(service._config(), f"{channel}_json_enabled", False):
            plan["blocked_reason"] = "json_channel_disabled"
            return
        config = service._r2_papers_config()
        if config is None:
            plan["blocked_reason"] = "publication_r2_credentials_unavailable"
            return
        ids = [row["id"] for row in plan.pop("_dirty_rows", []) if row["line"] == channel]
        if not ids:
            plan["blocked_reason"] = "no_publishable_changes_detected"
            return
        prefix = "site-data/research-publication" if channel == "literature" else f"site-data/{channel}"
        client = R2S3Client(config)
        try:
            body = client.get_object(f"{prefix}/current.json")
        finally:
            client.close()
        execution.update(source_fingerprint=source_fingerprint(root), channel=channel,
                         remote_baselines={channel: hashlib.sha256(body).hexdigest() if body is not None else None},
                         destination=digest([config.s3_endpoint, config.bucket]))
        plan.update(kind="content", channels=[channel], dirty_ids=ids, execution_mode="content",
                    expected_generations=dict(execution["remote_baselines"]),
                    pending_scopes={channel: plan["pending_scopes"].get(channel, [])},
                    pages_deploy_required=False, worker_deploy_required=False, html_page_count=0,
                    content_record_count=None, asset_compile_scope=[], cost_estimate_confidence="low")
        if channel == "literature":
            plan.update(html_page_count=None, asset_compile_scope=["research_projection"])
        plan["warnings"] = ["content_candidate_freezes_at_existing_export_snapshot", "public_visibility_requires_verification"]
    elif kind in {"template", "full"}:
        if (job_configuration or {}).get("include_cloudflare_deploy") is False:
            plan["blocked_reason"] = "publication_pages_deployment_disabled"
            return
        if kind == "template":
            plan.update(dirty_ids=[], pending_scopes={}, channels=[])
        else:
            rows = plan.get("_dirty_rows", [])
            selected = [row for row in rows if row["line"] not in {"surveillance", "situation", "literature"}
                        or not getattr(service._config(), f"{row['line']}_json_enabled", False)]
            channels = sorted({row["line"] for row in selected})
            plan.update(dirty_ids=sorted(row["id"] for row in selected), channels=channels,
                        pending_scopes={channel: plan["pending_scopes"].get(channel, []) for channel in channels})
        execution["source_fingerprint"] = source_fingerprint(root)
        execution["site_baseline"] = plan["expected_generations"].get("site")
        plan.update(kind=kind, execution_mode="code_only" if kind == "template" else "full",
                    pages_deploy_required=True, html_page_count=None, content_record_count=None,
                    asset_compile_scope=["astro_vite"], cost_estimate_confidence="unknown")
        plan["warnings"] = ["existing_static_pipeline_scope", "template_requires_unchanged_published_data"] if kind == "template" else ["maintenance_full_rebuild"]
        country_routes = {f"astro-site/src/pages/{locale}countries/[country]/index.astro" for locale in ("", "zh/", "fr/")}
        changed = {change["path"] for change in plan["changed_files"]}
        baseline = root / "exports/verified-pages-snapshot"
        inventory_blockers = {"published_dirty_source_baseline_unavailable", "published_source_baseline_unavailable",
            "published_source_revision_unavailable", "source_inventory_unavailable", "source_inventory_unstable",
            "source_symlink_requires_review", "rendering_configuration_unavailable"}
        if (kind == "template" and changed and changed.issubset(country_routes) and baseline.is_dir()
                and not set(plan.get("_inventory_warnings", [])).intersection(inventory_blockers)):
            try:
                metadata = json.loads((root / "astro-site/src/data/meta.json").read_bytes())
                codes = sorted({str(country["code"]).lower() for country in metadata["countries"]})
                paths = [f"{locale}countries/{code}/index.html" for code in codes for locale in ("", "zh/", "fr/")]
                if not codes or not all((baseline / path).is_file() for path in paths):
                    raise ValueError("new_routes_require_routing")
                if json.loads((baseline / "release.json").read_bytes()).get("release_id") != execution["site_baseline"]:
                    raise ValueError("country_baseline_mismatch")
                proof = json.loads((root / "exports/publicly-verified-site-release.json").read_bytes())
                baseline_identity = json.loads((baseline / "release.json").read_bytes())
                from .pipeline import _built_site_digest
                baseline_digest = _built_site_digest(baseline)
                if (proof.get("release_id") != execution["site_baseline"] or not proof.get("public_verified_at")
                        or not proof.get("build_digest") or proof["build_digest"] != baseline_identity.get("build_digest")
                        or proof["build_digest"] != baseline_digest):
                    raise ValueError("country_baseline_not_publicly_verified")
            except (OSError, ValueError, KeyError, TypeError):
                plan["warnings"].append("country_template_baseline_unavailable")
            else:
                execution["country_codes"] = codes
                execution["country_baseline_digest"] = baseline_digest
                execution["country_baseline_release_sha256"] = hashlib.sha256((baseline / "release.json").read_bytes()).hexdigest()
                plan.update(execution_mode="country_template", content_record_count=0, html_page_count=len(paths),
                            affected_templates=["country"], asset_compile_scope=["country_template"],
                            cost_estimate_confidence="high")
                plan["warnings"] = ["country_template_preserves_other_html", "template_requires_unchanged_published_data"]
    else:
        plan["blocked_reason"] = "publication_select_execution_scope"
        return
    plan.pop("_dirty_rows", None)
    if kind != "theme":
        from .publication_recovery import freeze_environment
        execution["environment"] = freeze_environment(service, job_configuration)
    plan.update(_execution=execution, executable=True, preview_only=False)


def validate_execution(plan: dict, *, root: Path, service, job_configuration: dict | None = None,
                       queued: bool = False, task_uuid: str | None = None, recovery: dict | None = None) -> None:
    from . import theme_publication as theme
    if not plan.get("executable"):
        raise PublicationPlanError(plan.get("blocked_reason") or "publication_plan_preview_only")
    if not queued and datetime.fromisoformat(plan["expires_at"]) <= datetime.now(timezone.utc):
        raise PublicationPlanError("publication_plan_expired", 410)
    execution = plan["_execution"]
    prepared = bool(queued and recovery and execution.get("environment"))
    if not prepared and (not queued or job_configuration is not None) and digest(job_configuration) != execution["job_configuration_digest"]:
        raise PublicationPlanError("publication_job_configuration_changed")
    if not prepared and not (queued and plan["execution_mode"] == "theme") and configuration_digest(service, root) != execution["configuration_digest"]:
        raise PublicationPlanError("publication_configuration_changed")
    if plan["execution_mode"] == "theme":
        config, reason = theme.configuration(service, frozen=execution["theme_destination"] if queued else None)
        if config is None:
            raise PublicationPlanError(reason)
        prepared = execution["theme_prepared"]
        if theme.destination(config) != prepared["destination"]:
            raise PublicationPlanError("theme_destination_changed")
        # Queued themes execute only the already compiled immutable candidate.
        # New source edits cannot change that task's meaning or retry output.
        if not queued:
            if theme.sha((root / "astro-site/scripts/build-theme.mjs").read_bytes()) != prepared["builder_sha256"]:
                raise PublicationPlanError("publication_source_changed")
            if prepared["source_sha256"] and theme.sha((root / "shared/site-theme-v1.json").read_bytes()) != prepared["source_sha256"]:
                raise PublicationPlanError("publication_source_changed")
            active = R2S3Client(config)
            try:
                body = active.get_object(theme.ENTRY)
            finally:
                active.close()
            if (theme.sha(body) if body is not None else None) != prepared["baseline_sha256"]:
                raise PublicationPlanError("publication_baseline_changed")
    else:
        if not prepared and source_fingerprint(root) != execution["source_fingerprint"]:
            raise PublicationPlanError("publication_source_changed")
        if plan["execution_mode"] in {"full", "code_only", "country_template"}:
            marker = root / "exports/successful-site-release.json"
            try:
                marker_data = json.loads(marker.read_bytes()) if marker.is_file() else {}
                current = marker_data.get("release_id")
            except (OSError, ValueError):
                raise PublicationPlanError("publication_baseline_unavailable") from None
            if current != execution["site_baseline"] and not (queued and task_uuid and marker_data.get("task_uuid") == task_uuid):
                raise PublicationPlanError("publication_baseline_changed")
            if plan["execution_mode"] == "country_template" and not (queued and task_uuid and marker_data.get("task_uuid") == task_uuid):
                from .pipeline import _built_site_digest
                baseline = root / "exports/verified-pages-snapshot"
                if (_built_site_digest(baseline) != execution["country_baseline_digest"]
                        or hashlib.sha256((baseline / "release.json").read_bytes()).hexdigest() != execution["country_baseline_release_sha256"]):
                    raise PublicationPlanError("publication_baseline_changed")
        if plan["execution_mode"] == "content" and not queued:
            config = service._r2_papers_config()
            if config is None or digest([config.s3_endpoint, config.bucket]) != execution["destination"]:
                raise PublicationPlanError("publication_destination_changed")
            channel = execution["channel"]
            prefix = "site-data/research-publication" if channel == "literature" else f"site-data/{channel}"
            client = R2S3Client(config)
            try:
                body = client.get_object(f"{prefix}/current.json")
            finally:
                client.close()
            if (hashlib.sha256(body).hexdigest() if body is not None else None) != execution["remote_baselines"][channel]:
                raise PublicationPlanError("publication_baseline_changed")


async def enqueue_plan(body, *, db, root: Path, data_dir: Path, service) -> dict:
    from src.domain import DataReleaseJob, Task
    plan = await asyncio.to_thread(load_plan, data_dir, body.plan_id, body.plan_digest)
    identity = task_identity(body.idempotency_key)
    existing = (await db.execute(select(Task).where(Task.task_uuid == identity))).scalar_one_or_none()
    if existing is not None:
        frozen = (existing.input_data or {}).get("publication_plan", {})
        if frozen.get("plan_digest") != body.plan_digest:
            raise PublicationPlanError("publication_idempotency_conflict")
        return {"task_uuid": identity, "status": existing.status.value, "job_id": plan["job_id"], "idempotent_replay": True}
    job = (await db.execute(select(DataReleaseJob).where(DataReleaseJob.job_id == plan["job_id"]))).scalar_one_or_none()
    if job is None:
        raise PublicationPlanError("publication_job_not_found", 404)
    job_config = {column.name: getattr(job, column.name) for column in DataReleaseJob.__table__.columns}
    await asyncio.to_thread(validate_execution, plan, root=root, service=service, job_configuration=job_config)
    frozen = {"plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "task_uuid": identity,
              "expected_generations": plan["_execution"].get("remote_baselines", {}),
              "execution_mode": plan["execution_mode"], "country_codes": plan["_execution"].get("country_codes", []),
              "job_configuration_digest": plan["_execution"]["job_configuration_digest"],
              "configuration_digest": plan["_execution"]["configuration_digest"],
              "expires_at": plan["expires_at"],
              "country_baseline_digest": plan["_execution"].get("country_baseline_digest"),
              "site_baseline": plan["_execution"].get("site_baseline"),
              "country_baseline_release_sha256": plan["_execution"].get("country_baseline_release_sha256")}
    mode = plan["execution_mode"]
    kwargs = {"theme_only": mode == "theme", "full": mode == "full", "code_only": mode in {"code_only", "country_template"},
              "data_only": mode == "content", "publication_plan": frozen}
    if mode == "content":
        kwargs.update(data_line=plan["channels"][0], pending_ids=plan["dirty_ids"])
    elif mode == "full":
        kwargs["pending_ids"] = plan["dirty_ids"]
    result = await service.trigger_job(plan["job_id"], manual=True, trigger="publication_plan", **kwargs)
    if result.get("status") == "skipped" and result.get("reason") != "idempotent_replay":
        raise PublicationPlanError("publication_" + result.get("reason", "enqueue_conflict"))
    return result


def theme_history(data_dir: Path) -> dict:
    items = []
    for path in (data_dir / "publication-plans").glob("*/theme-receipt.json"):
        try:
            receipt = json.loads(path.read_bytes())
            if receipt.get("public_verified"):
                items.append({"plan_id": path.parent.name, "created_at": receipt["public_verified_at"],
                              "version": receipt["version"], "compatibility": "v1", "public_verified": True})
        except (OSError, ValueError, KeyError):
            continue
    return {"items": sorted(items, key=lambda row: row["created_at"], reverse=True)[:100]}


def literature_receipt(root: Path, task_uuid: str) -> dict:
    """Only durable publisher evidence can prove a research pointer commit."""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", task_uuid):
        return {}
    run = root / "exports/research-runs" / task_uuid
    expected = f"literature-{task_uuid}"
    result = {}
    for name in ("authority-committed.json", "published.json"):
        try:
            path = run / name
            if path.stat().st_size > 1024 * 1024:
                continue
            value = json.loads(path.read_bytes())
            if value.get("release_id") != expected:
                continue
            if name == "authority-committed.json" and value.get("authoritative_committed") is True:
                result.update(authoritative_committed=True, committed_at=value.get("committed_at"),
                              version=value.get("generation"))
                if value.get("public_verification_required") is True:
                    result["public_verified"] = False
            elif name == "published.json" and value.get("json", {}).get("release_id") == expected:
                result["authoritative_committed"] = True
                verified = value.get("public_verification", {})
                if verified.get("status") == "verified":
                    result["public_verified"] = True
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return result
