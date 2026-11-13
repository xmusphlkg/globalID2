"""Read-only publication discovery, deliberately separate from execution.

Plans freeze observations for review. Local receipts are not remote CAS tokens,
and neither a plan ID nor its digest is permission to enqueue a release.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from .artifact_checkpoint import rendering_configuration
from .channel_control import is_automatic_json_paused

JSON_CHANNELS = ("surveillance", "situation", "literature")
SOURCE_PREFIXES = ("astro-site/", "src/", "scripts/", "configs/", "shared/", "cloudflare/")
SOURCE_FILES = {"requirements.txt", "requirements.in", "pyproject.toml", "main.py"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def capabilities(config: Any) -> dict:
    from .theme_publication import configuration as theme_configuration
    theme_config, theme_reason = theme_configuration()
    channels = []
    for channel in JSON_CHANNELS:
        enabled = bool(getattr(config, f"{channel}_json_enabled", False))
        paused = is_automatic_json_paused(channel)
        channels.append({
            "id": channel, "label": channel.title(), "enabled": enabled,
            "automatic": enabled and not paused, "rollback": False,
            "realtime_controls": True,
            "supported_modes": ["content"] if enabled else [],
            "reason": ("json_channel_disabled" if not enabled else "automatic_publication_paused" if paused else None),
        })
    channels.append({"id": "knowledge", "label": "Knowledge", "enabled": False,
                     "automatic": False, "rollback": False, "realtime_controls": False, "supported_modes": [],
                     "reason": "static_publication_only"})
    return {"schema_version": 1, "channels": channels, "plan_preview": True,
            "plan_execution": True, "rollback": theme_config is not None,
            "legacy_modes": ["full", "reports_only", "code_only"],
            "modes": [{"id": kind, "supported": theme_config is not None if kind == "theme" else kind != "routing",
                       "reason": theme_reason if kind == "theme" else "routing_requires_existing_deployment_workflow" if kind == "routing" else None}
                      for kind in ("content", "theme", "template", "routing", "full")]}


def classify_change(path: str) -> dict[str, str]:
    """Conservative dependency classification, never inferred from .css alone."""
    if path == "shared/site-theme-v1.json":
        kind, reason = "theme", "theme_candidate_requires_compatibility_validation"
    elif path.startswith("astro-site/src/data/") and path.endswith(".json") or path == "configs/literature/weekly_ai_reviews.json":
        kind, reason = "content", "content_projection_changed"
    elif path.startswith("cloudflare/") or path.endswith(("_routes.json", "_redirects", "_headers")):
        kind, reason = "routing", "delivery_or_worker_configuration_changed"
    elif path.startswith(("astro-site/src/pages/", "astro-site/src/layouts/", "astro-site/src/components/", "astro-site/src/styles/", "astro-site/src/lib/")):
        kind, reason = "template", "html_assets_or_component_contract_may_change"
    else:
        kind, reason = "full", "shared_input_or_toolchain_requires_conservative_rebuild"
    return {"path": path, "kind": kind, "reason": reason}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=30).stdout


def _source_path(path: str) -> bool:
    parts = Path(path).parts
    return ((path.startswith(SOURCE_PREFIXES) or path in SOURCE_FILES)
            and not any(part in {"node_modules", "__pycache__", "dist", ".astro", ".wrangler"} or part.startswith(".env") for part in parts)
            and not path.startswith(("astro-site/public/site-data/", "astro-site/src/data/_release-projection/"))
            and not re.search(r"wrangler\.(?:run\.|generated\.)", path))


def _read_receipt(root: Path, name: str) -> dict | None:
    try:
        path = root / name
        if path.stat().st_size > 32 * 1024 * 1024:
            return None
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or not isinstance(value.get("release_id"), str) or not value["release_id"]:
            return None
        return value
    except (OSError, ValueError):
        return None


def inspect_source(root: Path) -> dict:
    warnings = ["local_receipts_are_not_verified_remote_pointer_baselines"]
    site = _read_receipt(root, "exports/successful-site-release.json")
    research = _read_receipt(root, "exports/research-current-receipt.json")
    revision = None
    paths: list[str] = []
    fingerprints = {}
    base = (site or {}).get("source_commit")
    if (site or {}).get("commit_dirty"):
        warnings.append("published_dirty_source_baseline_unavailable")
    try:
        revision = _git(root, "rev-parse", "HEAD").decode().strip()
        if not isinstance(base, str) or not re.fullmatch(r"[a-f0-9]{40,64}", base):
            base = revision
            warnings.append("published_source_baseline_unavailable")
        try:
            # Include both sides of moves. Rename-only output can otherwise
            # hide a removed template when its new path is outside site inputs.
            changed = _git(root, "diff", "--no-renames", "--name-only", "-z", base, "--")
        except subprocess.CalledProcessError:
            warnings.append("published_source_revision_unavailable")
            changed = _git(root, "diff", "--no-renames", "--name-only", "-z", "HEAD", "--")
        untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        paths = sorted({p for p in (changed + untracked).decode().split("\0") if p and _source_path(p)})
        for relative in paths:
            path = root / relative
            if path.is_symlink():
                fingerprints[relative] = {"symlink": os.readlink(path)}
                warnings.append("source_symlink_requires_review")
            elif path.is_file():
                before = path.stat()
                with path.open("rb") as stream:
                    fingerprints[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
                if path.stat() != before:
                    warnings.append("source_inventory_unstable")
            else:
                fingerprints[relative] = None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        warnings.append("source_inventory_unavailable")
    return {"revision": revision, "changes": [classify_change(p) for p in paths],
            "fingerprints": fingerprints, "warnings": sorted(set(warnings)),
            "generations": {"site": (site or {}).get("release_id"),
                            "surveillance": None, "situation": None,
                            "literature": (research or {}).get("release_id")}}


def create_plan(*, root: Path, data_dir: Path, job_id: str, config: Any,
                dirty_rows: list[dict], job_configuration: dict | None = None,
                now: datetime | None = None, execution_service=None,
                requested_kind: str = "auto", channel: str | None = None, rollback_of: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    source = inspect_source(root)
    try:
        render_configuration = rendering_configuration(root)
    except (OSError, ValueError, AttributeError, TypeError):
        render_configuration = None
        source["warnings"].append("rendering_configuration_unavailable")
    changes = source["changes"]
    kinds = {change["kind"] for change in changes}
    incomplete = any(w in source["warnings"] for w in (
        "source_inventory_unavailable", "source_inventory_unstable", "published_source_baseline_unavailable",
        "published_source_revision_unavailable", "published_dirty_source_baseline_unavailable", "rendering_configuration_unavailable",
    ))
    # Preserve the narrow publication kind when the inventory contains one
    # dependency class.  The previous priority list started with ``full``;
    # since ``inspect_source`` always includes shared/toolchain inputs in some
    # test fixtures this silently relabelled a theme-only change as a full
    # rebuild and made the theme preview contract unusable.  Mixed classes are
    # intentionally conservative and still require a full publication.
    if incomplete:
        kind = "full"
    elif len(kinds) == 1:
        kind = next(iter(kinds), "content")
    elif "full" in kinds:
        kind = "full"
    else:
        kind = "full" if len(kinds) > 1 else "content"
    scopes: dict[str, list[str]] = {}
    for row in dirty_rows:
        scopes.setdefault(row["line"], []).append(row["scope_key"])
    scopes = {line: sorted(set(values)) for line, values in sorted(scopes.items())}
    static_content = any(line not in JSON_CHANNELS or not getattr(config, f"{line}_json_enabled", False) for line in scopes)
    # Even theme candidates require a template publication until the executor
    # and compatibility coverage are proven. Never advertise a zero-page path.
    pages = incomplete or static_content or bool(kinds - {"content"}) or "content" in kinds
    families = set()
    for change in changes:
        path = change["path"]
        if path.startswith("astro-site/src/pages/countries/"):
            families.add("country")
        elif path.startswith("astro-site/src/pages/diseases/"):
            families.add("disease")
        elif path.startswith("astro-site/src/pages/research/"):
            families.add("research")
        elif path.startswith("astro-site/src/pages/situation/"):
            families.add("situation")
        elif path.startswith("astro-site/src/pages/"):
            families.add("other_pages")
        elif change["kind"] in {"template", "full"}:
            families.add("shared")
    warnings = source["warnings"] + ["preview_only_plan_execution_not_available"]
    if "theme" in kinds:
        warnings.append("independent_theme_publication_not_available")
    if not changes and not dirty_rows and not incomplete:
        warnings.append("no_publishable_changes_detected")
    config_values = config.model_dump(mode="json") if hasattr(config, "model_dump") else vars(config)
    input_digest = _digest({"revision": source["revision"], "files": source["fingerprints"],
                            "configuration": config_values, "dirty_rows": dirty_rows,
                            "rendering_configuration": render_configuration,
                            "generations": source["generations"], "job_id": job_id,
                            "job_configuration": job_configuration})
    plan = {
        "plan_id": str(uuid4()), "job_id": job_id,
        "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=15)).isoformat(),
        "kind": kind, "channels": sorted(scopes), "source_revision": source["revision"],
        "input_digest": input_digest, "expected_generations": source["generations"],
        # Existing template/full builds can still render bodies through shared
        # inputs. A source-only change is not evidence of zero content work.
        "affected_templates": sorted(families), "content_record_count": None if dirty_rows or pages else 0,
        "html_page_count": None if pages else 0,
        "asset_compile_scope": ["astro_vite"] if pages else [],
        "pages_deploy_required": pages,
        "worker_deploy_required": any(c["path"].startswith("cloudflare/") for c in changes),
        "validation_scope": ["source", "dirty_scopes", "schema", "dependencies", "remote_baselines_at_execution"],
        "cost_estimate_confidence": "unknown" if pages else "low", "changed_files": changes,
        "dirty_ids": sorted(row["id"] for row in dirty_rows), "pending_scopes": scopes,
        "warnings": sorted(set(warnings)), "preview_only": True, "executable": False,
    }
    if execution_service is not None:
        from .publication_execution import prepare_execution
        plan["_dirty_rows"] = dirty_rows
        plan["_inventory_warnings"] = list(plan["warnings"])
        prepare_execution(plan, root=root, data_dir=data_dir, service=execution_service, kind=requested_kind,
                          channel=channel, job_configuration=job_configuration, rollback_of=rollback_of)
        plan.pop("_dirty_rows", None)
        plan.pop("_inventory_warnings", None)
    plan["plan_digest"] = _digest(plan)
    directory = data_dir / "publication-plans"
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".plan-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(plan, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{plan['plan_id']}.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return plan


def read_usage(path: Path, *, now: datetime | None = None) -> dict:
    """Read the collector's cache; never make Cloudflare calls on page views."""
    now = now or datetime.now(timezone.utc)
    unknown = {"status": "unknown", "plan": "unknown", "reason": "usage_snapshot_unavailable"}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Oversized usage snapshot")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Invalid usage snapshot")
        from dashboard.api.schemas.publication import PublicationUsageOut
        result = PublicationUsageOut.model_validate(value).model_dump(mode="json")
        observed = datetime.fromisoformat(result["observed_at"]) if result["observed_at"] else None
        if observed is None or observed.tzinfo is None or observed > now + timedelta(minutes=1):
            raise ValueError("Invalid usage observation time")
        start = datetime.fromisoformat(result["period_start"]) if result["period_start"] else None
        end = datetime.fromisoformat(result["period_end"]) if result["period_end"] else None
        # Metric availability and billing permissions are independent. An
        # observed adaptive count remains useful when subscriptions returns
        # 403; the separate plan/limit fields must continue to say unknown.
        complete = (result["account_total"] is not None
                    and start is not None and end is not None and start.tzinfo is not None and end.tzinfo is not None
                    and start <= observed < end and result["source"])
        if result["plan"] == "unknown":
            result.update(limit=None, remaining=None, projected_usage=None)
        stale = now - observed > timedelta(minutes=15) or (end is not None and end.tzinfo is not None and now >= end)
        result["status"] = "unknown" if not complete else "stale" if stale else "known"
        if result["status"] != "known":
            result["reason"] = result["reason"] or ("usage_snapshot_stale" if stale and complete else "account_usage_unavailable")
        return result
    except FileNotFoundError:
        return unknown
    except (OSError, ValueError, TypeError):
        return {**unknown, "reason": "usage_snapshot_invalid"}
