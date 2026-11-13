"""Build, validate and split the local site before any remote publication."""

from __future__ import annotations

import asyncio
import logging
import json
import hashlib
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .build_reuse import BuildReuse, stamp_homepages

logger = logging.getLogger(__name__)
CACHE_ERRORS = (OSError, ValueError, TypeError, subprocess.SubprocessError)


async def _save_receipt(reuse, inputs, *, stamped):
    if reuse is None or inputs is None:
        return False
    try:
        return await asyncio.to_thread(reuse.save, inputs, stamped=stamped)
    except CACHE_ERRORS:
        logger.warning("Could not persist optional build reuse receipt", exc_info=True)
        return False


async def build_release_site(
    *, service: Any, task: Any, runtime: Any, job: Any, python_path: Path,
    git_env: dict[str, str], release_identity: dict[str, Any],
    r2_papers_enabled: bool, r2_reports_enabled: bool,
    built_site_digest: Callable[[Path], str],
) -> dict[str, Any]:
    """Run local build gates in order and bind deployment identity to output bytes."""
    split = bool(job.include_cloudflare_deploy or r2_papers_enabled or r2_reports_enabled)
    config = service._config()
    if ((runtime.astro_dir / "src/lib/surveillance-data.ts").is_file()
            and not getattr(config, "surveillance_json_enabled", False)):
        raise RuntimeError("Runtime data templates require an initialized JSON channel and Worker; enable DATA_RELEASE__SURVEILLANCE_JSON_ENABLED after initialization before publishing templates")
    task_data = dict(getattr(task, "input_data", None) or {})
    if task_data.get("country_template_only"):
        if not task_data.get("code_only") or not task_data.get("publication_plan"):
            raise ValueError("Country template compilation requires a frozen code plan")
        baseline = runtime.root_dir / "exports/verified-pages-snapshot"
        frozen_plan = task_data["publication_plan"]
        candidate = runtime.root_dir / "exports/country-template-runs" / task.task_uuid
        from .surveillance_publish import _atomic_receipt
        ready_path = candidate.with_suffix(".ready.json")
        signature = {"plan_digest": frozen_plan.get("plan_digest"),
                     "baseline_digest": frozen_plan["country_baseline_digest"],
                     "baseline_release_sha256": frozen_plan["country_baseline_release_sha256"],
                     "countries": task_data["country_template_codes"]}
        if ready_path.is_file():
            ready = json.loads(ready_path.read_bytes())
            if (ready.get("signature") != signature or await asyncio.to_thread(built_site_digest, candidate) != ready.get("candidate_digest")
                    or hashlib.sha256((candidate / "release.json").read_bytes()).hexdigest() != signature["baseline_release_sha256"]):
                raise ValueError("Frozen country candidate integrity check failed")
            receipt = ready["receipt"]
        else:
            if candidate.exists():
                await asyncio.to_thread(shutil.rmtree, candidate)
            await service._run_logged_command(task.task_uuid, title="Compile Country Template",
                cmd=["node", "--experimental-strip-types", "scripts/build-country-template.mjs",
                     "--baseline", str(baseline), "--output", str(candidate), "--countries",
                     ",".join(task_data.get("country_template_codes") or []),
                     "--baseline-digest", str(frozen_plan["country_baseline_digest"]),
                     "--baseline-release-sha256", str(frozen_plan["country_baseline_release_sha256"])],
                cwd=runtime.astro_dir,
                env={"GIDS_RUNTIME_DATA_READY": "1", "GIDS_EXTERNAL_SURVEILLANCE_DATA": "1",
                     "GIDS_RUNTIME_SITUATION_READY": "1" if getattr(config, "situation_json_enabled", False) else "0",
                     "GIDS_RESEARCH_PUBLICATION_ENABLED": "1" if getattr(config, "literature_json_enabled", False) else "0",
                     "PUBLIC_GIDS_THEME_V1_URL": str(getattr(config, "theme_stylesheet_url", "") or ""),
                     "NODE_OPTIONS": "--max-old-space-size=12288",
                     "PUBLIC_GIDS_RELEASE_ID": release_identity["release_id"],
                     "PUBLIC_GIDS_SOURCE_COMMIT": release_identity["source_commit"],
                     "PUBLIC_GIDS_SOURCE_BRANCH": release_identity["source_branch"],
                     "PUBLIC_GIDS_DEPLOY_BRANCH": release_identity["deployment_branch"],
                     "PUBLIC_GIDS_BUILT_AT": release_identity["built_at"]},
                metadata={"event": "country_template_compile", "release_job_id": job.job_id},
                timeout_seconds=runtime.astro_build_timeout_seconds)
            receipt = json.loads((candidate / "country-template-receipt.json").read_bytes())
            expected = {f"{locale}countries/{code}/index.html" for code in task_data["country_template_codes"] for locale in ("", "zh/", "fr/")}
            if (receipt.get("family") != "country" or set(receipt.get("pages", [])) != expected
                    or receipt.get("content_render_count") != 0 or receipt.get("papers_render_count") != 0
                    or receipt.get("reports_render_count") != 0):
                raise ValueError("Country compiler output does not match the frozen plan")
            (candidate / "country-template-receipt.json").unlink()
            await asyncio.to_thread(_atomic_receipt, ready_path, {
                "signature": signature, "candidate_digest": await asyncio.to_thread(built_site_digest, candidate),
                "receipt": receipt})
        dist = runtime.astro_dir / "dist"
        if dist.exists():
            await asyncio.to_thread(shutil.rmtree, dist)
        await asyncio.to_thread(shutil.copytree, candidate, dist)
        from .release_binding import PAPERS_FIELDS
        prior = json.loads((candidate / "release.json").read_bytes())
        release_identity = {**release_identity, **{key: prior[key] for key in PAPERS_FIELDS if key in prior},
                            "build_digest": await asyncio.to_thread(built_site_digest, dist)}
        await service._record_release_checkpoint(task.task_uuid, "country_template", receipt)
        await runtime.task_manager.add_workbook_entry(task.task_uuid, entry_type="info", title="Country Template Compiled",
            content=json.dumps(receipt), content_type="json", metadata={"event": "country_template_compiled", **receipt})
        return release_identity
    external_surveillance = bool(getattr(config, "surveillance_json_enabled", False)
        and (task_data.get("content_only") or task_data.get("code_only")))
    reuse = None
    inputs = None
    matched = False
    reason = "cache-unavailable"
    try:
        reuse = BuildReuse(runtime.root_dir, split=split, configuration={
            "settings": config.model_dump() if hasattr(config, "model_dump") else vars(config),
            "job": job.model_dump() if hasattr(job, "model_dump") else vars(job),
            "deployment_branch": release_identity["deployment_branch"],
            "r2_papers": r2_papers_enabled, "r2_reports": r2_reports_enabled,
            "external_surveillance": external_surveillance,
            "pages_data_worker": bool(getattr(config, "pages_data_worker_enabled", False)),
        })
        inputs = await asyncio.to_thread(reuse.inputs)
        matched = await asyncio.to_thread(reuse.matches, inputs)
        reason = reuse.reason
    except CACHE_ERRORS:
        logger.warning("Build reuse unavailable; running normal validation", exc_info=True)
        reuse = None
    if matched:
        stamped = await asyncio.to_thread(stamp_homepages, runtime.astro_dir / "dist", release_identity)
        if await _save_receipt(reuse, inputs, stamped=stamped):
            await runtime.task_manager.add_workbook_entry(
                task.task_uuid, entry_type="info", title="Validated Site Partitions Reused",
                content="Rendering inputs and all split artifacts match a completed local validation. Skipped Astro, output audits and bundle splitting; publication still reconciles its destinations.",
                content_type="text", metadata={"event": "site_build_reused", "release_job_id": job.job_id},
            )
            if job.include_cloudflare_deploy:
                release_identity = {**release_identity, "build_digest": await asyncio.to_thread(
                    built_site_digest, runtime.astro_dir / "dist")}
            return release_identity
        reason = "receipt-not-promoted"
    await runtime.task_manager.add_workbook_entry(
        task.task_uuid, entry_type="info", title="Site Build Required",
        content=f"Validated partition reuse unavailable: {reason}. Running the incremental renderer and its validation gates.",
        content_type="text", metadata={"event": "site_build_required", "reason": reason},
    )
    # Complete every local, side-effect-free release gate before publishing to
    # remote repositories. A failed site build must never leave downloads and
    # raw archives ahead of the production site.
    await runtime.task_manager.update_task_progress(task.task_uuid, 45)
    await service._run_logged_command(
        task.task_uuid,
        title="Build Astro Site",
        cmd=["npm", "run", "build"],
        cwd=runtime.astro_dir,
        env={
            # Publication latency takes priority over minimizing heap use.
            # The production worker has a 60 GiB memory budget.
            "NODE_OPTIONS": "--max-old-space-size=12288",
            "GLOBALID_SKIP_SITE_DATA_GENERATION": "1",
            "GIDS_EXTERNAL_SURVEILLANCE_DATA": "1" if external_surveillance else "0",
            "GIDS_RUNTIME_DATA_READY": "1" if getattr(config, "surveillance_json_enabled", False) else "0",
            "GIDS_RUNTIME_SITUATION_READY": "1" if getattr(config, "situation_json_enabled", False) else "0",
            "GIDS_PAGES_DATA_WORKER": "1" if getattr(config, "pages_data_worker_enabled", False) else "0",
            "GIDS_RESEARCH_PUBLICATION_ENABLED": "1" if getattr(config, "literature_json_enabled", False) else "0",
            "PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN": "1" if getattr(config, "literature_json_enabled", False) else "0",
            "PUBLIC_GIDS_THEME_V1_URL": str(getattr(config, "theme_stylesheet_url", "") or ""),
            "PUBLIC_GIDS_RELEASE_ID": release_identity["release_id"],
            "PUBLIC_GIDS_SOURCE_COMMIT": release_identity["source_commit"],
            "PUBLIC_GIDS_SOURCE_BRANCH": release_identity["source_branch"],
            "PUBLIC_GIDS_DEPLOY_BRANCH": release_identity["deployment_branch"],
            "PUBLIC_GIDS_BUILT_AT": release_identity["built_at"],
        },
        metadata={"event": "astro_build", "release_job_id": job.job_id},
        timeout_seconds=runtime.astro_build_timeout_seconds,
    )
    await service._run_logged_command(
        task.task_uuid,
        title="Validate Situation Release Gate",
        cmd=service._validate_situation_release_command(python_path=python_path),
        cwd=runtime.root_dir,
        env=git_env,
        metadata={"event": "situation_release_gate", "release_job_id": job.job_id},
        timeout_seconds=120,
    )
    if job.include_cloudflare_deploy or r2_papers_enabled or r2_reports_enabled:
        # Move long-tail research pages out of the Pages upload set before the
        # deployable digest is computed. Local npm builds keep a full dist.
        await service._run_logged_command(
            task.task_uuid,
            title="Split Papers Bundle",
            cmd=service._split_papers_bundle_command(python_path=python_path),
            cwd=runtime.root_dir,
            env=git_env,
            metadata={"event": "split_papers_bundle", "release_job_id": job.job_id},
            timeout_seconds=1800,
        )
    if job.include_cloudflare_deploy or r2_reports_enabled:
        await service._run_logged_command(
            task.task_uuid,
            title="Split Reports Bundle",
            cmd=service._split_reports_bundle_command(python_path=python_path),
            cwd=runtime.root_dir,
            env=git_env,
            metadata={"event": "split_reports_bundle", "release_job_id": job.job_id},
            timeout_seconds=1800,
        )
    stamped = await asyncio.to_thread(stamp_homepages, runtime.astro_dir / "dist", release_identity)
    await _save_receipt(reuse, inputs, stamped=stamped)
    if job.include_cloudflare_deploy:
        # The public manifest identifies these exact bytes as well as the
        # source revision. A retry may rebuild different facts at the same
        # revision and must not verify an earlier production manifest.
        release_identity = {
            **release_identity,
            "build_digest": await asyncio.to_thread(
                built_site_digest, runtime.astro_dir / "dist"
            ),
        }
    return release_identity
