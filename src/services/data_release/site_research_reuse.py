"""Reuse a committed research generation when its data and renderer are unchanged.

The source receipt is only an optimization. It must match a freshly captured
remote pointer, its destination, and the complete public research projection.
Task-local decisions are frozen so upload-only retries never change branches.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re

from src.generation.published_research_reuse import require_public_research_matches
from .artifact_index import ArtifactIndex
from .literature_publish import PREFIX
from .papers_reuse import load_committed_papers_receipt
from .surveillance_publish import _atomic_receipt, _starting_baseline, _target, validate_manifest

INPUTS = "research-render-inputs.json"
REUSED = "research-reused.json"
COMMITTED = "data/cache/research-publication-inputs.json"
HASH = re.compile(r"[a-f0-9]{64}\Z")


def project_root(run: Path) -> Path | None:
    return next((parent.parent for parent in Path(run).parents if parent.name == "exports"), None)


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
        raise ValueError("Invalid research reuse receipt")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Invalid research reuse receipt")
    return value


def render_inputs(root: Path, *, astro_dir: Path | None = None, settings=None) -> dict | None:
    """Hash renderer/projection sources, shell inputs and relevant configuration.

    Generated research data is checked separately against the captured pointer.
    Conservatively cover all frontend sources, including dynamic imports; no
    unrelated source change can hide a research dependency from this contract.
    """
    root = Path(root)
    astro = Path(astro_dir) if astro_dir is not None else root / "astro-site"
    if not (astro / "src/pages/research").is_dir() or not (root / "src/generation/site_data_literature.py").is_file():
        return None
    if settings is None:
        from src.core.config import get_config
        settings = get_config().data_release
    index = ArtifactIndex(root, cache_dir=root / "data/cache/research-reuse-index", resolve_keys=False)
    roots = [("site/" + name, astro / name) for name in (
        "src", "public", "scripts", "package.json", "package-lock.json", "astro.config.mjs",
        "tailwind.config.mjs", "postcss.config.mjs", "tsconfig.json")]
    roots += [(name, root / name) for name in (
        "shared", "src/generation", "src/literature", "src/services/data_release",
        "cloudflare/surveillance-data/src", "configs/literature",
        "configs/disease_ontology.json", "configs/standard_diseases.csv")]
    digest = hashlib.sha256(b"research-publication-inputs-v1\n")
    shell = {"meta.json", "diseases/index.json", "reports/index.json"}
    for label, base in roots:
        digest.update(label.encode() + b"\0" + (b"present" if base.exists() else b"missing"))
        paths = sorted(base.rglob("*")) if base.is_dir() else [base] if base.is_file() else []
        for path in paths:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            # Background AI reviews are data inputs already covered by the
            # frozen public projection, not renderer or projection code.
            if path == root / "configs/literature/weekly_ai_reviews.json":
                continue
            if path.is_relative_to(astro / "src/data"):
                relative = path.relative_to(astro / "src/data").as_posix()
                if path.suffix == ".json" and relative not in shell:
                    continue
            if path.is_relative_to(astro / "public/site-data"):
                continue
            if path.is_symlink():
                raise ValueError("Symlink in research publication inputs")
            if path.is_file():
                digest.update((label + "/" + path.relative_to(base).as_posix()).encode() + b"\0")
                digest.update(bytes.fromhex(index.describe(path)["sha256"]))
    configuration = {key: getattr(settings, key, None) for key in (
        "literature_json_enabled", "surveillance_json_enabled", "situation_json_enabled",
        "pages_data_worker_enabled", "theme_stylesheet_url", "main_site_origin", "r2_papers_public_base_url")}
    environment = {key: os.environ.get(key) for key in (
        "GLOBALID_SITE_URL", "SITE_URL", "PUBLIC_GIDS_MAINTENANCE_MESSAGE", "SITUATION_SHADOW_PREVIEW",
        "PUBLIC_GIDS_DATA_MANIFEST_URL", "PUBLIC_GIDS_SITUATION_MANIFEST_URL", "PUBLIC_GA4_MEASUREMENT_ID",
        "SUBSCRIPTIONS__PUBLIC_BASE_URL", "GIDS_RESEARCH_STATIC_HOST")}
    # GA configuration and the footer year are runtime template dependencies.
    settings_path = root / "data/system-settings.json"
    if "PUBLIC_GA4_MEASUREMENT_ID" not in os.environ:
        try:
            environment["analytics_setting"] = _read(settings_path).get("site", {}).get("public_ga4_measurement_id")
        except (OSError, ValueError, AttributeError):
            environment["analytics_setting"] = None
    index.save()
    return {"schema": 1, "sources_sha256": digest.hexdigest(), "configuration": configuration,
            "environment": environment, "year": datetime.now(timezone.utc).year}


def capture_render_inputs(root: Path, run: Path, *, astro_dir: Path | None = None, settings=None) -> dict | None:
    value = render_inputs(root, astro_dir=astro_dir, settings=settings)
    if value is None:
        return None
    path = Path(run) / INPUTS
    if path.exists():
        if _read(path) != value:
            raise ValueError("Research renderer inputs changed after preparation")
    else:
        _atomic_receipt(path, value)
    return value


def _identity(pointer: dict, config) -> dict:
    papers = pointer.get("papers") or {}
    generation = papers.get("papers_generation_sha256")
    manifest_sha = papers.get("papers_manifest_sha256")
    origin = config.public_base_url.rstrip("/")
    if (not isinstance(generation, str) or not HASH.fullmatch(generation)
            or not isinstance(manifest_sha, str) or not HASH.fullmatch(manifest_sha)
            or papers.get("papers_origin") != origin
            or papers.get("papers_manifest_url") != f"{origin}/releases/sha256/catalogues/{generation}.json"):
        raise ValueError("Research baseline has no valid Papers binding")
    return {"target": _target(config, PREFIX), "release_id": pointer["release_id"],
            "generation": generation, "manifest_sha256": manifest_sha, "origin": origin}


def record_committed_inputs(root: Path, run: Path, config) -> bool:
    """Bind frozen build inputs only after the normal publisher has committed."""
    path = Path(run) / INPUTS
    if not path.is_file():
        return False
    inputs = _read(path)
    pointer, _ = validate_manifest((Path(run) / "current.json").read_bytes())
    identity = _identity(pointer, config)
    if load_committed_papers_receipt(root / "data/cache/isolated-research-publication.json",
            pointer=pointer, target=identity["target"]) is None:
        return False
    published = _read(Path(run) / "published.json")
    if (published.get("release_id") != identity["release_id"]
            or published.get("public_verification", {}).get("status") != "verified"):
        return False
    _atomic_receipt(root / COMMITTED, {"schema": 1, "identity": identity, "inputs": inputs})
    return True


def reusable_generation(root: Path, run: Path, config, *, research_source: Path) -> dict | None:
    """Return a candidate for reuse; missing/incompatible evidence falls back."""
    try:
        captured, body = _starting_baseline(run, config, PREFIX)
        if not captured or body is None:
            return None
        pointer, _ = validate_manifest(body)
        identity = _identity(pointer, config)
        saved = _read(root / COMMITTED)
        if (saved.get("schema") != 1 or saved.get("identity") != identity
                or saved.get("inputs") != _read(run / INPUTS)):
            return None
        if load_committed_papers_receipt(root / "data/cache/isolated-research-publication.json",
                pointer=pointer, target=identity["target"]) is None:
            return None
        if pointer["release_id"].startswith("literature-"):
            receipt = _read(root / "exports/research-current-receipt.json")
            if (receipt.get("schema") != 1 or receipt.get("release_id") != pointer["release_id"]
                    or receipt.get("release_id") != f"literature-{receipt.get('task_uuid')}"
                    or receipt.get("papers_generation_sha256") != identity["generation"]):
                return None
        require_public_research_matches(research_source, pointer)
        return {"schema": 1, "identity": identity, "baseline_sha256": hashlib.sha256(body).hexdigest(),
                "inputs": saved["inputs"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def retained_generation(run: Path, config, research_source: Path) -> dict:
    """Keep the published Papers generation for an explicit Pages-only build."""
    captured, body = _starting_baseline(run, config, PREFIX)
    if not captured or body is None:
        raise ValueError("Pages-only release requires a published research baseline")
    pointer, _ = validate_manifest(body)
    require_public_research_matches(research_source, pointer)
    return {"schema": 1, "identity": _identity(pointer, config),
            "baseline_sha256": hashlib.sha256(body).hexdigest()}


def freeze_reuse(run: Path, value: dict) -> None:
    _atomic_receipt(Path(run) / REUSED, value)


def load_reuse(run: Path, config) -> dict | None:
    path = Path(run) / REUSED
    if not path.exists():
        return None
    value = _read(path)
    captured, body = _starting_baseline(run, config, PREFIX)
    if not captured or body is None:
        raise ValueError("Frozen research reuse has no captured baseline")
    pointer, _ = validate_manifest(body)
    if (value.get("schema") != 1 or value.get("identity") != _identity(pointer, config)
            or value.get("baseline_sha256") != hashlib.sha256(body).hexdigest()):
        raise ValueError("Frozen research reuse does not match its baseline")
    return value


def verify_reused_baseline(run: Path, config, *, client=None) -> None:
    """Read the remote authority again; a local receipt never proves it is current."""
    if load_reuse(run, config) is None:
        raise ValueError("No frozen research reuse receipt")
    _, previous = _starting_baseline(run, config, PREFIX)
    from .papers_r2 import R2S3Client, R2S3Error
    active = client or R2S3Client(config)
    try:
        if active.get_object(f"{PREFIX}/current.json") != previous:
            raise R2S3Error("Research publication baseline changed before reuse", status=412)
    finally:
        if client is None:
            active.close()


async def selected_literature_changes(input_data: dict, get_database) -> bool:
    """Check exact selected IDs; unselected independent changes stay pending."""
    if input_data.get("code_only"):
        return False
    ids = input_data.get("publish_dirty_ids")
    if not isinstance(ids, list) or any(type(value) is not int or value <= 0 for value in ids):
        return True
    from .global_selection import read_selected_batch, selection_batches
    if not ids:
        return False
    async with get_database() as db:
        for batch in selection_batches(ids):
            rows = await read_selected_batch(db, batch)
            if {row.id for row in rows} != set(batch) or any(row.line == "literature" for row in rows):
                return True
    return False
