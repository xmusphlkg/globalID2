#!/usr/bin/env python3
"""Publish a privately built research snapshot and its versioned page bundle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.publish_papers_r2 import _config_from_env  # noqa: E402
from src.generation.literature_publication import _atomic_write, freeze_literature_json, select_index_artifacts  # noqa: E402
from src.services.data_release.literature_artifacts import build_research_papers_manifest  # noqa: E402
from src.services.data_release.literature_publish import PREFIX, publish_literature_candidate  # noqa: E402
from src.services.data_release.papers_content import prepare_papers_content_from_manifest  # noqa: E402
from src.services.data_release.papers_projection import (  # noqa: E402
    assert_v3_candidate, load_projection_candidate, prepare_projection_candidate, verify_projected_publication,
)
from src.services.data_release.papers_reuse import load_committed_papers_receipt  # noqa: E402
from src.services.data_release.surveillance_publish import _starting_baseline, _target, validate_manifest  # noqa: E402


def _public_articles(snapshot: Path, slugs: list[str]) -> list[dict]:
    directory = snapshot / 'build' / 'research' / 'articles'
    articles = []
    for slug in slugs:
        path = directory / f'{slug}.json'
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing public article projection: {slug}')
        articles.append(json.loads(path.read_bytes()))
    return articles


def _load_v3_marker(run: Path, *, release_id: str, origin: str):
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
    if (candidate.release_id != release_id or candidate.origin != origin
            or candidate.generation != marker.get('generation')):
        raise ValueError('Prepared Papers candidate does not match its marker')
    assert_v3_candidate(candidate)
    return candidate


def _write_v3_marker(run: Path, candidate) -> None:
    _atomic_write(run / 'papers-prepared.json', json.dumps({
        'schema': 3, 'release_id': candidate.release_id, 'generation': candidate.generation,
        'candidate': 'projection-v3/candidate.json',
    }, sort_keys=True, separators=(',', ':')).encode())


def _project_stage(*, run: Path, stage: Path, snapshot: Path, manifest: dict, release_id: str, origin: str):
    import shutil
    template_dir = run / 'paper-templates'
    base = prepare_papers_content_from_manifest(stage, manifest, papers_origin=origin,
        release_id=release_id, template_dir=template_dir)
    slugs = sorted({entry['paper_template']['slug'] for entry in base.entries if 'paper_template' in entry})
    if not slugs:
        raise ValueError('Research build has no article pages to project')
    directory = run / 'projection-v3'
    if directory.is_symlink():
        raise ValueError('Invalid projection directory')
    if directory.exists():
        shutil.rmtree(directory)
    candidate, _receipt = prepare_projection_candidate(base, articles=_public_articles(snapshot, slugs),
        directory=directory, release_id=release_id, migration=True)
    assert_v3_candidate(candidate)
    _write_v3_marker(run, candidate)
    return candidate


def publish(*, run: Path, snapshot: Path, stage: Path, shared: Path, receipt: Path,
            release_id: str, config, workers: int = 16, client=None,
            projection_candidate: Path | None = None, projection_verifier=None,
            prepare_only: bool = False) -> dict:
    run, snapshot, stage, shared, receipt = map(Path, (run, snapshot, stage, shared, receipt))
    captured, previous = _starting_baseline(run, config, PREFIX)
    if not captured:
        raise ValueError('Research publication baseline was not captured before export')
    if os.environ.get('GIDS_PAPERS_PROJECTION_V3') != '1':
        raise ValueError('Papers v3 publication requires the explicit projection feature gate')
    projected = None
    direct_update = projection_candidate is not None
    if projection_candidate is not None:
        projected = load_projection_candidate(projection_candidate)
        if projected.release_id != release_id:
            raise ValueError('Projection candidate belongs to a different release')
        frozen = json.loads(Path(projection_candidate).read_bytes())
        baseline = validate_manifest(previous)[0] if previous else {}
        if frozen.get('base_generation') != baseline.get('papers', {}).get('papers_generation_sha256'):
            raise ValueError('Projection candidate base does not match the captured research publication')
        assert_v3_candidate(projected)
    else:
        projected = _load_v3_marker(run, release_id=release_id, origin=config.public_base_url)
    recovery_only = os.environ.get('GIDS_PUBLICATION_RECOVERY_ONLY') == '1'
    if recovery_only and projected is None:
        raise ValueError('Frozen literature candidate unavailable; regeneration is forbidden')
    build = None if projected is not None else json.loads(Path(f'{stage}.inputs.json').read_bytes())
    shared.mkdir(parents=True, exist_ok=True)
    with (shared / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (run / 'current.json').exists():
            source = snapshot / 'public/research'
            freeze_literature_json(source, shared, release_id=release_id,
                generated_at=datetime.now(timezone.utc).isoformat(), logical_names=select_index_artifacts(source))
            _atomic_write(run / 'current.json', (shared / 'current.json').read_bytes())
        current, _ = validate_manifest((run / 'current.json').read_bytes())
        if current['release_id'] != release_id:
            raise ValueError('Research candidate belongs to a different task')
        if direct_update:
            baseline_files = validate_manifest(previous)[0]['files'] if previous else {}
            identity = lambda files: {name: (item['sha256'], item['bytes']) for name, item in files.items()}
            if identity(current['files']) != identity(baseline_files):
                raise ValueError('Direct paper update requires unchanged directory/index projections; run the regular research build')
        elif projected is None:
            manifest_path = run / 'papers-build-manifest.json'
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_bytes())
            else:
                previous_manifest = validate_manifest(previous)[0] if previous else None
                old = (load_committed_papers_receipt(receipt, pointer=previous_manifest, target=_target(config, PREFIX))
                       if previous_manifest and previous_manifest.get('papers') else None)
                manifest = build_research_papers_manifest(stage, origin=config.public_base_url, build=build, previous=old)
                _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode())
            projected = _project_stage(run=run, stage=stage, snapshot=snapshot, manifest=manifest,
                release_id=release_id, origin=config.public_base_url)
        if prepare_only:
            return {'prepared': True, 'release_id': release_id, 'generation': projected.generation}
        result = publish_literature_candidate(run=run, objects_dir=shared / 'objects', papers=projected,
            config=config, receipt=receipt, client=client, workers=workers)
        verification = (projection_verifier or verify_projected_publication)(projected)
        result['public_verification'] = verification
        _atomic_write(run / 'published.json', json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--workers', type=int, default=96)
    parser.add_argument('--prepare-only', action='store_true', help='Freeze the complete candidate without uploading')
    parser.add_argument('--projection-candidate', type=Path, help='Frozen v3 candidate; requires GIDS_PAPERS_PROJECTION_V3=1')
    args = parser.parse_args()
    result = publish(run=args.run, snapshot=args.snapshot, stage=args.stage, release_id=args.release_id,
        shared=ROOT / 'exports/research-json', receipt=ROOT / 'data/cache/isolated-research-publication.json',
        config=_config_from_env(), workers=args.workers, projection_candidate=args.projection_candidate,
        prepare_only=args.prepare_only)
    print(json.dumps({'event': 'research_publication_complete', **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
