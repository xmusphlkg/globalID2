"""Subscription management router backed by the Cloudflare Worker admin API."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter, HTTPException, Query

from src.services.settings_service import system_settings_service

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv is in project requirements.
    dotenv_values = None

router = APIRouter()

ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT_DIR / ".env"
SUBSCRIPTION_SCRIPT = ROOT_DIR / "cloudflare" / "subscriptions" / "scripts" / "wrangler-env.sh"


def _dotenv() -> dict[str, str]:
    if dotenv_values is None or not ENV_PATH.exists():
        return {}
    values = dotenv_values(ENV_PATH)
    return {key: str(value) for key, value in values.items() if value is not None}


def _env_value(name: str, default: str = "") -> str:
    return (os.getenv(name) or _dotenv().get(name) or default).strip()


def _worker_base_url() -> str:
    return _env_value("SUBSCRIPTIONS__PUBLIC_BASE_URL").rstrip("/")


def _admin_token() -> str:
    return _env_value("SUBSCRIPTIONS__ADMIN_API_TOKEN")


def _worker_configured() -> bool:
    return bool(_worker_base_url() and _admin_token())


async def _worker_request(
    path: str,
    *,
    method: str = "GET",
    payload: Optional[dict[str, Any]] = None,
    query: Optional[dict[str, Any]] = None,
    admin: bool = True,
    timeout: int = 30,
) -> dict[str, Any]:
    base_url = _worker_base_url()
    if not base_url:
        raise HTTPException(400, "SUBSCRIPTIONS__PUBLIC_BASE_URL is not configured.")
    if admin and not _admin_token():
        raise HTTPException(400, "SUBSCRIPTIONS__ADMIN_API_TOKEN is not configured.")

    url = f"{base_url}{path}"
    if query:
        clean_query = {
            key: str(value)
            for key, value in query.items()
            if value is not None and str(value).strip() != ""
        }
        if clean_query:
            url = f"{url}?{urlparse.urlencode(clean_query)}"

    body = None
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["Authorization"] = f"Bearer {_admin_token()}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    def send() -> dict[str, Any]:
        req = urlrequest.Request(url, data=body, headers=headers, method=method)
        try:
            with urlrequest.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") or str(exc)
            raise HTTPException(exc.code, detail) from exc
        except (urlerror.URLError, TimeoutError) as exc:
            raise HTTPException(502, f"Subscription Worker is unreachable: {exc}") from exc

        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(502, "Subscription Worker returned invalid JSON.") from exc
        return data if isinstance(data, dict) else {"data": data}

    return await asyncio.to_thread(send)


@router.get("/subscriptions/config")
async def subscription_config():
    base_url = _worker_base_url()
    return {
        "ok": True,
        "configured": _worker_configured(),
        "base_url": base_url,
        "admin_token_present": bool(_admin_token()),
        "d1_database_name": _env_value("SUBSCRIPTIONS__D1_DATABASE_NAME"),
        "d1_database_id_present": bool(_env_value("SUBSCRIPTIONS__D1_DATABASE_ID")),
        "sync_options_on_release": _env_value("SUBSCRIPTIONS__SYNC_OPTIONS_ON_RELEASE", "auto"),
    }


@router.get("/subscriptions/options")
async def subscription_options():
    return await _worker_request("/api/subscriptions/options", admin=False)


@router.get("/subscriptions/stats")
async def subscription_stats():
    return await _worker_request("/api/admin/stats")


@router.get("/subscriptions/records")
async def subscription_records(
    status: Optional[str] = Query(default=None),
    list_code: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
):
    return await _worker_request(
        "/api/admin/subscriptions",
        query={
            "status": status,
            "list_code": list_code,
            "q": q,
            "limit": limit,
            "offset": offset,
        },
    )


@router.post("/subscriptions/audience")
async def subscription_audience(payload: dict[str, Any]):
    return await _worker_request("/api/admin/audience", method="POST", payload=payload)


@router.post("/subscriptions/maintenance")
async def subscription_maintenance():
    return await _worker_request("/api/admin/maintenance", method="POST", payload={})


@router.post("/subscriptions/sync-options")
async def subscription_sync_options():
    if not SUBSCRIPTION_SCRIPT.exists():
        raise HTTPException(404, f"Subscription helper script not found: {SUBSCRIPTION_SCRIPT}")

    merged_env = os.environ.copy()
    merged_env.update({key: value for key, value in _dotenv().items() if key not in merged_env})
    cloudflare = system_settings_service.cloudflare_runtime()
    if cloudflare.get("cloudflare_api_token"):
        merged_env["CLOUDFLARE_API_TOKEN"] = cloudflare["cloudflare_api_token"]
    if cloudflare.get("cloudflare_account_id"):
        merged_env["CLOUDFLARE_ACCOUNT_ID"] = cloudflare["cloudflare_account_id"]

    proc = await asyncio.create_subprocess_exec(
        str(SUBSCRIPTION_SCRIPT),
        "sync-options-remote",
        cwd=str(ROOT_DIR),
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise HTTPException(504, "Subscription option sync timed out after 180 seconds.") from exc

    output = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise HTTPException(500, output[-4000:] or "Subscription option sync failed.")

    return {
        "ok": True,
        "message": "Subscription options synced to D1.",
        "output": output[-4000:],
    }
