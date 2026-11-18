#!/usr/bin/env python3
"""Inspect or reconcile the local R2 object database without uploading objects.

Saved inventories must cover the complete bucket and the exact endpoint. Pause
all writers while taking and importing a snapshot. Clearing uncertain upload
reservations additionally requires an executable that verifies a publisher fence.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from scripts.publish_papers_r2 import _config_from_env
from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Client
from src.services.data_release.r2_object_index import DEFAULT_MAX_TOTAL_BYTES, R2ObjectIndex


def _load_environment(path: Path) -> None:
    for key, value in dotenv_values(path).items():
        if value is not None and key.startswith(("R2_", "CLOUDFLARE_")):
            os.environ[key] = value


def _target(endpoint: str | None, bucket: str | None) -> tuple[str, str]:
    account = os.environ.get("R2_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    endpoint = endpoint or os.environ.get("R2_ENDPOINT_URL") or (
        f"https://{account}.r2.cloudflarestorage.com" if account else None)
    bucket = bucket or os.environ.get("R2_PAPERS_BUCKET") or "globalid-papers"
    if not endpoint:
        raise ValueError("Specify --endpoint or configure an R2 endpoint/account")
    parsed = urlparse(endpoint)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Invalid R2 endpoint")
    if not bucket or "/" in bucket or bucket != bucket.strip():
        raise ValueError("Invalid R2 bucket")
    return endpoint.rstrip("/"), bucket


def _observed_timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError("Inventory requires a timezone-qualified observed_at timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        timestamp = parsed.timestamp()
        if not 0 < timestamp <= time.time() + 1:
            raise ValueError()
        return timestamp
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid inventory observed_at timestamp") from exc


def load_inventory(path: Path, *, endpoint: str, bucket: str, expected_sha256: str | None = None) -> tuple[dict, float]:
    raw = path.read_bytes()
    if expected_sha256 is not None:
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Inventory SHA-256 mismatch")
    saved = json.loads(raw)
    if (not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("bucket") != bucket
            or not isinstance(saved.get("endpoint"), str) or saved["endpoint"].rstrip("/") != endpoint):
        raise ValueError("Inventory schema or endpoint/bucket does not match the selected target")
    if (saved.get("complete") is False or saved.get("truncated") is True
            or not (saved.get("complete") is True
                    or saved.get("source") == "R2 ListObjectsV2 complete bucket listing")):
        raise ValueError("Inventory must explicitly represent a complete bucket listing")
    objects = saved.get("objects")
    if not isinstance(objects, dict):
        raise ValueError("Inventory objects must be a mapping")
    observed = _observed_timestamp(saved.get("observed_at"))
    if "started_at" in saved and _observed_timestamp(saved["started_at"]) > observed:
        raise ValueError("Inventory observation precedes its start")
    if "object_count" in saved and saved["object_count"] != len(objects):
        raise ValueError("Inventory object count does not match its contents")
    return objects, observed


def _fence_checker(path: Path | None):
    if path is None:
        raise ValueError("--recover-reservations requires --fence-check")
    checker = path.resolve(strict=True)
    if not checker.is_file() or not os.access(checker, os.X_OK):
        raise ValueError("Fence checker must be an executable file")

    def assert_fenced() -> None:
        try:
            result = subprocess.run([str(checker)], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Publisher fence check failed") from exc
        if result.returncode:
            # Fence scripts may inspect credentials; never echo their output.
            raise ValueError("Publisher fence check failed")

    return assert_fenced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "reconcile"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--index-path", type=Path)
    parser.add_argument("--endpoint")
    parser.add_argument("--bucket")
    parser.add_argument("--max-total-bytes", type=int)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inventory-sha256")
    parser.add_argument("--recover-reservations", action="store_true")
    parser.add_argument("--fence-check", type=Path)
    args = parser.parse_args(argv)
    if args.command != "reconcile" and (args.inventory or args.recover_reservations or args.fence_check or args.inventory_sha256):
        parser.error("Inventory and recovery options apply only to reconcile")
    if args.inventory_sha256 and not args.inventory:
        parser.error("--inventory-sha256 requires --inventory")
    if args.recover_reservations and not args.fence_check:
        parser.error("--recover-reservations requires --fence-check")
    _load_environment(args.env_file)
    endpoint, bucket = _target(args.endpoint, args.bucket)
    budget = args.max_total_bytes if args.max_total_bytes is not None else int(
        os.environ.get("R2_PAPERS_MAX_TOTAL_BYTES", str(DEFAULT_MAX_TOTAL_BYTES)))
    if budget <= 0:
        parser.error("--max-total-bytes must be positive")
    index = R2ObjectIndex(endpoint, bucket, path=args.index_path, max_total_bytes=budget)
    client = None
    try:
        if args.command == "reconcile":
            assert_fenced = _fence_checker(args.fence_check) if args.fence_check else None
            if assert_fenced:
                assert_fenced()
            if args.inventory:
                objects, observed = load_inventory(args.inventory, endpoint=endpoint, bucket=bucket,
                                                   expected_sha256=args.inventory_sha256)
                # This client is used solely for the same local lock as publishers.
                # Offline import and status never derive credentials or contact R2.
                config = R2PapersConfig("offline", bucket, "", "", endpoint_url=endpoint)
                client = R2S3Client(config, object_index=index, max_total_bytes=budget)
                with client._index_lock(exclusive=True):
                    if assert_fenced:
                        assert_fenced()
                    latest = index.inventory_checked_at
                    if latest is not None and observed < latest:
                        raise ValueError("Saved inventory is older than the indexed inventory; take a fresh snapshot")
                    index.replace_inventory(objects, checked_at=observed,
                                            recover_reservations=args.recover_reservations)
            else:
                # Explicit flags select the same target for online credentials.
                os.environ["R2_ENDPOINT_URL"], os.environ["R2_PAPERS_BUCKET"] = endpoint, bucket
                config = _config_from_env()
                client = R2S3Client(config, object_index=index, max_total_bytes=budget)
                if args.recover_reservations:
                    with client._index_lock(exclusive=True):
                        assert_fenced()
                        objects = client.list_objects()
                        assert_fenced()
                        index.replace_inventory(objects, recover_reservations=True)
                else:
                    client.refresh_inventory(force=True)
            print(json.dumps({"command": "reconcile", "source": "saved_inventory" if args.inventory else "R2 ListObjectsV2",
                              "recovered_reservations": args.recover_reservations, **index.stats()}, indent=2))
        else:
            print(json.dumps({"command": "status", **index.stats()}, indent=2))
    finally:
        if client is not None:
            client.close()
        index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
