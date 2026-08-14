#!/usr/bin/env python3
"""Validate built Situation Room artifacts before any Cloudflare deployment."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.domain import SituationSnapshot  # noqa: E402
from src.services.situation_history_service import archive_snapshot  # noqa: E402


DIST = ROOT / "astro-site" / "dist"


async def validate() -> dict:
    page = DIST / "situation" / "index.html"
    sitemap = DIST / "sitemaps" / "situation.xml"
    page_exists = page.is_file()
    sitemap_exists = sitemap.is_file()
    html = page.read_text(encoding="utf-8") if page_exists else ""
    sitemap_xml = sitemap.read_text(encoding="utf-8") if sitemap_exists else ""

    async with get_db() as db:
        snapshot = (
            await db.execute(
                select(SituationSnapshot)
                .where(SituationSnapshot.snapshot_kind == "daily")
                .order_by(SituationSnapshot.checked_at.desc(), SituationSnapshot.revision.desc())
            )
        ).scalars().first()
        if snapshot is None:
            raise RuntimeError("No daily Situation Room snapshot exists")
        payload = dict(snapshot.payload or {})
        public_enabled = bool(payload.get("public_enabled", False))
        checks = [
            {"id": "situation_page_built", "passed": page_exists, "path": str(page)},
            {"id": "situation_sitemap_built", "passed": sitemap_exists, "path": str(sitemap)},
        ]
        if public_enabled:
            latest_json = DIST / "site-data" / "situation" / "latest.json"
            checks.extend(
                [
                    {"id": "public_latest_json", "passed": latest_json.is_file(), "path": str(latest_json)},
                    {"id": "public_page_indexable", "passed": "noindex" not in html.lower()},
                    {"id": "public_sitemap_entry", "passed": "/situation/" in sitemap_xml},
                ]
            )
        else:
            checks.extend(
                [
                    {"id": "shadow_page_noindex", "passed": "noindex,nofollow" in html.lower()},
                    {"id": "shadow_sitemap_hidden", "passed": "/situation/" not in sitemap_xml},
                ]
            )
        failed = [check["id"] for check in checks if not check["passed"]]
        prior_gate = dict(snapshot.quality_gate or payload.get("quality_gate") or {})
        prior_checks = [check for check in prior_gate.get("checks") or [] if check.get("id") not in {item["id"] for item in checks}]
        combined_checks = [*prior_checks, *checks]
        failed = [check.get("id") for check in combined_checks if not check.get("passed")]
        gate = {"status": "passed" if not failed else "failed", "passed": not failed, "failed_checks": failed, "checks": combined_checks}
        snapshot.quality_gate = gate
        snapshot.quality_gate_status = gate["status"]
        snapshot.status = "published" if gate["passed"] else "quality_failed"
        payload["quality_gate"] = gate
        payload["quality_gate_status"] = gate["status"]
        snapshot.payload = payload
        result = {"snapshot_id": snapshot.snapshot_id, "public_enabled": public_enabled, "quality_gate": gate}
    # Keep the durable archive aligned with the post-build quality decision,
    # not merely the pre-build statistical gate recorded during refresh.
    await archive_snapshot(snapshot)
    if failed:
        raise RuntimeError("Situation release gate failed: " + ", ".join(str(item) for item in failed))
    return result


def main() -> None:
    result = asyncio.run(validate())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
