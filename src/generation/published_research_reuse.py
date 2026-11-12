"""Reuse one already-published research snapshot in a static site build."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
from uuid import UUID

from src.generation.literature_publication import select_index_artifacts


class PublishedResearchMismatch(ValueError):
    """A valid local projection belongs to a different published data snapshot."""


def require_public_research_matches(source: Path, baseline: dict) -> None:
    """Require the exact already-published browser projection, without network or DB reads."""
    if (not isinstance(baseline, dict) or baseline.get('schema_version') != 1
            or not isinstance(baseline.get('release_id'), str)
            or not isinstance(baseline.get('files'), dict)
            or not {'index.json', 'catalogue.json'} <= baseline['files'].keys()):
        raise ValueError('Published research baseline has no complete index; explicit migration is required')
    source = Path(source)
    names = select_index_artifacts(source)
    if set(names) != set(baseline['files']):
        raise PublishedResearchMismatch('Research index file selection differs from the published baseline')
    for name in names:
        path = source / name
        if (not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(source.resolve())
                or any(parent.is_symlink() for parent in path.parents if parent != source.parent and parent.is_relative_to(source))):
            raise ValueError('Unsafe published research input')
        raw = path.read_bytes()
        entry = baseline['files'][name]
        if (not isinstance(entry, dict) or entry.get('bytes') != len(raw)
                or entry.get('sha256') != hashlib.sha256(raw).hexdigest()
                or entry.get('url') != f"objects/{entry.get('sha256')}.json"):
            raise PublishedResearchMismatch(f'Research input differs from the published baseline: {name}')


def _validate_build_source(source: Path) -> None:
    for name in ('routes.json', 'index.json'):
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Published research snapshot is missing {name}')


def _validate_private_articles(source: Path, expected: dict) -> None:
    _validate_build_source(source)
    routes = json.loads((source / 'routes.json').read_bytes())
    if not isinstance(routes, dict) or not isinstance(routes.get('articles'), list):
        raise ValueError('Invalid published research routes')
    slugs = [route.get('slug') if isinstance(route, dict) else None for route in routes['articles']]
    if (any(not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug) for slug in slugs)
            or len(set(slugs)) != len(slugs) or set(slugs) != set(expected)):
        raise ValueError('Published private research routes differ from the frozen article receipt')
    for slug in slugs:
        path = source / 'articles' / f'{slug}.json'
        if (not isinstance(expected[slug], str) or not re.fullmatch(r'[a-f0-9]{64}', expected[slug])
                or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(source.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected[slug]):
            raise ValueError('Published private article differs from its frozen receipt')


def prepare_research_for_baseline(root: Path, output: Path, public: Path, baseline: dict) -> str:
    """Install the private snapshot matching a captured pointer, or verify local data.

    A recent independent literature publication can be newer than the last
    full-site export. Never treat a style-only build as authorization to publish
    that older export over the current literature generation.
    """
    receipt = root / 'exports/research-current-receipt.json'
    if receipt.exists():
        try:
            return install_published_research(root, output, public, expected_manifest=baseline)
        except (FileNotFoundError, PublishedResearchMismatch) as error:
            # A receipt can legitimately point at an older literature snapshot
            # after a full export. The local tree is acceptable only when its
            # entire public projection still matches the captured pointer.
            if str(baseline.get('release_id', '')).startswith('literature-'):
                raise ValueError('Cannot install the currently published literature snapshot') from error
    elif str(baseline.get('release_id', '')).startswith('literature-'):
        raise ValueError('Currently published literature has no acknowledged private snapshot receipt')
    _validate_build_source(output / 'research')
    require_public_research_matches(public / 'research', baseline)
    return str(baseline['release_id'])


def install_published_research(root: Path, output: Path, public: Path, *, expected_manifest: dict | None = None) -> str:
    """Install a coherent published snapshot without re-querying the database.

    Both trees are prepared before replacing either. Roll back the first swap if
    the second fails; an interrupted run never acknowledges a publication.
    """
    receipt = json.loads((root / "exports/research-current-receipt.json").read_bytes())
    task_id = str(UUID(receipt["task_uuid"]))
    release_id = receipt["release_id"]
    if release_id != f"literature-{task_id}":
        raise ValueError("Research receipt does not identify its snapshot")
    if expected_manifest is not None and str(expected_manifest.get('release_id', '')).startswith('literature-'):
        generation = (expected_manifest.get('papers') or {}).get('papers_generation_sha256')
        if (not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{64}', generation)
                or expected_manifest['release_id'] != release_id
                or receipt.get('papers_generation_sha256') != (expected_manifest.get('papers') or {}).get('papers_generation_sha256')):
            raise ValueError('Research receipt does not match the captured publication generation')
    snapshot = root / "exports/research-runs" / task_id / "snapshot"
    sources = [snapshot / "build/research", snapshot / "public/research"]
    for path in (sources[0] / "routes.json", sources[0] / "index.json", sources[1] / "index.json"):
        if not path.is_file():
            raise ValueError(f"Published research snapshot is missing {path.name}")
    destinations = [output / "research", public / "research"]
    if any(path.is_symlink() for path in sources + destinations):
        raise ValueError("Research snapshot directories must not be symlinks")
    if expected_manifest is not None:
        require_public_research_matches(sources[1], expected_manifest)
        article_receipt = json.loads((snapshot / 'article-inputs.json').read_bytes())
        if (not isinstance(article_receipt, dict) or article_receipt.get('schema') != 1
                or not isinstance(article_receipt.get('articles'), dict)):
            raise ValueError('Published private research requires its frozen article receipt')
        _validate_private_articles(sources[0], article_receipt['articles'])

    lock_path = root / "data/cache/research-public-artifacts.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        staged: list[Path] = []
        backups: list[tuple[Path, Path | None]] = []
        try:
            for source, dest in zip(sources, destinations):
                dest.parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".research-stage-", dir=dest.parent))
                staged.append(stage)
                # GNU cp uses copy-on-write when the filesystem supports it.
                import subprocess
                subprocess.run(["cp", "-a", "--reflink=auto", str(source) + "/.", str(stage)], check=True)
            if expected_manifest is not None:
                _validate_private_articles(staged[0], article_receipt['articles'])
                require_public_research_matches(staged[1], expected_manifest)
            for stage, dest in zip(staged, destinations):
                backup = None
                if dest.exists():
                    backup = Path(tempfile.mkdtemp(prefix=".research-backup-", dir=dest.parent))
                    backup.rmdir()
                    os.replace(dest, backup)
                backups.append((dest, backup))
                os.replace(stage, dest)
        except BaseException:
            for dest, backup in reversed(backups):
                if dest.exists():
                    shutil.rmtree(dest)
                if backup is not None:
                    os.replace(backup, dest)
            raise
        finally:
            for path in staged:
                if path.exists():
                    shutil.rmtree(path)
        for _, backup in backups:
            if backup is not None:
                shutil.rmtree(backup)
    print(f"  Published research snapshot reused: {release_id}", flush=True)
    return release_id
