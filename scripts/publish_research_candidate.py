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
from src.services.data_release.papers_reuse import load_committed_papers_receipt  # noqa: E402
from src.services.data_release.surveillance_publish import _starting_baseline, _target, validate_manifest  # noqa: E402


def publish(*, run: Path, snapshot: Path, stage: Path, shared: Path, receipt: Path,
            release_id: str, config, workers: int = 16, client=None,
            projection_candidate: Path | None = None, projection_verifier=None,
            prepare_only: bool = False) -> dict:
    run, snapshot, stage, shared, receipt = map(Path, (run, snapshot, stage, shared, receipt))
    captured, previous = _starting_baseline(run, config, PREFIX)
    if not captured:
        raise ValueError('Research publication baseline was not captured before export')
    projected = None
    if projection_candidate is not None:
        if os.environ.get('GIDS_PAPERS_PROJECTION_V3') != '1':
            raise ValueError('Papers v3 publication requires the explicit projection feature gate')
        from src.services.data_release.papers_projection import load_projection_candidate
        projected = load_projection_candidate(projection_candidate)
        if projected.release_id != release_id:
            raise ValueError('Projection candidate belongs to a different release')
        frozen = json.loads(Path(projection_candidate).read_bytes())
        baseline = validate_manifest(previous)[0] if previous else {}
        if frozen.get('base_generation') != baseline.get('papers', {}).get('papers_generation_sha256'):
            raise ValueError('Projection candidate base does not match the captured research publication')
    from src.services.data_release.literature_recovery import freeze_papers, load_papers
    prepared = (run / 'papers-prepared.json').exists()
    recovery_only = os.environ.get('GIDS_PUBLICATION_RECOVERY_ONLY') == '1'
    if recovery_only and not prepared and projected is None:
        raise ValueError('Frozen literature candidate unavailable; regeneration is forbidden')
    build = None if projected is not None or prepared else json.loads(Path(f'{stage}.inputs.json').read_bytes())
    shared.mkdir(parents=True, exist_ok=True)
    with (shared / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if prepared and projected is None:
            papers, build = load_papers(run, stage=stage, origin=config.public_base_url, release_id=release_id)
            if prepare_only:
                return {'prepared': True, 'release_id': release_id, 'generation': papers.generation}
            result = publish_literature_candidate(run=run, objects_dir=shared / 'objects', papers=papers,
                config=config, receipt=receipt, client=client, workers=workers, build=build)
            _atomic_write(run / 'published.json', json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
            return result
        if not (run / 'current.json').exists():
            source = snapshot / 'public/research'
            freeze_literature_json(source, shared, release_id=release_id,
                generated_at=datetime.now(timezone.utc).isoformat(), logical_names=select_index_artifacts(source))
            _atomic_write(run / 'current.json', (shared / 'current.json').read_bytes())
        current, _ = validate_manifest((run / 'current.json').read_bytes())
        if current['release_id'] != release_id:
            raise ValueError('Research candidate belongs to a different task')
        if projected is not None:
            baseline_files = validate_manifest(previous)[0]['files'] if previous else {}
            identity = lambda files: {name: (item['sha256'], item['bytes']) for name, item in files.items()}
            if identity(current['files']) != identity(baseline_files):
                raise ValueError('Direct paper update requires unchanged directory/index projections; run the regular research build')
            if prepare_only:
                return {'prepared': True, 'release_id': release_id, 'generation': projected.generation}
            # A direct JSON candidate enters exactly the same baseline/CAS,
            # immutable upload and acknowledgement recovery mechanism as v2.
            result = publish_literature_candidate(run=run, objects_dir=shared / 'objects', papers=projected,
                config=config, receipt=receipt, client=client, workers=workers)
            from src.services.data_release.papers_projection import verify_projected_publication
            verification = (projection_verifier or verify_projected_publication)(projected)
            result['public_verification'] = verification
            _atomic_write(run / 'published.json', json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
            return result
        manifest_path = run / 'papers-build-manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_bytes())
        else:
            previous_manifest = validate_manifest(previous)[0] if previous else None
            old = (load_committed_papers_receipt(receipt, pointer=previous_manifest, target=_target(config, PREFIX))
                   if previous_manifest and previous_manifest.get('papers') else None)
            manifest = build_research_papers_manifest(stage, origin=config.public_base_url, build=build, previous=old)
            _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode())
        template_dir = run / 'paper-templates'
        # A run frozen by the previous publisher must retain its exact binding
        # when recovering an interrupted acknowledgement. All new runs use JSON.
        if current.get('papers') and not template_dir.exists():
            template_dir = None
        papers = prepare_papers_content_from_manifest(stage, manifest, papers_origin=config.public_base_url,
            release_id=release_id, template_dir=template_dir)
        freeze_papers(run, papers, build)
        if prepare_only:
            return {'prepared': True, 'release_id': release_id, 'generation': papers.generation}
        result = publish_literature_candidate(run=run, objects_dir=shared / 'objects', papers=papers,
            config=config, receipt=receipt, client=client, workers=workers, build=build)
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
