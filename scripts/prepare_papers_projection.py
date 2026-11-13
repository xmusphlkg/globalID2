#!/usr/bin/env python3
"""Prepare an opt-in Papers v3 migration or single-body update, without publishing."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.data_release.papers_content import prepare_papers_content_from_manifest
from src.services.data_release.papers_projection import load_projection_candidate, prepare_projection_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-candidate', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--build-manifest', type=Path)
    parser.add_argument('--legacy-templates', type=Path)
    parser.add_argument('--articles-dir', type=Path)
    parser.add_argument('--article', type=Path, action='append', default=[])
    parser.add_argument('--templates', type=Path, help='JSON mapping en/zh/fr to compatible v3 shells')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--base-release-id', help='Exact release identity of the verified v2 catalogue being migrated')
    args = parser.parse_args()
    migration = args.previous_candidate is None
    if migration:
        if not all((args.source_root, args.build_manifest, args.legacy_templates, args.articles_dir, args.base_release_id)):
            parser.error('Migration requires source-root, build-manifest, legacy-templates articles-dir and base-release-id')
        manifest = json.loads(args.build_manifest.read_bytes())
        base = prepare_papers_content_from_manifest(args.source_root, manifest,
            papers_origin=manifest['papers_origin'], release_id=args.base_release_id, template_dir=args.legacy_templates)
        slugs = sorted({entry['paper_template']['slug'] for entry in base.entries if 'paper_template' in entry})
        articles = [json.loads((args.articles_dir / f'{slug}.json').read_bytes()) for slug in slugs]
    else:
        base = load_projection_candidate(args.previous_candidate)
        articles = [json.loads(path.read_bytes()) for path in args.article]
    templates = json.loads(args.templates.read_bytes()) if args.templates else None
    candidate, receipt = prepare_projection_candidate(base, articles=articles, directory=args.output,
        release_id=args.release_id, migration=migration, templates=templates)
    print(json.dumps({'event': 'papers_projection_prepared', 'candidate': str(args.output / 'candidate.json'),
                      'generation': candidate.generation, **receipt}, ensure_ascii=False))

if __name__ == '__main__':
    main()
