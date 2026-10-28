#!/usr/bin/env python3
"""Validate the built notice-based Situation pages before any deployment.

Checks, all against files on disk (no database access):

* the international export exists, carries the expected schema version, and
  holds at least one notice;
* every notice links to an http(s) authority URL;
* every published digest point cites notice ids that exist in the export;
* the built ``/situation/`` page exists in every locale, and each published
  weekly digest has a built page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "gids.international_authority_events.v1"
DATA = ROOT / "astro-site" / "src" / "data" / "situation" / "international"
DIST = ROOT / "astro-site" / "dist"
LOCALE_PREFIXES = ("", "zh/", "fr/")


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def validate(data_dir: Path = DATA, site_dir: Path = DIST) -> dict:
    checks: list[dict] = []

    def check(check_id: str, passed: bool, detail: str = "") -> None:
        checks.append({"id": check_id, "passed": bool(passed), "detail": detail})

    latest = _read(data_dir / "latest.json")
    check("export_present", latest is not None, str(data_dir / "latest.json"))
    check("schema_version", bool(latest) and latest.get("schema_version") == SCHEMA_VERSION)
    recent = (latest or {}).get("recent") or []
    check("notices_present", len(recent) > 0, f"{len(recent)} recent notices")
    bad_urls = [row.get("event_id") for row in recent if not str(row.get("url") or "").startswith(("http://", "https://"))]
    check("notice_urls", not bad_urls, ", ".join(map(str, bad_urls[:5])))

    known_ids: set[str] = set()
    for archive in sorted((data_dir / "archive").glob("*.json")):
        for row in (_read(archive) or {}).get("events") or []:
            known_ids.add(str(row.get("event_id")))
    known_ids.update(str(row.get("event_id")) for row in recent)

    index = _read(data_dir / "digests" / "index.json") or {}
    periods = [str(row.get("period_key")) for row in index.get("periods") or [] if row.get("period_key")]
    uncited: list[str] = []
    for period in periods:
        digest = _read(data_dir / "digests" / f"{period}.json") or {}
        for point in digest.get("points") or []:
            ids = [str(item) for item in point.get("notice_ids") or []]
            if not ids or any(item not in known_ids for item in ids):
                uncited.append(period)
                break
    check("digest_citations", not uncited, ", ".join(uncited))

    site_dir = site_dir.resolve()
    missing_pages = [
        f"{prefix}situation/index.html"
        for prefix in LOCALE_PREFIXES
        if not (site_dir / prefix / "situation" / "index.html").is_file()
    ]
    missing_pages += [
        f"situation/weekly/{period}/index.html"
        for period in periods
        if not (site_dir / "situation" / "weekly" / period / "index.html").is_file()
    ]
    check("built_pages", not missing_pages, ", ".join(missing_pages[:10]))

    return {"passed": all(item["passed"] for item in checks), "digests": len(periods), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=str(DATA))
    parser.add_argument("--site-dir", default=str(DIST))
    args = parser.parse_args()
    result = validate(Path(args.data_dir), Path(args.site_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
