"""Compile template changes against published data without touching producers."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import re
import subprocess
import tarfile
from tempfile import TemporaryDirectory, TemporaryFile

from .published_data import clone_file, files, verify_copy
from .stage_receipts import StageReceipts
from .artifact_checkpoint import source_fingerprint, rendering_configuration, _fingerprint


def _workspace_inputs(root: Path, revision: str | None = None) -> str:
    # Version history is an independently published JSON feed. Editing it must
    # not invalidate an already prepared template workspace.
    return _fingerprint({'source': revision or source_fingerprint(root), 'rendering': rendering_configuration(root)})


def prepare_committed(root: Path, task_uuid: str, *, revision: str, recovery=False, independent_research=False) -> Path:
    """Copy authored renderer inputs from the commit selected by the panel."""
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Main site template requires a frozen Git commit")
    with TemporaryDirectory(prefix="globalid-template-") as directory:
        if not recovery:
            with TemporaryFile() as archive:
                subprocess.run(["git", "archive", "--format=tar", revision, "--",
                    "astro-site", "scripts", "shared", "configs", "cloudflare/surveillance-data",
                    "CHANGELOG.md", "requirements.txt"], cwd=root, stdout=archive, check=True)
                archive.seek(0)
                with tarfile.open(fileobj=archive) as snapshot:
                    snapshot.extractall(directory, filter="data")
        return prepare(root, task_uuid, recovery=recovery, independent_research=independent_research,
                       source_root=Path(directory), revision=revision)


def prepare(root: Path, task_uuid: str, *, recovery: bool = False, independent_research: bool = False,
            source_root: Path | None = None, revision: str | None = None) -> Path:
    if Path(task_uuid).name != task_uuid or task_uuid in {"", ".", ".."}:
        raise ValueError("Invalid template task identity")
    work = root / "exports/code-workspaces" / task_uuid
    site = work / "astro-site"
    if work.is_symlink() or site.is_symlink():
        raise ValueError("Unsafe template workspace")
    if recovery:
        # Recovery must retain the exact original paths/stat identities. The
        # ready stage validates bytes; rebuilding the workspace would lose it.
        if not (work / "workspace.json").is_file():
            raise ValueError("Frozen template workspace unavailable")
        return site
    receipts = StageReceipts(root, task_uuid, {}, {})
    if receipts.records.get("ready") and (work / "workspace.json").is_file():
        saved = json.loads((work / "workspace.json").read_bytes())
        marker_body = (root / "exports/successful-site-release.json").read_bytes()
        marker = json.loads(marker_body)
        ready = receipts.records['ready'].get('identity', {})
        same_baseline = saved.get('baseline_sha256') == hashlib.sha256(marker_body).hexdigest()
        own_publication = (marker.get('task_uuid') == task_uuid
            and marker.get('release_id') == ready.get('release_id')
            and marker.get('build_digest') == ready.get('build_digest'))
        if ((same_baseline or own_publication) and saved.get("source_fingerprint") == _workspace_inputs(root, revision)
                and saved.get("independent_research", False) == independent_research):
            return site
    fingerprint = _workspace_inputs(root, revision)
    authored = source_root or root
    marker_body = (root / "exports/successful-site-release.json").read_bytes() if (root / "exports/successful-site-release.json").is_file() else b''
    original_paths = [root / "astro-site/src/data", root / "astro-site/public/site-data"]
    sources = StageReceipts(root, task_uuid, {}, {}).published_data_paths(
        original_paths)
    if work.is_symlink():
        raise ValueError("Unsafe template workspace")
    if work.exists():
        shutil.rmtree(work)
    site.mkdir(parents=True)

    def copy_tree(source, target, *, exclude=()):
        if not source.exists():
            return
        for relative, path in files(source).items():
            if not any(relative == name or relative.startswith(name + "/") for name in exclude):
                clone_file(path, target / relative)

    # Copy source, not a checkout or exports tree. Imports and validators with
    # relative paths then all resolve to the private project root.
    original = authored / "astro-site"
    for entry in original.iterdir():
        if entry.is_file() and not entry.is_symlink():
            clone_file(entry, site / entry.name)
    copy_tree(original / "src", site / "src", exclude=("data",))
    copy_tree(original / "scripts", site / "scripts")
    copy_tree(original / "public", site / "public", exclude=("site-data",))
    for source, target in zip(sources, (site / "src/data", site / "public/site-data")):
        target.mkdir(parents=True, exist_ok=True)
        copy_tree(source, target)
    verify_copy(root, sources, [site / "src/data", site / "public/site-data"])
    if StageReceipts(root, task_uuid, {}, {}).published_data_paths(original_paths) != sources:
        raise ValueError("Published baseline changed while preparing template workspace")
    for name in ("changelog.ts", "acknowledgements.ts"):
        path = original / "src/data" / name
        if path.is_file():
            clone_file(path, site / "src/data" / name)
    for name in ("shared", "configs", "scripts"):
        copy_tree(authored / name, work / name, exclude=("__pycache__",))
    worker = "cloudflare/surveillance-data"
    for name in ("src", "scripts"):
        copy_tree(authored / worker / name, work / worker / name)
    for name in ("CHANGELOG.md", "requirements.txt", "data/system-settings.json"):
        source = (root if name == "data/system-settings.json" else authored) / name
        if source.is_file():
            clone_file(source, work / name)
    # Only validated renderer caches are imported, as independent bytes. The
    # renderer still checks source fingerprints, page digests and references;
    # incomplete/changed cache entries are ordinary misses, never authority.
    published = json.loads(marker_body)
    previous_task = str(published.get('task_uuid') or '')
    previous_work = root / 'exports/code-workspaces' / previous_task
    cache_source = (previous_work if previous_task not in {'', '.', '..'} and Path(previous_task).name == previous_task
                    and (previous_work / 'workspace.json').is_file() else root) / 'data/cache'
    # build_stage derives GIDS_RESEARCH_PUBLICATION_ENABLED from the same
    # configuration flag. In that mode build-site/finalize skip article HTML
    # caches entirely; importing them would copy gigabytes of unused old pages.
    cache_names = ['site-page-manifest.json', 'site-pages', 'astro-assets']
    if not independent_research:
        cache_names += ['research-build-manifest.json', 'research-pages']
    for name in cache_names:
        source, target = cache_source / name, work / 'data/cache' / name
        if source.is_file():
            clone_file(source, target)
        elif source.is_dir():
            copy_tree(source, target)
    # Dependencies are used only for compilation; no install runs here.
    astro_modules = root / "astro-site/node_modules"
    worker_modules = root / worker / "node_modules"
    if not (worker_modules / "esbuild/package.json").is_file():
        # Minimal release checkouts may not have installed the Pages Worker's
        # own dependencies. Astro already installs a compatible esbuild copy.
        worker_modules = astro_modules
    for source, target in ((astro_modules, site / "node_modules"),
                           (worker_modules, work / worker / "node_modules")):
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source.resolve(), target_is_directory=True)
    if _workspace_inputs(root, revision) != fingerprint:
        raise ValueError("Source changed while preparing template workspace")
    if (root / "exports/successful-site-release.json").read_bytes() != marker_body:
        raise ValueError("Published baseline changed while preparing template workspace")
    (work / "workspace.json").write_text(json.dumps({"schema": 1, "task_uuid": task_uuid,
        "source_fingerprint": fingerprint, "baseline_sha256": hashlib.sha256(marker_body).hexdigest(),
        "independent_research": independent_research}))
    return site
