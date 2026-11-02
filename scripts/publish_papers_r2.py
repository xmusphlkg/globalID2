#!/usr/bin/env python3
"""Incrementally publish the papers site bundle to Cloudflare R2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.papers_bundle import DEFAULT_PAPERS_ORIGIN
from src.services.data_release.papers_r2 import (
    R2PapersConfig,
    derive_r2_credentials_from_api_token,
    publish_papers_site,
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
        "--skip-public-verify",
        action="store_true",
        help="Skip the public HEAD/GET sample check after upload",
    )
    args = parser.parse_args(argv)
    config = _config_from_env()
    result = publish_papers_site(
        args.source_dir,
        config,
        verify_public=not args.skip_public_verify,
    )
    print(
        json.dumps(
            {
                "uploaded": result.uploaded,
                "removed": result.removed,
                "unchanged": result.unchanged,
                "bytes_uploaded": result.bytes_uploaded,
                "manifest_url": result.manifest_url,
                "sample_url": result.sample_url,
                "sample_ok": result.sample_ok,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
