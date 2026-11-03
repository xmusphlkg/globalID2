"""Publish new hashed assets before R2 HTML starts referencing them."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import httpx

from .artifact_checkpoint import link_immutable_or_copy
from .papers_bundle import evaluate_pages_budget


class SharedAssetVerificationError(RuntimeError):
    release_stage = "shared_assets_compatibility_deploy"


def verified_snapshot(snapshot: Path, live_identity: dict) -> bool:
    """Accept only a complete local copy of the currently served release."""
    from .pipeline import _built_site_digest

    try:
        identity = json.loads((snapshot / "release.json").read_text())
        digest = identity.get("build_digest")
        return bool(
            digest
            and identity.get("release_id")
            and identity.get("release_id") == live_identity.get("release_id")
            and digest == live_identity.get("build_digest")
            and _built_site_digest(snapshot) == digest
        )
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def retain_verified_snapshot(current: Path, destination: Path, identity: dict) -> None:
    """Keep independent bytes for subsequent compatibility deployments."""
    if not verified_snapshot(current, identity):
        raise RuntimeError("Verified Pages snapshot does not match its release manifest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pages-snapshot-", dir=destination.parent) as temp:
        temp_path = Path(temp)
        candidate, old = temp_path / "candidate", temp_path / "old"
        shutil.copytree(current, candidate)
        if not verified_snapshot(candidate, identity):
            raise RuntimeError("Copied Pages snapshot failed checksum verification")
        if destination.exists():
            destination.rename(old)
        try:
            candidate.rename(destination)
        except BaseException:
            if old.exists():
                old.rename(destination)
            raise


def new_shared_assets(previous: Path | None, current: Path) -> list[Path]:
    if previous is None or not previous.is_dir():
        raise RuntimeError("A verified previous Pages deployment is required")
    added = []
    for path in sorted((current / "_astro").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(current)
        old = previous / relative
        if not old.exists():
            added.append(relative)
        elif old.read_bytes() != path.read_bytes():
            raise RuntimeError(
                f"Hashed shared asset changed at the same URL: {relative}"
            )
    return added


def prepare_shared_assets(
    previous: Path,
    current: Path,
    destination: Path,
    added: list[Path],
    *,
    budget: int = 20_000,
) -> None:
    # All linked files are immutable for the lifetime of this candidate. Never
    # overwrite an existing target: that would mutate the rollback snapshot.
    old_budget = evaluate_pages_budget(previous, budget=budget)
    if old_budget.file_count + len(added) >= budget:
        raise RuntimeError(
            "Shared asset compatibility deployment exceeds the Pages file budget: "
            f"previous snapshot has {old_budget.file_count} files and "
            f"{len(added)} assets would be added (limit {budget}). "
            "Use the verified previous Pages deployment, not a full local build."
        )
    shutil.copytree(previous, destination, copy_function=link_immutable_or_copy)
    for relative in added:
        target = destination / relative
        if target.exists():
            raise RuntimeError(
                f"Compatibility asset would overwrite a prior file: {relative}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        link_immutable_or_copy(current / relative, target)
    if not evaluate_pages_budget(destination, budget=budget).ok:
        raise RuntimeError(
            "Shared asset compatibility deployment exceeds the Pages file budget"
        )


async def verify_shared_assets(
    current: Path, added: list[Path], *, main_origin: str, papers_origin: str
) -> None:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for relative in added:
            expected = hashlib.sha256((current / relative).read_bytes()).hexdigest()
            url = f"{main_origin.rstrip('/')}/{relative.as_posix()}"
            for attempt in range(5):
                try:
                    response = await client.get(url, headers={"Origin": papers_origin})
                    response.raise_for_status()
                    if hashlib.sha256(response.content).hexdigest() != expected:
                        raise RuntimeError(
                            f"Shared asset checksum mismatch: {relative}"
                        )
                    # `*` is valid here: report pages read the same public assets,
                    # so the header is no longer pinned to the papers origin alone.
                    allow_origin = response.headers.get("access-control-allow-origin")
                    if allow_origin not in {papers_origin, "*"}:
                        raise RuntimeError(f"Shared asset CORS mismatch: {relative}")
                    break
                except (httpx.HTTPError, RuntimeError) as exc:
                    if attempt == 4:
                        raise SharedAssetVerificationError(
                            f"Shared asset verification failed: {type(exc).__name__}: {exc}"
                        ) from exc
                    await asyncio.sleep(2 * (attempt + 1))


async def publish_shared_assets(
    service,
    task,
    runtime,
    *,
    previous: Path | None,
    project_name: str,
    deployment_branch: str,
    release_identity: dict,
    papers_origin: str,
) -> None:
    # Self-contained papers bundles upload their hashed dependencies before HTML.
    # Legacy bundles still require the verified main-site compatibility deploy.
    papers_manifest = runtime.root_dir / "exports" / "papers-site" / "papers-manifest.json"
    if papers_manifest.is_file():
        manifest = json.loads(papers_manifest.read_text(encoding="utf-8"))
        if manifest.get("local_build_assets") is True:
            for item in manifest.get("files", []):
                if str(item.get("path", "")).startswith("_astro/"):
                    path = papers_manifest.parent / item["path"]
                    if not path.resolve().is_relative_to(papers_manifest.parent.resolve()):
                        raise RuntimeError("Papers asset escapes bundle directory")
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
                        raise RuntimeError(f"Papers build asset checksum mismatch: {item['path']}")
            return
    current = runtime.astro_dir / "dist"
    from .papers_bundle import DEFAULT_MAIN_SITE_ORIGIN

    main_origin = str(service._config().main_site_origin or DEFAULT_MAIN_SITE_ORIGIN).rstrip("/")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(f"{main_origin}/release.json", headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        live_identity = response.json()
    retained = runtime.root_dir / "exports" / "verified-pages-snapshot"
    candidates = [retained, previous] if previous is not None else [retained]
    previous = None
    for candidate in candidates:
        if await asyncio.to_thread(verified_snapshot, candidate, live_identity):
            previous = candidate
            break
    if previous is None:
        raise RuntimeError(
            "No verified previous Pages snapshot matches the live release; "
            "restore the exact deployed artifact before a compatibility deployment."
        )
    added = await asyncio.to_thread(new_shared_assets, previous, current)
    if not added:
        return
    with tempfile.TemporaryDirectory(
        prefix="shared-assets-", dir=runtime.root_dir / "exports"
    ) as temporary:
        destination = Path(temporary) / "dist"
        await asyncio.to_thread(
            prepare_shared_assets,
            previous,
            current,
            destination,
            added,
            budget=int(service._config().pages_file_budget),
        )
        command = service._cloudflare_deploy_command(
            project_name=project_name,
            branch=deployment_branch,
            source_commit=release_identity["source_commit"],
            commit_message="Publish shared assets before R2 pages",
            commit_dirty=True,
        )
        command[command.index("deploy") + 1] = str(destination)
        await service._run_logged_command(
            task.task_uuid,
            title="Publish Shared Assets Compatibility",
            cmd=command,
            cwd=runtime.astro_dir,
            env={
                "CI": "1",
                "CLOUDFLARE_API_TOKEN": service._cloudflare_api_token(),
                "CLOUDFLARE_ACCOUNT_ID": service._cloudflare_account_id(),
            },
            metadata={
                "event": "shared_assets_compatibility_deploy",
                "added_assets": len(added),
            },
            timeout_seconds=runtime.cloudflare_deploy_timeout_seconds,
        )
        await verify_shared_assets(
            current,
            added,
            main_origin=main_origin,
            papers_origin=papers_origin,
        )
