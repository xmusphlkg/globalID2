"""Publish full-site research through the same atomic pointer as literature jobs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from src.generation.literature_publication import _atomic_write, freeze_literature_json, select_index_artifacts
from .literature_publish import PREFIX, publish_literature_candidate
from .papers_bundle import build_papers_manifest, is_obsolete_research_build_path
from .papers_content import prepare_papers_content_from_manifest, verified_existing_objects
from .papers_projection import (
    assert_v3_candidate, load_projection_candidate, prepare_projection_candidate,
    verify_projected_publication,
)
from .papers_r2 import R2S3Client
from .surveillance_publish import _starting_baseline


def freeze_site_research(*, papers_dir: Path, site_dist: Path, research_source: Path,
                         run: Path, release_id: str, papers_origin: str) -> dict:
    """Snapshot the split research bundle and its main-site entry pages locally.

    The ready receipt is written last. Interrupted local preparation is rebuilt;
    a published candidate is never silently regenerated on retry.
    """
    run = Path(run)
    ready = run / 'site-ready.json'
    if ready.exists():
        result = json.loads(ready.read_bytes())
        if result.get('release_id') != release_id:
            raise ValueError('Site research candidate belongs to another release')
        return result
    if os.environ.get('GIDS_PUBLICATION_RECOVERY_ONLY') == '1':
        raise ValueError('Frozen site research candidate unavailable; regeneration is forbidden')
    if (run / 'published.json').exists():
        raise ValueError('Published site research candidate has no ready receipt')
    if not papers_dir.is_dir() or not site_dist.is_dir():
        raise ValueError('Missing split Papers or main site build')
    names = select_index_artifacts(research_source)
    run.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.site-research-', dir=run))
    try:
        bundle = work / 'bundle'
        bundle.mkdir()
        def copy(source: Path, destination: Path):
            if source.is_symlink():
                raise ValueError('Symlink in frozen research bundle')
            if source.is_dir():
                for child in source.iterdir():
                    copy(child, destination / child.name)
            elif source.is_file():
                relative = destination.relative_to(bundle).as_posix()
                if relative == 'papers-manifest.json' or is_obsolete_research_build_path(relative):
                    return
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        copy(papers_dir, bundle)
        for relative in ('research', 'zh/research', 'fr/research', '_astro', 'scripts', 'site-data/research-graph.json'):
            source = site_dist / relative
            if source.exists():
                copy(source, bundle / relative)
        # The JSON publication owns these projections; retired monolithic
        # paths must not return to the public Papers content catalogue.
        frozen_source = work / 'research-json-source'
        for name in names:
            source = research_source / name
            if source.is_symlink() or not source.resolve().is_relative_to(research_source.resolve()):
                raise ValueError('Unsafe research JSON input')
            destination = frozen_source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for prefix in ('', 'zh', 'fr'):
            if not (bundle / prefix / 'research/index.html').is_file():
                raise ValueError('Research generation requires every locale landing page')
        manifest = build_papers_manifest(bundle, papers_origin=papers_origin)
        frozen_json = work / 'json'
        freeze_literature_json(frozen_source, frozen_json, release_id=release_id,
                               generated_at=datetime.now(timezone.utc).isoformat(), logical_names=names)
        _atomic_write(work / 'papers-build-manifest.json', json.dumps(manifest, separators=(',', ':')).encode())
        # No public changes happen here; leftovers only belong to this run.
        for name in ('bundle', 'json'):
            target = run / name
            if target.is_symlink():
                raise ValueError('Unsafe site research output')
            if target.exists():
                shutil.rmtree(target)
            os.replace(work / name, target)
        os.replace(work / 'papers-build-manifest.json', run / 'papers-build-manifest.json')
        _atomic_write(run / 'current.json', (run / 'json/current.json').read_bytes())
        result = {'schema': 1, 'release_id': release_id, 'file_count': len(manifest['files'])}
        _atomic_write(ready, json.dumps(result, separators=(',', ':')).encode())
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _article_documents(articles_dir: Path, slugs: list[str]) -> list[dict]:
    documents = []
    for slug in slugs:
        path = Path(articles_dir) / f'{slug}.json'
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing public article projection: {slug}')
        documents.append(json.loads(path.read_bytes()))
    return documents


def _load_site_v3(run: Path, *, release_id: str, origin: str):
    path = run / 'papers-prepared.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('Invalid prepared Papers candidate')
    marker = json.loads(path.read_bytes())
    if marker.get('schema') != 3:
        raise ValueError('Legacy v2 Papers candidates cannot be published')
    if marker.get('release_id') != release_id or marker.get('candidate') != 'projection-v3/candidate.json':
        raise ValueError('Prepared Papers candidate belongs to a different release')
    candidate = load_projection_candidate(run / marker['candidate'])
    if candidate.release_id != release_id or candidate.origin != origin or candidate.generation != marker.get('generation'):
        raise ValueError('Prepared Papers candidate does not match its marker')
    assert_v3_candidate(candidate)
    return candidate


def publish_site_research(*, papers_dir: Path, site_dist: Path, research_source: Path,
                          run: Path, release_id: str, config, receipt: Path,
                          articles_dir: Path | None = None,
                          template_dir: Path | None = None, inventory: dict | None = None,
                          workers: int = 32, client=None, prepare_only: bool = False,
                          preserve_research_data: bool = False, projection_verifier=None) -> dict:
    captured, previous = _starting_baseline(run, config, PREFIX)
    if not captured:
        raise ValueError('Capture the research baseline before building the site')
    freeze_site_research(papers_dir=papers_dir, site_dist=site_dist, research_source=research_source,
                         run=run, release_id=release_id, papers_origin=config.public_base_url)
    if preserve_research_data:
        prior = json.loads(previous) if previous is not None else {}
        frozen = json.loads((run / 'current.json').read_bytes())
        if ('index.json' not in prior.get('files', {}) or frozen['files'] != prior['files']):
            raise ValueError('Style release research data differs from the captured published snapshot')
    candidate = _load_site_v3(run, release_id=release_id, origin=config.public_base_url)
    if candidate is None:
        if os.environ.get('GIDS_PUBLICATION_RECOVERY_ONLY') == '1':
            raise ValueError('Frozen site research candidate unavailable; regeneration is forbidden')
        if articles_dir is None:
            raise ValueError('Site research publication requires public article projections')
        manifest = json.loads((run / 'papers-build-manifest.json').read_bytes())
        base = prepare_papers_content_from_manifest(run / 'bundle', manifest,
            papers_origin=config.public_base_url, release_id=release_id,
            template_dir=template_dir or run / 'paper-templates')
        slugs = sorted({entry['paper_template']['slug'] for entry in base.entries if 'paper_template' in entry})
        directory = run / 'projection-v3'
        if directory.is_symlink():
            raise ValueError('Invalid projection directory')
        if directory.exists():
            shutil.rmtree(directory)
        if slugs:
            candidate, _receipt = prepare_projection_candidate(base, articles=_article_documents(articles_dir, slugs),
                directory=directory, release_id=release_id, migration=True)
        else:
            from .site_research_baseline import compose_site_candidate
            prior = json.loads(previous) if previous else {}
            if prior and json.loads((run / 'current.json').read_bytes())['files'] != prior.get('files'):
                raise ValueError('Static research projections differ from the captured Papers v3 baseline')
            active = client or R2S3Client(config)
            try:
                candidate = compose_site_candidate(run=run, static=base,
                    previous=prior, client=active,
                    release_id=release_id, directory=directory, workers=workers)
            finally:
                if client is None:
                    active.close()
        assert_v3_candidate(candidate)
        _atomic_write(run / 'papers-prepared.json', json.dumps({
            'schema': 3, 'release_id': release_id, 'generation': candidate.generation,
            'candidate': 'projection-v3/candidate.json',
        }, sort_keys=True, separators=(',', ':')).encode())
    if prepare_only:
        return {'release_id': release_id, 'generation': candidate.generation,
                'article_pages': sum('paper_template' in item for item in candidate.entries),
                'file_count': len(candidate.entries)}
    known = verified_existing_objects(candidate, inventory, workers=min(workers, 8)) if inventory is not None else None
    active = client or R2S3Client(config)
    try:
        result = publish_literature_candidate(run=run, objects_dir=run / 'json/objects', papers=candidate,
            config=config, receipt=receipt, client=active, workers=workers, known_staged_objects=known, build=None)
        # A committed pointer alone does not prove that the Worker can render
        # this v3 generation. Keep the frozen candidate recoverable on failure;
        # only record completion after canonical canaries pass in every locale.
        result['public_verification'] = (projection_verifier or verify_projected_publication)(candidate)
        _atomic_write(run / 'published.json', json.dumps(result, separators=(',', ':')).encode())
        return result
    finally:
        if client is None:
            active.close()
