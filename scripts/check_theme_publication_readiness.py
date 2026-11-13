#!/usr/bin/env python3
"""Read-only readiness check for the v1 independent theme publication.

The command only reads configuration and, when a target is configured, fetches
the compatibility CSS. It never uploads CSS, changes an R2 pointer, or enables
the publication feature.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config

ENTRY = "theme/compat/v1/theme.css"


def _origin(value: str) -> bool:
    parsed = urlsplit(value.rstrip("/"))
    return (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.path in {"", "/"} and not parsed.hostname.endswith(".r2.dev"))


def _read_css(url: str, timeout: float) -> dict:
    try:
        with urlopen(Request(url, headers={"Cache-Control": "no-cache",
                                           "User-Agent": "GlobalID-Theme-Readiness/1"}), timeout=timeout) as response:
            body = response.read(1024 * 1024 + 1)
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if response.geturl() != url:
                return {"available": False, "error_code": "theme_public_redirect_not_allowed"}
            if len(body) > 1024 * 1024:
                return {"available": False, "error_code": "theme_public_object_oversized"}
            if content_type != "text/css":
                return {"available": False, "error_code": "theme_public_content_type_invalid"}
            return {"available": True, "status": 200, "bytes": len(body)}
    except HTTPError as exc:
        return {"available": False, "error_code": f"theme_http_{exc.code}"}
    except (OSError, URLError):
        return {"available": False, "error_code": "theme_public_unreachable"}


def check(*, timeout_seconds: float = 10) -> dict:
    config = get_config().data_release
    bucket = str(getattr(config, "theme_r2_bucket", "") or "").strip()
    base = str(getattr(config, "theme_public_base_url", "") or "").strip().rstrip("/")
    stylesheet = str(getattr(config, "theme_stylesheet_url", "") or "").strip()
    result = {"schema_version": 1, "channel": "theme", "ready": False,
              "publication_enabled": bool(getattr(config, "theme_publish_enabled", False)),
              "compat_v1_ready": bool(getattr(config, "theme_compat_v1_ready", False)),
              "bucket_configured": bool(bucket), "public_base_url": base,
              "stylesheet_url": stylesheet, "public_probe": None, "reasons": []}
    if not bucket:
        result["reasons"].append("theme_public_bucket_unconfigured")
    if not _origin(base):
        result["reasons"].append("theme_public_destination_unconfigured")
    expected = f"{base}/{ENTRY}" if _origin(base) else ""
    if stylesheet != expected:
        result["reasons"].append("theme_compatible_templates_not_verified")
    if bucket in {getattr(config, "r2_papers_bucket", None), getattr(config, "r2_reports_bucket", None)}:
        result["reasons"].append("theme_requires_dedicated_public_bucket")
    if not result["reasons"]:
        result["public_probe"] = _read_css(stylesheet, timeout_seconds)
        if not result["public_probe"].get("available"):
            result["reasons"].append(result["public_probe"].get("error_code", "theme_public_probe_failed"))
    if not result["compat_v1_ready"]:
        result["reasons"].append("theme_compatible_templates_not_verified")
    if not result["publication_enabled"]:
        result["reasons"].append("theme_publication_disabled")
    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    result["ready"] = not result["reasons"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    value = check(timeout_seconds=args.timeout_seconds)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0 if value["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
