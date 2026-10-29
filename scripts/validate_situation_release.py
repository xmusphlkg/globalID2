#!/usr/bin/env python3
"""Validate the alert-based Situation export and built pages before deployment.

File-only checks (no database), against ``gids.situation_alerts.v1``:

* ``map.json`` exists with the expected schema version, in every export file;
* every map point links to an http(s) authority URL;
* a quote is present exactly where the source's redistribution terms allow it;
* no field carries full report text (``text``, ``document``, ``full_text``...);
* every map outbreak has a timeline file, and every point's alert is on it;
* the built ``/situation/`` page exists in every locale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "gids.situation_alerts.v1"
QUOTE_TERMS = "metadata_and_extract"
QUOTE_LIMIT = 600
DATA = ROOT / "astro-site" / "src" / "data" / "situation" / "international"
DIST = ROOT / "astro-site" / "dist"
LOCALE_PREFIXES = ("", "zh/", "fr/")
FORBIDDEN_KEYS = {"text", "document", "full_text", "body", "content", "raw_text"}


def _read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _walk_keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def _is_url(value: Any) -> bool:
    return str(value or "").startswith(("http://", "https://"))


def validate(data_dir: Path = DATA, site_dir: Path = DIST) -> dict:
    checks: list[dict] = []

    def check(check_id: str, passed: bool, detail: str = "") -> None:
        checks.append({"id": check_id, "passed": bool(passed), "detail": detail})

    map_file = _read(data_dir / "map.json")
    check("export_present", map_file is not None, str(data_dir / "map.json"))
    map_file = map_file or {}
    points = map_file.get("points") or []

    files = [data_dir / name for name in ("map.json", "monitoring.json", "sources.json")]
    files += sorted((data_dir / "outbreaks").glob("*.json")) + sorted((data_dir / "weekly").glob("*.json"))
    payloads = {path: _read(path) for path in files}
    bad_schema = [
        str(path.relative_to(data_dir))
        for path, payload in payloads.items()
        if payload is None or payload.get("schema_version") != SCHEMA_VERSION
    ]
    check("schema_version", not bad_schema, ", ".join(bad_schema[:5]))

    check("points_present", len(points) > 0, f"{len(points)} map points")
    bad_urls = [str(p.get("alert_id")) for p in points if not _is_url(p.get("url"))]
    check("point_urls", not bad_urls, ", ".join(bad_urls[:5]))

    sources = {
        str(row.get("source_id")): row.get("redistribution")
        for row in (payloads.get(data_dir / "sources.json") or {}).get("sources") or []
    }
    quote_problems: list[str] = []
    for point in points:
        quote = point.get("quote")
        quotable = sources.get(str(point.get("source_id"))) == QUOTE_TERMS
        if quotable and not (isinstance(quote, str) and quote.strip()):
            quote_problems.append(f"{point.get('alert_id')}: quote missing")
        elif not quotable and quote:
            quote_problems.append(f"{point.get('alert_id')}: quote published without cleared terms")
        elif isinstance(quote, str) and len(quote) > QUOTE_LIMIT:
            quote_problems.append(f"{point.get('alert_id')}: quote longer than {QUOTE_LIMIT}")
    check("quotes", not quote_problems, "; ".join(quote_problems[:5]))

    leaked = sorted(
        {
            f"{path.relative_to(data_dir)}:{key}"
            for path, payload in payloads.items()
            for key in _walk_keys(payload)
            if key.lower() in FORBIDDEN_KEYS
        }
    )
    check("no_full_text", not leaked, ", ".join(leaked[:5]))

    timelines: dict[str, set[str]] = {}
    for path, payload in payloads.items():
        if path.parent.name == "outbreaks" and payload:
            timelines[str(payload.get("outbreak_id") or path.stem)] = {
                str(alert.get("alert_id")) for alert in payload.get("alerts") or []
            }
    missing_files = [
        str(item.get("outbreak_id"))
        for item in map_file.get("outbreaks") or []
        if str(item.get("outbreak_id")) not in timelines
    ]
    check("outbreak_files", not missing_files, ", ".join(missing_files[:5]))
    orphans = [
        str(point.get("alert_id"))
        for point in points
        if point.get("outbreak_id")
        and str(point.get("alert_id")) not in timelines.get(str(point["outbreak_id"]), set())
    ]
    check("point_in_timeline", not orphans, ", ".join(orphans[:5]))
    empty = [outbreak for outbreak, alerts in timelines.items() if not alerts]
    check("timelines_not_empty", not empty, ", ".join(empty[:5]))

    site_dir = site_dir.resolve()
    missing_pages = [
        f"{prefix}situation/index.html"
        for prefix in LOCALE_PREFIXES
        if not (site_dir / prefix / "situation" / "index.html").is_file()
    ]
    check("built_pages", not missing_pages, ", ".join(missing_pages))

    return {"passed": all(item["passed"] for item in checks), "points": len(points), "checks": checks}


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
