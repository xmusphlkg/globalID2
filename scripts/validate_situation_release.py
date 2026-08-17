#!/usr/bin/env python3
"""Validate built Situation Room artifacts before any Cloudflare deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.database import get_db  # noqa: E402
from src.domain import SituationPeriodReportV3, SituationPublicationPointerV3  # noqa: E402
from src.services.situation_v3.contracts import SituationReportV3  # noqa: E402


DIST = ROOT / "astro-site" / "dist"


async def validate(site_dir: Path = DIST) -> dict:
    site_dir = site_dir.resolve()
    page = site_dir / "situation" / "index.html"
    sitemap = site_dir / "sitemaps" / "situation.xml"
    page_exists = page.is_file()
    sitemap_exists = sitemap.is_file()
    html = page.read_text(encoding="utf-8") if page_exists else ""
    sitemap_xml = sitemap.read_text(encoding="utf-8") if sitemap_exists else ""

    async with get_db() as db:
        pointer = (
            await db.execute(
                select(SituationPublicationPointerV3).where(
                    SituationPublicationPointerV3.channel == "latest"
                )
            )
        ).scalar_one_or_none()
        if pointer is None:
            raise RuntimeError("No Situation Room v3 publication pointer exists")
        report = (
            await db.execute(
                select(SituationPeriodReportV3).where(
                    SituationPeriodReportV3.report_id == pointer.report_id
                )
            )
        ).scalar_one_or_none()
        if report is None:
            raise RuntimeError("Situation Room v3 publication pointer is dangling")
        contract = SituationReportV3.model_validate(report.payload)
        payload = contract.model_dump(mode="json")
        public_enabled = bool(payload.get("public_enabled", False))
        checks = [
            {
                "id": "analysis_gate_passed",
                "passed": contract.quality_gate.passed,
                "report_id": report.report_id,
            },
            {"id": "situation_page_built", "passed": page_exists, "path": str(page)},
            {"id": "situation_sitemap_built", "passed": sitemap_exists, "path": str(sitemap)},
        ]
        if public_enabled:
            latest_json = site_dir / "site-data" / "situation" / "v3" / "latest.json"
            legacy_alias = site_dir / "site-data" / "situation" / "latest.json"
            exported_payload = None
            if latest_json.is_file():
                exported_payload = SituationReportV3.model_validate_json(
                    latest_json.read_text(encoding="utf-8")
                )
            checks.extend(
                [
                    {"id": "public_latest_json", "passed": latest_json.is_file(), "path": str(latest_json)},
                    {
                        "id": "public_latest_matches_pointer",
                        "passed": bool(
                            exported_payload
                            and exported_payload.report.report_id == pointer.report_id
                        ),
                    },
                    {
                        "id": "legacy_latest_alias",
                        "passed": legacy_alias.is_file()
                        and legacy_alias.read_bytes() == latest_json.read_bytes(),
                        "path": str(legacy_alias),
                    },
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
        gate = {
            "status": "passed" if not failed else "failed",
            "passed": not failed,
            "failed_checks": failed,
            "checks": checks,
        }
        result = {
            "report_id": report.report_id,
            "public_enabled": public_enabled,
            "release_gate": gate,
        }
    # Report archives are immutable. Build/deployment checks gate the static
    # release without rewriting the already archived statistical report.
    if failed:
        raise RuntimeError("Situation release gate failed: " + ", ".join(str(item) for item in failed))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate built Situation Room artifacts before deployment"
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=DIST,
        help=f"Built static site directory (default: {DIST})",
    )
    args = parser.parse_args()
    result = asyncio.run(validate(args.site_dir))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
