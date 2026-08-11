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

from anthropic import AsyncAnthropic
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.ai.model_center import (
    extract_retry_after_seconds,
    get_active_model_routes,
    is_model_unavailable_error,
    is_rate_limit_error,
    mark_route_rate_limited,
    mark_route_unavailable,
)
from src.core import get_logger
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
LOCALE_NAMES = {
    "en": "English",
    "zh": "Simplified Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}
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


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _openai_response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            return _content_to_text(getattr(message, "content", None))
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return _content_to_text(message.get("content"))
                text = first.get("text")
                if isinstance(text, str):
                    return text
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        return json.dumps(response, ensure_ascii=False)
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    return str(response)


def _looks_like_html(text: str) -> bool:
    normalized = text.lstrip().lower()
    return normalized.startswith("<!doctype html") or normalized.startswith("<html")


def _should_not_try_base_url_fallback(error: Exception) -> bool:
    if is_rate_limit_error(error):
        return True
    status_code = getattr(error, "status_code", None)
    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        normalized_status = None
    return normalized_status in {400, 401, 403, 429}


async def _chat_translation(route: dict[str, Any], payload: dict[str, Any]) -> str:
    style = str(route.get("api_style") or "openai_compatible").lower()
    target_labels = {
        locale: LOCALE_NAMES.get(locale, locale)
        for locale in payload["target_locales"]
    }
    system_prompt = (
        "You translate administrative product-update emails for GIDS. "
        "Preserve Markdown structure, links, numbers, code blocks, disease names, and dates. "
        "Do not add facts, disclaimers, or sections. Return only valid JSON."
    )
    user_content = json.dumps(
        {
            "source_locale": payload["source_locale"],
            "target_locales": target_labels,
            "subject": payload["subject"],
            "markdown": payload["markdown"],
            "required_schema": {
                "translations": {
                    "<locale>": {
                        "subject": "translated subject",
                        "markdown": "translated markdown",
                    }
                }
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    configured_max_tokens = route.get("max_tokens")
    try:
        max_tokens = int(configured_max_tokens) if configured_max_tokens is not None else 5000
    except (TypeError, ValueError):
        max_tokens = 5000
    max_tokens = max(1200, min(max_tokens, 8000))
    temperature = float(route.get("temperature") if route.get("temperature") is not None else 0.1)

    if style == "anthropic":
        client = AsyncAnthropic(api_key=route.get("api_key"))
        response = await client.messages.create(
            model=str(route.get("model_name") or ""),
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return "\n".join(
            str(block.text)
            for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        )

    base_url = str(route.get("base_url") or "").rstrip("/")
    base_urls: list[str | None] = [base_url or None]
    if base_url and not base_url.endswith("/v1"):
        base_urls.append(f"{base_url}/v1")

    last_error: Exception | None = None
    for candidate_base_url in base_urls:
        try:
            client = AsyncOpenAI(
                api_key=route.get("api_key"),
                base_url=candidate_base_url,
                default_headers=(route.get("extra_headers") or None),
            )
            response = await client.chat.completions.create(
                model=str(route.get("model_name") or ""),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = _openai_response_text(response)
            if _looks_like_html(text):
                raise RuntimeError(
                    "Model route returned an HTML page instead of a chat completion. "
                    "Check the provider base_url; OpenAI-compatible gateways usually require /v1."
                )
            return text
        except Exception as exc:
            last_error = exc
            if _should_not_try_base_url_fallback(exc):
                raise
            continue

    if last_error:
        raise last_error
    raise RuntimeError("AI translation returned no usable response.")


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

    routes = await get_active_model_routes()
    if not routes:
        raise HTTPException(
            400,
            "No active AI model route is available. Configure and enable a model before sending translated notifications.",
        )

    errors: list[str] = []
    payload = {
        "subject": subject,
        "markdown": markdown,
        "source_locale": source_locale,
        "target_locales": missing,
    }
    for route in routes:
        route_label = f"{route.get('provider_key')}/{route.get('model_name')}"
        try:
            text = await _chat_translation(route, payload)
            parsed = json.loads(_extract_json_object(text))
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
                "model_route": {
                    "model_id": route.get("model_id"),
                    "model_key": route.get("model_key"),
                    "model_name": route.get("model_name"),
                    "provider_key": route.get("provider_key"),
                    "provider_name": route.get("provider_name"),
                },
                "locales": target_locales,
            }
        except Exception as exc:
            message = str(exc)
            errors.append(f"{route_label}: {message}")
            if is_rate_limit_error(exc):
                await mark_route_rate_limited(route, message, extract_retry_after_seconds(exc))
            elif is_model_unavailable_error(exc):
                await mark_route_unavailable(route, message)
            logger.warning(f"Notification translation failed for {route_label}: {message}")

    raise HTTPException(502, f"AI translation failed for all model routes: {' | '.join(errors[-3:])}")


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
