#!/usr/bin/env python3
"""Check an R2-backed reports host serves its control files and report pages."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def _fetch(client: httpx.Client, url: str, *, expected: str | None = None) -> httpx.Response:
    response = client.get(url)
    response.raise_for_status()
    if expected and expected not in response.text:
        raise RuntimeError(f"Reports host verification did not find expected content at {url}")
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--reports-origin", required=True)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    origin = args.reports_origin.rstrip("/")
    manifest_path = args.source_dir / "reports-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    current_assets = set((manifest.get("asset_generations") or [[]])[0])
    assets = [item for item in files if item.get("path") in current_assets]
    sample = next((item for item in files if str(item.get("path", "")).startswith("countries/") and str(item.get("path", "")).endswith("index.html")), None)
    directory = next((item for item in files if item.get("path") == "reports/index.html"), None)
    if directory is None:
        raise RuntimeError("Reports manifest does not include the report directory")
    data_index = next((item for item in files if item.get("path") == "site-data/reports/index.json"), None)
    if data_index is None:
        raise RuntimeError("Reports manifest does not include the public report data index")
    with httpx.Client(follow_redirects=True, timeout=args.timeout) as client:
        sitemap = _fetch(client, origin + "/sitemap.xml", expected="<urlset")
        if f"<loc>{origin}/reports/</loc>" not in sitemap.text:
            raise RuntimeError("Reports sitemap does not include the report directory")
        robots = _fetch(client, origin + "/robots.txt", expected=origin + "/sitemap.xml")
        directory_url = origin + "/" + str(directory.get("key") or "") if directory else None
        if directory:
            _fetch(client, directory_url or "", expected="Public report archive")
        data_index_url = origin + "/" + str(data_index.get("key") or "")
        data_index_response = _fetch(client, data_index_url, expected="[")
        reports = data_index_response.json()
        if not isinstance(reports, list):
            raise RuntimeError("Public report data index must be a JSON array")
        if reports:
            report_id = reports[0].get("id") if isinstance(reports[0], dict) else None
            detail = next(
                (
                    item for item in files
                    if item.get("path") == f"site-data/reports/{report_id}.json"
                ),
                None,
            )
            if detail is None:
                raise RuntimeError(f"Reports manifest does not include public report data for #{report_id}")
            detail_url = origin + "/" + str(detail.get("key") or "")
            detail_payload = _fetch(client, detail_url, expected='"report_document_v4"').json()
            if not isinstance(detail_payload, dict):
                raise RuntimeError(f"Public report data #{report_id} must be a JSON object")
        sample_url = origin + "/" + str(sample.get("key") or "") if sample else None
        if sample:
            _fetch(client, sample_url or "", expected="</html>")
        def verify_asset(item):
            response = _fetch(client, origin + "/" + item["key"])
            if hashlib.sha256(response.content).hexdigest() != item["sha256"]:
                raise RuntimeError(f"Reports asset content mismatch: {item['path']}")
            content_type = response.headers.get("content-type", "")
            if item["path"].endswith(".js") and "javascript" not in content_type:
                raise RuntimeError(f"Reports script has invalid MIME type: {item['path']}")
            if item["path"].endswith(".css") and "text/css" not in content_type:
                raise RuntimeError(f"Reports stylesheet has invalid MIME type: {item['path']}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(verify_asset, assets))
    print(json.dumps({
        "origin": origin,
        "directory_url": directory_url,
        "report_data_index_url": data_index_url,
        "report_data_count": len(reports),
        "sitemap_status": sitemap.status_code,
        "robots_status": robots.status_code,
        "sample_report_url": sample_url,
        "sample_report_verified": bool(sample),
        "verified_assets": len(assets),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
