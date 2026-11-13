#!/usr/bin/env python3
"""Read-only readiness check for the Literature JSON publication channel.

The check never uploads, changes the publication pointer, acknowledges dirty
markers, or enables the channel. It verifies the configured public pointer and
one bounded immutable sample using the same monitor as the control plane.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config
from src.services.data_release.research_monitor import probe_public_research


def _valid_https_origin(value: str) -> bool:
    parsed = urlsplit(value.rstrip("/"))
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in {"", "/"}


async def check(*, pointer_url: str | None = None, papers_origin: str | None = None,
                timeout_seconds: float = 10, object_limit_bytes: int = 8 * 1024 * 1024) -> dict:
    config = get_config().data_release
    enabled = bool(getattr(config, "literature_json_enabled", False))
    origin = (papers_origin or getattr(config, "r2_papers_public_base_url", "")).strip().rstrip("/")
    pointer = (pointer_url or "https://globalinfectiousdisease.com/site-data/research-publication/current.json").strip()
    result: dict = {
        "schema_version": 1,
        "channel": "literature",
        "automatic_publication_enabled": enabled,
        "pointer_url": pointer,
        "papers_origin": origin,
        "public_probe": None,
        "ready": False,
        "reasons": [],
    }
    if not _valid_https_origin(origin):
        result["reasons"].append("papers_public_origin_unconfigured")
    if not pointer.startswith("https://"):
        result["reasons"].append("literature_pointer_url_invalid")
    if result["reasons"]:
        if not enabled:
            result["reasons"].append("literature_json_disabled")
        return result
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_seconds,
                                 headers={"User-Agent": "GlobalID-Literature-Readiness/1"}) as client:
        probe = await probe_public_research(client, pointer, papers_origin=origin,
            timeout_seconds=timeout_seconds, object_limit_bytes=object_limit_bytes)
    result["public_probe"] = probe
    if not probe.get("available"):
        result["reasons"].append(str(probe.get("error_code") or "literature_public_probe_failed"))
    result["ready"] = enabled and not result["reasons"]
    if not enabled:
        result["reasons"].append("literature_json_disabled")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer-url")
    parser.add_argument("--papers-origin")
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--object-limit-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    value = asyncio.run(check(pointer_url=args.pointer_url, papers_origin=args.papers_origin,
                              timeout_seconds=args.timeout_seconds, object_limit_bytes=args.object_limit_bytes))
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0 if value["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
