"""Subscription management router backed by the Cloudflare Worker admin API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
from pathlib import Path
from typing import Any, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel, Field

from src.core import get_logger
from src.services.model_operations import run_worker_operation
from src.services.settings_service import system_settings_service

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - python-dotenv is in project requirements.
    dotenv_values = None

router = APIRouter()
logger = get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT_DIR / ".env"
SUBSCRIPTION_SCRIPT = ROOT_DIR / "cloudflare" / "subscriptions" / "scripts" / "wrangler-env.sh"
SUPPORTED_NOTIFICATION_LOCALES = ["en", "zh", "ja", "ko", "es", "fr", "de", "pt"]
WORKER_NETWORK_ATTEMPTS = 3
WORKER_RETRY_DELAY_SECONDS = 0.35
_active_notification_sends: set[str] = set()


class NotificationCreateRequest(BaseModel):
    markdown: str = Field(..., min_length=1, max_length=50000)
    subject: Optional[str] = Field(default=None, max_length=200)
    source_locale: str = Field(default="zh")
    target_locales: list[str] = Field(default_factory=list)
    list_codes: list[str] = Field(default_factory=list)
    start_sending: bool = True
    batch_size: int = Field(default=20, ge=1, le=100)
    max_recipients: int = Field(default=10000, ge=1, le=50000)


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
        raw = ""
        for attempt in range(1, WORKER_NETWORK_ATTEMPTS + 1):
            try:
                with urlrequest.urlopen(req, timeout=timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                break
            except urlerror.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") or str(exc)
                raise HTTPException(exc.code, detail) from exc
            except (urlerror.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
                if attempt >= WORKER_NETWORK_ATTEMPTS:
                    raise HTTPException(
                        502,
                        f"Subscription Worker is unreachable after {attempt} attempts: {exc}",
                    ) from exc
                logger.warning(
                    f"Subscription Worker request failed "
                    f"({attempt}/{WORKER_NETWORK_ATTEMPTS}) for {method} {path}: {exc}"
                )
                time.sleep(WORKER_RETRY_DELAY_SECONDS * attempt)

        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(502, "Subscription Worker returned invalid JSON.") from exc
        return data if isinstance(data, dict) else {"data": data}

    return await asyncio.to_thread(send)


def _normalize_locale(value: str | None, fallback: str = "en") -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in SUPPORTED_NOTIFICATION_LOCALES:
        return normalized
    base = normalized.split("-", 1)[0]
    if base in SUPPORTED_NOTIFICATION_LOCALES:
        return base
    return fallback


def _normalize_locale_list(values: list[str] | None, fallback: list[str] | None = None) -> list[str]:
    source = values or fallback or SUPPORTED_NOTIFICATION_LOCALES
    seen: set[str] = set()
    result: list[str] = []
    for item in source:
        locale = _normalize_locale(str(item), "")
        if locale and locale not in seen:
            seen.add(locale)
            result.append(locale)
    return result or ["en"]


def _notification_subject(markdown: str, subject: str | None = None) -> str:
    cleaned = (subject or "").strip()
    if cleaned:
        return re.sub(r"\s+", " ", cleaned)[:200]
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return re.sub(r"\s+", " ", match.group(1).strip())[:200]
    for line in markdown.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return re.sub(r"\s+", " ", text)[:120]
    return "GIDS Update"


def _extract_json_object(text: str) -> str:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1).strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        return value[start : end + 1]
    return value


async def _chat_translation(payload: dict[str, Any]) -> dict[str, Any]:
    """Wait for worker-owned model work without selecting or calling a provider."""
    return await run_worker_operation("notification_translation", payload)


async def _translate_notification_contents(
    *,
    subject: str,
    markdown: str,
    source_locale: str,
    target_locales: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    contents: dict[str, dict[str, str]] = {
        source_locale: {
            "subject": subject,
            "markdown": markdown,
        }
    }
    missing = [locale for locale in target_locales if locale != source_locale]
    if not missing:
        return contents, {"status": "skipped", "reason": "source_locale_only"}

    payload = {
        "subject": subject,
        "markdown": markdown,
        "source_locale": source_locale,
        "target_locales": missing,
    }
    try:
        result = await _chat_translation(payload)
        parsed = json.loads(_extract_json_object(result["text"]))
        translations = parsed.get("translations") if isinstance(parsed, dict) else None
        if not isinstance(translations, dict):
            raise RuntimeError("AI response did not include translations object.")

        for locale in missing:
            item = translations.get(locale)
            if not isinstance(item, dict):
                raise RuntimeError(f"AI response missing locale: {locale}")
            translated_subject = str(item.get("subject") or "").strip()
            translated_markdown = str(item.get("markdown") or "").strip()
            if not translated_subject or not translated_markdown:
                raise RuntimeError(f"AI response returned empty content for locale: {locale}")
            contents[locale] = {
                "subject": translated_subject[:200],
                "markdown": translated_markdown[:50000],
            }

        return contents, {
            "status": "translated",
            "model_route": result["model_route"],
            "locales": target_locales,
        }
    except Exception as exc:
        logger.warning(f"Notification translation failed: {exc}")
        raise HTTPException(502, f"AI translation failed: {exc}") from exc


async def _process_notification_campaign_background(campaign_id: str, batch_size: int = 20) -> None:
    if campaign_id in _active_notification_sends:
        return
    _active_notification_sends.add(campaign_id)
    try:
        for _ in range(5000):
            result = await _worker_request(
                f"/api/admin/notifications/{urlparse.quote(campaign_id)}/process",
                method="POST",
                payload={"batch_size": batch_size},
                timeout=180,
            )
            progress = result.get("progress") if isinstance(result, dict) else {}
            queued = int((progress or {}).get("queued") or 0) if isinstance(progress, dict) else 0
            processed = int(result.get("processed") or 0) if isinstance(result, dict) else 0
            if queued <= 0:
                break
            await asyncio.sleep(0.5 if processed > 0 else 2.0)
    except Exception as exc:
        logger.exception(f"Notification campaign background send failed for {campaign_id}: {exc}")
    finally:
        _active_notification_sends.discard(campaign_id)


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
    response: Response,
    status: Optional[str] = Query(default=None),
    list_code: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=250),
):
    offset = (page - 1) * page_size
    result = await _worker_request(
        "/api/admin/subscriptions",
        query={
            "status": status,
            "list_code": list_code,
            "q": q,
            "limit": page_size,
            "offset": offset,
        },
    )
    pagination = result.get("pagination") if isinstance(result, dict) else {}
    total = int((pagination or {}).get("total") or len(result.get("subscriptions") or []))
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return result.get("subscriptions") or []


@router.post("/subscriptions/audience")
async def subscription_audience(payload: dict[str, Any]):
    return await _worker_request("/api/admin/audience", method="POST", payload=payload)


@router.post("/subscriptions/maintenance")
async def subscription_maintenance():
    return await _worker_request("/api/admin/maintenance", method="POST", payload={})


@router.get("/notification-campaigns")
async def subscription_notifications(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    offset = (page - 1) * page_size
    result = await _worker_request(
        "/api/admin/notifications",
        query={"limit": page_size, "offset": offset},
    )
    pagination = result.get("pagination") if isinstance(result, dict) else {}
    total = int((pagination or {}).get("total") or len(result.get("campaigns") or []))
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return result.get("campaigns") or []


@router.get("/notification-campaigns/{campaign_id}")
async def subscription_notification_detail(
    campaign_id: str,
    delivery_limit: int = Query(default=100, ge=1, le=500),
):
    return await _worker_request(
        f"/api/admin/notifications/{urlparse.quote(campaign_id)}",
        query={"delivery_limit": delivery_limit},
    )


@router.post("/notification-campaigns", status_code=201)
async def create_subscription_notification(
    body: NotificationCreateRequest,
    background_tasks: BackgroundTasks,
):
    markdown = body.markdown.strip()
    subject = _notification_subject(markdown, body.subject)
    source_locale = _normalize_locale(body.source_locale, "zh")
    target_locales = _normalize_locale_list(body.target_locales, SUPPORTED_NOTIFICATION_LOCALES)
    if source_locale not in target_locales:
        target_locales.insert(0, source_locale)

    contents, ai_meta = await _translate_notification_contents(
        subject=subject,
        markdown=markdown,
        source_locale=source_locale,
        target_locales=target_locales,
    )

    worker_payload = {
        "subject": subject,
        "markdown": markdown,
        "source_locale": source_locale,
        "default_locale": source_locale,
        "target_locales": target_locales,
        "list_codes": body.list_codes,
        "contents": contents,
        "created_by": "dashboard",
        "max_recipients": body.max_recipients,
        "ai": ai_meta,
    }
    result = await _worker_request(
        "/api/admin/notifications",
        method="POST",
        payload=worker_payload,
        timeout=60,
    )

    campaign = result.get("campaign") if isinstance(result, dict) else None
    campaign_id = campaign.get("id") if isinstance(campaign, dict) else None
    if body.start_sending and campaign_id:
        background_tasks.add_task(_process_notification_campaign_background, str(campaign_id), body.batch_size)

    return {
        **result,
        "send_started": bool(body.start_sending and campaign_id),
    }


@router.post("/notification-campaigns/{campaign_id}/send", status_code=202)
async def start_subscription_notification_send(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    batch_size: int = Query(default=20, ge=1, le=100),
):
    background_tasks.add_task(_process_notification_campaign_background, campaign_id, batch_size)
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "send_started": True,
        "already_running": campaign_id in _active_notification_sends,
    }


@router.post("/notification-campaigns/{campaign_id}/process", status_code=202)
async def process_subscription_notification_batch(
    campaign_id: str,
    batch_size: int = Query(default=20, ge=1, le=100),
):
    return await _worker_request(
        f"/api/admin/notifications/{urlparse.quote(campaign_id)}/process",
        method="POST",
        payload={"batch_size": batch_size},
        timeout=180,
    )


@router.post("/subscriptions/sync-options")
async def subscription_sync_options():
    if not SUBSCRIPTION_SCRIPT.exists():
        raise HTTPException(404, f"Subscription helper script not found: {SUBSCRIPTION_SCRIPT}")

    target_environment = _env_value("SUBSCRIPTIONS__REMOTE_ENVIRONMENT").lower()
    if target_environment not in {"staging", "production"}:
        raise HTTPException(
            409,
            "Set SUBSCRIPTIONS__REMOTE_ENVIRONMENT to staging or production before syncing options.",
        )
    if _env_value("SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC") != target_environment:
        raise HTTPException(
            409,
            "SUBSCRIPTIONS__ALLOW_REMOTE_OPTION_SYNC must exactly match the target environment.",
        )

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
        target_environment,
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
