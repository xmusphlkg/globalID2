"""Operator CLI for publisher RSS validation and non-persisting dry runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .clients.rss import PublisherRssClient, validate_feed_whitelist


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Publisher RSS whitelist must be a JSON object")
    return payload


async def _probe(path: Path, *, contact_email: str) -> dict[str, Any]:
    return await PublisherRssClient(
        user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact_email})",
    ).probe_readiness(whitelist=_load(path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or dry-run the publisher RSS whitelist")
    parser.add_argument("path", type=Path)
    parser.add_argument("--probe", action="store_true", help="Fetch each enabled feed without persisting state")
    parser.add_argument("--contact-email", default="research-radar@globalinfectiousdisease.com")
    args = parser.parse_args()
    report = (
        asyncio.run(_probe(args.path, contact_email=args.contact_email))
        if args.probe
        else validate_feed_whitelist(_load(args.path))
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
