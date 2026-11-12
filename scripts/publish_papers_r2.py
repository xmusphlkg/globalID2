#!/usr/bin/env python3
"""Incrementally publish the papers site bundle to Cloudflare R2."""

from __future__ import annotations

import argparse
import json
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.papers_bundle import DEFAULT_PAPERS_ORIGIN
from src.services.data_release.papers_r2 import (
    R2PapersConfig,
    derive_r2_credentials_from_api_token,
)


def _config_from_env() -> R2PapersConfig:
    values = {
        "account_id": os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        or os.environ.get("R2_ACCOUNT_ID")
        or "",
        "bucket": os.environ.get("R2_PAPERS_BUCKET") or "globalid-papers",
        "access_key_id": os.environ.get("R2_ACCESS_KEY_ID") or "",
        "secret_access_key": os.environ.get("R2_SECRET_ACCESS_KEY") or "",
        "public_base_url": os.environ.get("R2_PAPERS_PUBLIC_BASE_URL")
        or DEFAULT_PAPERS_ORIGIN,
        "endpoint_url": os.environ.get("R2_ENDPOINT_URL") or "",
    }
    if not (values["access_key_id"] and values["secret_access_key"]):
        derived = derive_r2_credentials_from_api_token(
            os.environ.get("CLOUDFLARE_API_TOKEN") or ""
        )
        if derived:
            values["access_key_id"], values["secret_access_key"] = derived
    config = R2PapersConfig.from_mapping(values)
    if config is None:
        raise SystemExit(
            "R2 papers publish requires CLOUDFLARE_ACCOUNT_ID (or R2_ACCOUNT_ID), "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_PAPERS_BUCKET."
        )
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=ROOT / "exports" / "papers-site",
        help="Local papers bundle produced by split_papers_bundle.py",
    )
    parser.add_argument(
        "--workers", type=int, default=int(os.environ.get("R2_PAPERS_UPLOAD_WORKERS", "32")),
        help="Concurrent pooled R2 transfers (1–96; default: 32)",
    )
    parser.add_argument(
        "--max-total-bytes", type=int, default=int(os.environ.get("R2_PAPERS_MAX_TOTAL_BYTES", "8000000000")),
        help="Conservative total bucket budget, including other data channels (default: 8 GB)",
    )
    parser.add_argument(
        "--skip-public-verify",
        action="store_true",
        help="Skip the public HEAD/GET sample check after upload",
    )
    parser.add_argument('--site-dist', type=Path, default=ROOT / 'astro-site/dist')
    parser.add_argument('--research-source', type=Path, default=ROOT / 'astro-site/public/site-data/research')
    parser.add_argument('--run', type=Path, default=os.environ.get('GIDS_RESEARCH_PUBLICATION_RUN'))
    parser.add_argument('--release-id', default=os.environ.get('GIDS_RESEARCH_RELEASE_ID'))
    parser.add_argument('--template-dir', type=Path)
    parser.add_argument('--inventory', type=Path, help='Verified remote inventory for one-time migration reuse')
    parser.add_argument('--capture-baseline-only', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--preserve-research-data', action='store_true',
        default=os.environ.get('GIDS_PRESERVE_RESEARCH_DATA') == '1')
    args = parser.parse_args(argv)
    config = _config_from_env()
    if not args.release_id or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', args.release_id):
        parser.error('--release-id (or GIDS_RESEARCH_RELEASE_ID) is required')
    run = args.run or ROOT / 'exports/site-research-runs' / args.release_id
    if args.capture_baseline_only:
        from src.services.data_release.surveillance_publish import capture_surveillance_baseline
        result = capture_surveillance_baseline(run, config, prefix='site-data/research-publication')
    else:
        from src.services.data_release.site_research_publish import publish_site_research
        inventory = json.loads(args.inventory.read_bytes()) if args.inventory else None
        if inventory is not None and 'objects' in inventory:
            if inventory.get('bucket') != config.bucket:
                raise ValueError('Inventory belongs to another bucket')
            inventory = inventory['objects']
        if inventory is not None and sum(int(item['bytes']) for item in inventory.values()) > args.max_total_bytes:
            raise ValueError('R2 bucket already exceeds publication storage budget')
        result = publish_site_research(papers_dir=args.source_dir, site_dist=args.site_dist,
            research_source=args.research_source, run=run, release_id=args.release_id, config=config,
            receipt=ROOT / 'data/cache/isolated-research-publication.json',
            template_dir=args.template_dir, inventory=inventory, workers=args.workers,
            prepare_only=args.prepare_only, preserve_research_data=args.preserve_research_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
