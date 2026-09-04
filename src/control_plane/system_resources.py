"""Lightweight host metrics for the control-center shell."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROXY_ENVIRONMENT_KEYS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
EXIT_LOCATION_PROVIDERS = (
    "https://ipapi.co/json/",
    "https://ipwho.is/",
)
PROXY_LOOKUP_TTL_SECONDS = 3600
PROXY_LOOKUP_FAILURE_TTL_SECONDS = 300

_proxy_location_cache: tuple[float, dict[str, Any]] | None = None


def _read_cpu_percent() -> float | None:
    def read_sample() -> tuple[int, int] | None:
        try:
            fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
            if not fields or fields[0] != "cpu":
                return None
            values = [int(value) for value in fields[1:]]
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return total, idle
        except (IndexError, OSError, ValueError):
            return None

    first = read_sample()
    if first is None:
        return None
    time.sleep(0.08)
    second = read_sample()
    if second is None:
        return None
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _read_memory() -> dict[str, int | float | None]:
    try:
        values = {
            key.rstrip(":"): int(value) * 1024
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            if (parts := line.split()) and len(parts) >= 2
            for key, value in [(parts[0], parts[1])]
        }
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            raise ValueError("missing memory counters")
        used = max(0, total - available)
        return {"total_bytes": total, "used_bytes": used, "used_percent": round(used / total * 100, 1)}
    except (OSError, ValueError):
        return {"total_bytes": None, "used_bytes": None, "used_percent": None}


def _read_disk() -> dict[str, int | float | None]:
    try:
        usage = shutil.disk_usage("/")
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
        }
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "used_percent": None}


def _read_network_connections() -> dict[str, int]:
    total = established = listening = 0
    for filename in ("tcp", "tcp6", "udp", "udp6"):
        try:
            lines = Path("/proc/net").joinpath(filename).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            total += 1
            state = parts[3]
            established += state == "01"
            listening += state == "0A"
    return {"total": total, "established": established, "listening": listening}


def _configured_proxy() -> str | None:
    raw_value = next((os.environ.get(key) for key in PROXY_ENVIRONMENT_KEYS if os.environ.get(key)), None)
    if not raw_value:
        return None
    parsed = urlsplit(raw_value if "://" in raw_value else f"http://{raw_value}")
    if not parsed.hostname:
        return "configured"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _lookup_proxy_location() -> dict[str, Any]:
    """Resolve the actual public egress address, whether or not a proxy is env-configured."""
    for provider in EXIT_LOCATION_PROVIDERS:
        try:
            request = Request(provider, headers={"Accept": "application/json", "User-Agent": "GIDS-Control-Center/1.0"})
            with urlopen(request, timeout=2.0) as response:  # noqa: S310 - fixed HTTPS providers, short best-effort lookup
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("success") is False:
                continue
            country_name = payload.get("country_name", payload.get("country"))
            result = {
                "ip": payload.get("ip") if isinstance(payload.get("ip"), str) else None,
                "country_code": payload.get("country_code") if isinstance(payload.get("country_code"), str) else None,
                "country_name": country_name if isinstance(country_name, str) else None,
                "lookup_status": "available",
            }
            if result["ip"]:
                return result
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            continue
    return {"ip": None, "country_code": None, "country_name": None, "lookup_status": "unavailable"}


async def proxy_location() -> dict[str, Any]:
    """Return the observable egress location, cached for sidebar polling."""
    endpoint = _configured_proxy()

    global _proxy_location_cache
    now = time.monotonic()
    if _proxy_location_cache and now - _proxy_location_cache[0] < PROXY_LOOKUP_TTL_SECONDS:
        return {"configured": endpoint is not None, "endpoint": endpoint, **_proxy_location_cache[1]}

    result = await asyncio.to_thread(_lookup_proxy_location)
    ttl = PROXY_LOOKUP_TTL_SECONDS if result["lookup_status"] == "available" else PROXY_LOOKUP_FAILURE_TTL_SECONDS
    _proxy_location_cache = (now - PROXY_LOOKUP_TTL_SECONDS + ttl, result)
    return {"configured": endpoint is not None, "endpoint": endpoint, **result}


def system_resources() -> dict[str, Any]:
    """Collect local metrics in a small, dependency-free snapshot."""
    cpu_percent = _read_cpu_percent()
    try:
        load_1m = round(os.getloadavg()[0], 2)
    except OSError:
        load_1m = None
    return {
        "cpu": {"usage_percent": cpu_percent, "cores": os.cpu_count() or 0, "load_1m": load_1m},
        "memory": _read_memory(),
        "disk": _read_disk(),
        "network": _read_network_connections(),
    }


__all__ = ["proxy_location", "system_resources"]
